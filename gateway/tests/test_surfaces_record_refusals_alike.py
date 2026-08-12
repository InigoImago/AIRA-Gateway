"""A refusal from an authenticated caller leaves a row — on **both** surfaces.

`FRD-122`'s rule is that the log records what was **asked**, not only what was served, and
`test_a_kira_request_is_audited_exactly_like_a_gemini_one` already compares the two surfaces row
for row. It compares a **served** request. A developer round on 2026-08-12 asked the same question
about the refused ones and found the two surfaces disagreeing:

    malformed JSON, valid credential    Gemini: a row      KIRA: nothing at all

The cause is the order of two steps that look unrelated. `_refused` records only when
`request.state.attribution` is set — deliberately, because a request refused *before* the
credential was judged has nobody to attribute to and writing one would let anyone put another
system's name in the audit trail (`FRD-122` §2). On the Gemini surface attribution is a
router-level dependency and is therefore always resolved before the route body runs. On KIRA it
was resolved *inside* the route, after the body was parsed — so anything the parse rejected fell
into the gap.

**Attribution never needed the body.** It reads the header and the principal. Putting it first
costs nothing and closes the difference.

Two surfaces answering one governance question differently is the shape this project keeps
finding — an empty membership list meaning "anything goes" on one of them, a kill switch guarded
by a visibility predicate on one plane. The lesson each time is the same: **the parity is the
property, so the parity is what a test has to assert.** Comparing them only where they succeed
compares them where they are least likely to differ.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.db.models import ModelRead, RequestLog

KIRA = "/kira/api/external/chat"
GEMINI = "/v1beta/models/mock-1:generateContent"


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app(
        GatewaySettings(auth_required=False, environment="local", demo_mode=True, log_queue_size=0)
    )
    with TestClient(app) as running:

        async def _seed() -> None:
            async with app.state.db_sessionmaker() as session:
                session.add(
                    ModelRead(
                        model="mock-1",
                        numeric_id=9001,
                        capabilities=["generate"],
                        approved=True,
                    )
                )
                await session.commit()

        running.portal.call(_seed)  # type: ignore[attr-defined]
        yield running


async def _rows(client: TestClient) -> list[Any]:
    app = client.app  # type: ignore[attr-defined]
    async with app.state.db_sessionmaker() as session:
        return list((await session.execute(select(RequestLog))).scalars())


@pytest.mark.parametrize(
    ("surface", "url"),
    [pytest.param("gemini", GEMINI, id="gemini"), pytest.param("kira", KIRA, id="kira")],
)
async def test_malformed_json_from_a_known_caller_is_recorded(
    client: TestClient, surface: str, url: str
) -> None:
    """The case that differed. Parametrised over both surfaces on purpose: a property that holds
    on one of them is not the property — and asserting it separately is how the two came to
    disagree in the first place."""
    response = client.post(url, content=b"{ not json")

    assert response.status_code in (400, 422), response.text
    rows = await _rows(client)

    assert len(rows) == 1, f"{surface}: a refusal left no trace"
    assert rows[0].api == surface
    assert rows[0].outcome == "invalid_request"


@pytest.mark.parametrize(
    ("surface", "url", "body"),
    [
        pytest.param("gemini", GEMINI, {"contents": []}, id="gemini"),
        pytest.param(
            "kira",
            KIRA,
            {"request": {"parts": [{"text": ""}]}, "model_id": 9001},
            id="kira",
        ),
    ],
)
async def test_an_empty_request_is_recorded_on_both_surfaces(
    client: TestClient, surface: str, url: str, body: dict[str, Any]
) -> None:
    """The second half of the same rule, with a body that parses and asks for nothing. It already
    held on both — kept so that a future change to one surface's ordering shows up as *two*
    failures rather than one, which is what makes a parity break legible."""
    response = client.post(url, json=body)

    assert response.status_code >= 400
    rows = await _rows(client)

    assert len(rows) == 1, f"{surface}: a refusal left no trace"
    assert rows[0].api == surface


async def test_an_unauthenticated_request_is_still_not_recorded() -> None:
    """The deliberate exception, asserted so the fix above cannot quietly widen into it.

    A request refused **before** the credential was judged has nobody to attribute to, and writing
    a row for it would let anyone put another system's name into the audit trail with one
    unauthenticated call — an unverifiable claim is not evidence (`FRD-122` §2). The repair was to
    resolve attribution earlier, *after* authentication; it must not become "record everything".
    """
    app = create_app(GatewaySettings(auth_required=True, environment="local", log_queue_size=0))
    with TestClient(app) as client:
        assert client.post(KIRA, content=b"{ not json").status_code == 401
        assert client.post(GEMINI, content=b"{ not json").status_code == 401

        async with app.state.db_sessionmaker() as session:
            rows = list((await session.execute(select(RequestLog))).scalars())

    assert rows == []
