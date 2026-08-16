"""One request asks the read-model a bounded number of times (2026-08-15).

**Measured, not reasoned about.** A served `:generateContent` opened **15** database sessions
against an *empty* read-model — no budget, no rate limit, no pipeline configured — and five of them
were `ModelCatalog.declaration()` for the same model: the pipeline's `declaration_of`, the routed
model's provider, `check_declaration`, the reservation's `estimate`, `provenance`, and once per
candidate inside `requirements_for`. Three more read the same `use_cases` row.

Each of those is an `async with sessionmaker()` — a connection checked out of the pool for a row
that had already been read on the same request. The comment in `ratelimit/service.py` names "six or
seven database round trips" as the known cost; the real figure was more than twice that, and the
duplication is the part no amount of tuning elsewhere removes.

The fix is a memo with a **request's** lifetime (`ModelCatalog.per_request`,
`serving.use_case_record`) — not the application's, deliberately: the catalog is a runtime
authority, and a model that stayed approved after a Global Administrator revoked it is the
opposite of what `FRD-307` is for.

This file is the measurement kept. A ceiling rather than an exact number, because the point is
that the cost does not grow with the number of readers — but a **tight** ceiling, because one that
nothing can reach is a test that watches nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import sqlalchemy.ext.asyncio as sa_asyncio
from fastapi.testclient import TestClient

from aira_gateway.app import create_app
from aira_gateway.catalog import ModelCatalog
from aira_gateway.config import GatewaySettings
from aira_gateway.db.models import ModelRead, UseCaseRead

BODY = {"contents": [{"role": "user", "parts": [{"text": "hallo"}]}]}
HEADERS = {"X-AIRA-Use-Case": "uc-a"}

#: What one served generation may cost in database sessions. Fifteen before the memo, ten after;
#: this leaves room for a control to grow one and fails on the shape that put five identical reads
#: on one request.
MAX_SESSIONS = 12

#: How many times the **database** may be asked about one model on one request. One: every reader
#: after the first is answered from the memo.
MAX_CATALOG_READS = 1

#: And the use-case row. **Two**, and both are named, because a ceiling nobody can account for is
#: a number somebody raises the next time it fails:
#:
#:   1. the request path — the release (`FRD-308`), the tool switch (`FRD-131`) and both halves of
#:      prompt caching (`FRD-133`) share one read through `serving.use_case_record`;
#:   2. the audit writer, which asks whether this use case stores payloads at all (`FRD-404`).
#:
#: The second is not a duplicate: the writer runs off the request path with its own session by
#: design (`FRD-405` §4.4), and it is only in the same task here because `log_queue_size=0` makes
#: it write inline. Folding it into the request's memo would tie the two lifetimes together for a
#: single query.
MAX_USE_CASE_READS = 2


@contextmanager
def _counting(monkeypatch: Any) -> Iterator[dict[str, int]]:
    """Count sessions opened, catalog rows read and use-case rows read."""
    counts = {"sessions": 0, "declaration": 0, "use_case": 0}

    original_session = sa_asyncio.AsyncSession.__init__

    def counted_session(self: Any, *args: Any, **kwargs: Any) -> None:
        counts["sessions"] += 1
        original_session(self, *args, **kwargs)

    original_declaration = ModelCatalog.declaration

    async def counted_declaration(self: ModelCatalog, model: str) -> Any:
        counts["declaration"] += 1
        return await original_declaration(self, model)

    original_get = sa_asyncio.AsyncSession.get

    async def counted_get(self: Any, entity: Any, ident: Any, *args: Any, **kwargs: Any) -> Any:
        if entity is UseCaseRead:
            counts["use_case"] += 1
        return await original_get(self, entity, ident, *args, **kwargs)

    monkeypatch.setattr(sa_asyncio.AsyncSession, "__init__", counted_session)
    # On the class, so the memoised subclass's calls **through** to the source are what is counted
    # and its own answers are not. Patching the subclass would count the cache and prove nothing.
    monkeypatch.setattr(ModelCatalog, "declaration", counted_declaration)
    monkeypatch.setattr(sa_asyncio.AsyncSession, "get", counted_get)
    yield counts


async def test_a_served_request_reads_each_row_once(monkeypatch: Any) -> None:
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    with TestClient(app) as client:
        async with app.state.db_sessionmaker() as session:
            session.add(ModelRead(model="mock-1", capabilities=["generate"], approved=True))
            session.add(UseCaseRead(slug="uc-a", name="uc-a", allowed_models=["mock-1"]))
            await session.commit()

        with _counting(monkeypatch) as counts:
            response = client.post(
                "/v1beta/models/mock-1:generateContent", json=BODY, headers=HEADERS
            )

    assert response.status_code == 200, response.text
    assert counts["declaration"] <= MAX_CATALOG_READS, (
        f"the catalog was read {counts['declaration']} times for one model on one request. Every "
        "reader after the first is meant to be answered from `ModelCatalog.per_request`."
    )
    assert counts["use_case"] <= MAX_USE_CASE_READS, (
        f"the use-case row was read {counts['use_case']} times. `serving.use_case_record` exists "
        "so the release, the tool switch and both halves of prompt caching share one read."
    )
    assert counts["sessions"] <= MAX_SESSIONS, (
        f"one request opened {counts['sessions']} database sessions. Each is a connection checked "
        "out of the pool, and this is the first thing that runs out under load."
    )


async def test_the_memo_does_not_outlive_the_request(monkeypatch: Any) -> None:
    """**A request's lifetime, not the application's**, and this is the half that matters.

    The catalog decides what a request may ask for, and configuration arrives over Kafka at any
    moment. An application-scoped cache would go on applying a declaration somebody had already
    replaced — for as long as the entry lived, invisibly. So a *second* request reads the row again
    and is judged by the new one.

    Asserted through a **consequence** rather than a counter: a cache that is quietly still warm
    would keep answering the old cap, and only an observable refusal proves it is not.
    """
    capped = {**BODY, "generationConfig": {"maxOutputTokens": 100}}
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    with TestClient(app) as client:
        async with app.state.db_sessionmaker() as session:
            session.add(
                ModelRead(model="mock-1", capabilities=["generate"], max_output_tokens=1000)
            )
            await session.commit()

        assert client.post("/v1beta/models/mock-1:generateContent", json=capped).status_code == 200

        # Lowered between the two requests, exactly as a `model.upserted` event would.
        async with app.state.db_sessionmaker() as session:
            record = await session.get(ModelRead, "mock-1")
            assert record is not None
            record.max_output_tokens = 10
            await session.commit()

        with _counting(monkeypatch) as counts:
            refused = client.post("/v1beta/models/mock-1:generateContent", json=capped)

    assert counts["declaration"] >= 1, "the second request read the catalog again"
    assert refused.status_code == 400, "the new cap decided, so the memo did not outlive the first"
    assert "10" in refused.json()["error"]["message"]
