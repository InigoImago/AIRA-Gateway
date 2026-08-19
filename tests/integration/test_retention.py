"""Retention against the live database (FRD-404).

The unit tests run on SQLite. This one runs on Postgres, where the JSON semantics that made the
pruner rewrite the same rows forever actually live, and where the migration's index has to exist.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from aira_gateway.db.base import build_sessionmaker
from aira_gateway.retention import RetentionService

pytestmark = pytest.mark.integration


async def _seed(engine: AsyncEngine, subject: str, use_case: str, age_days: int) -> str:
    row_id = f"itest-{uuid.uuid4().hex[:12]}"
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO request_logs (id, created_at, subject, auth_method, use_case, api,"
                " operation, model, status, prompt_tokens, completion_tokens, total_tokens,"
                " cost_nanos, request_payload, response_payload)"
                " VALUES (:id, :ts, :subject, 'api_key', :use_case, 'gemini', 'generateContent',"
                " 'mock-1', 200, 10, 20, 30, 85000, CAST(:req AS json), CAST(:res AS json))"
            ),
            {
                "id": row_id,
                "ts": datetime.now(UTC) - timedelta(days=age_days),
                "subject": subject,
                "use_case": use_case,
                "req": '{"contents": [{"parts": [{"text": "personal data"}]}]}',
                "res": '{"text": "answer"}',
            },
        )
    return row_id


async def test_the_pruner_clears_old_payloads_and_keeps_the_accounting(engine: AsyncEngine) -> None:
    subject = f"itest-{uuid.uuid4().hex[:8]}"
    slug = f"itest-{uuid.uuid4().hex[:8]}"
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO use_cases (slug, name, description, processing_notes, retention_days)"
                " VALUES (:slug, 'Retention probe', '', '', 7)"
            ),
            {"slug": slug},
        )
    old = await _seed(engine, subject, slug, age_days=30)
    fresh = await _seed(engine, subject, slug, age_days=1)

    try:
        service = RetentionService(build_sessionmaker(engine))
        first = await service.prune()
        assert first.payloads_cleared >= 1

        async with engine.connect() as connection:
            rows = dict(
                (
                    await connection.execute(
                        text(
                            "SELECT id, (request_payload IS NOT NULL) FROM request_logs"
                            " WHERE subject = :subject"
                        ),
                        {"subject": subject},
                    )
                ).all()
            )
        assert rows[old] is False, "the old payload should be gone"
        assert rows[fresh] is True, "the fresh payload should still be there"

        # The metadata is what the spend reporting reads — it must survive.
        async with engine.connect() as connection:
            kept = (
                await connection.execute(
                    text("SELECT total_tokens, cost_nanos FROM request_logs WHERE id = :id"),
                    {"id": old},
                )
            ).first()
        assert kept is not None and kept.total_tokens == 30 and kept.cost_nanos == 85000

        # On Postgres, an absent payload must be SQL NULL rather than the JSON value null,
        # otherwise the next run would rewrite the same rows again.
        async with engine.connect() as connection:
            again = await RetentionService(build_sessionmaker(engine)).prune()
        assert again.payloads_cleared == 0
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM request_logs WHERE subject = :subject"), {"subject": subject}
            )
            await connection.execute(
                text("DELETE FROM use_cases WHERE slug = :slug"), {"slug": slug}
            )


async def test_the_pruner_is_index_backed(engine: AsyncEngine) -> None:
    """The pruner filters on `(use_case, created_at)`, and an index has to cover both.

    Without one it is a sequential scan over a table that only grows — one row per request, kept
    long past every payload it carried.

    **By shape, not by name.** This used to assert that `ix_request_logs_use_case_created_at`
    exists, and on 2026-08-19 that index was dropped as redundant: `0033` had added
    `ix_request_logs_use_case_page` on the same leading columns plus `id`, and left the older one
    to be maintained on every insert. The property was intact and the check failed, because it was
    written about a spelling.

    **And not by asking the planner either**, which was the next thing tried and is worse: with a
    thousand rows the whole table fits in a few pages, so Postgres picks the single-column
    `created_at` index and filters on `use_case` — the cheapest plan, and a correct one. An
    `EXPLAIN` assertion here would measure the size of the demo database. What was measured by
    hand, with the single-column index dropped inside a rolled-back transaction, is that the
    surviving index does serve the predicate with **both** columns as index conditions:

        Index Scan using ix_request_logs_use_case_page
          Index Cond: use_case = … AND created_at < …

    So the structural question is the right one, and it is the one that stays true at any size:
    is there an index whose first two columns are `use_case` then `created_at`?
    """
    async with engine.connect() as connection:
        definitions = [
            row[0]
            for row in (
                await connection.execute(
                    text(
                        "SELECT indexdef FROM pg_indexes WHERE tablename = 'request_logs'"
                        " AND indexdef LIKE '%use_case%'"
                    )
                )
            ).all()
        ]

    assert definitions, "no index on request_logs mentions use_case at all"
    covering = [
        definition
        for definition in definitions
        if re.search(r"\(use_case[^,)]*,\s*created_at", definition)
    ]
    assert covering, (
        "no index leads with `(use_case, created_at)`, so the pruner bounds one column and filters "
        "the rest of the table by the other:\n  " + "\n  ".join(definitions)
    )
