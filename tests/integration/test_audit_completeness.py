"""The audit trail against the real stack (FRD-122).

The hermetic tests prove the route writes the row. What only shows up here is that the columns
exist in the **migrated** schema rather than only in the SQLAlchemy model — a distinction that has
bitten this project before, because the hermetic suite builds its schema with ``create_all`` and
never runs a migration. A column present in the model and missing from Alembic fails nowhere until
production.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .conftest import GATEWAY_URL

pytestmark = pytest.mark.integration

#: Everything `0012_audit_completeness` adds, plus the index names it creates.
_AUDIT_COLUMNS = {
    "credential",
    "requested_model",
    "model_selection",
    "outcome",
    "pipeline_decisions",
    "degraded",
}


async def test_the_migration_created_every_audit_column(engine: AsyncEngine) -> None:
    """The hermetic suite uses ``create_all``, so it would pass with an empty migration."""
    async with engine.connect() as connection:
        present = {
            row[0]
            for row in (
                await connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns"
                        " WHERE table_name = 'request_logs'"
                    )
                )
            ).all()
        }

    assert present >= _AUDIT_COLUMNS, (
        f"missing from the migrated schema: {_AUDIT_COLUMNS - present}"
    )


async def test_outcome_and_credential_are_indexed(engine: AsyncEngine) -> None:
    """Reporting groups by outcome and incident response filters by credential. Both scan a table
    retention deliberately keeps rows in, so both need an index — and a missing one is invisible
    until the installation is large enough for it to hurt."""
    async with engine.connect() as connection:
        definitions = " ".join(
            row[0]
            for row in (
                await connection.execute(
                    text("SELECT indexdef FROM pg_indexes WHERE tablename = 'request_logs'")
                )
            ).all()
        )

    assert "outcome" in definitions
    assert "credential" in definitions


async def test_a_refused_request_reaches_the_real_request_log(engine: AsyncEngine, fixture) -> None:
    """End to end: an unknown model is refused, and the refusal is in the table afterwards.

    ``model_not_found`` exercises exactly the path that produced no row at all before: a request
    that never reached an upstream.

    **It is sent with a use-case credential, and it used to be sent with a governance bearer.** A
    governance token attributes to no use case, so the request was refused at attribution with a
    `403` and the test skipped — on every deployment, saying "nothing for this test to assert".
    The guarantee it exists for (`FRD-128`: a request is recorded however it ended) was therefore
    unverified end to end for as long as the skip has been there. An API key resolves to a use
    case, the ghost model is refused at *routing*, and the row is written.
    """
    model = f"ghost-{uuid.uuid4().hex[:8]}"

    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client:
        response = await client.post(
            f"/v1beta/models/{model}:generateContent",
            headers=fixture.headers(),
            json={"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
        )
    assert response.status_code == 404, response.text

    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT outcome, requested_model, status FROM request_logs"
                    " WHERE requested_model = :model ORDER BY created_at DESC LIMIT 1"
                ),
                {"model": model},
            )
        ).first()

    assert row is not None, "a refused request left no trace in the audit trail"
    assert row[0] == "model_not_found"
    assert row[1] == model
    assert row[2] == 404


async def test_a_refusal_becomes_a_figure_in_the_report(governance_token: str, fixture) -> None:
    """The refusals have to be *countable*, not merely stored — a use case hitting a wall should
    be a number on a screen rather than a log search.

    The window is a day rather than all of history: the endpoint bounds a report to 366 days
    (`FRD-601`), which is the correct behaviour and worth exercising by staying inside it.
    """
    model = f"ghost-{uuid.uuid4().hex[:8]}"
    now = datetime.now(UTC)

    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client:
        # The refusal is made with a **use-case credential** — a governance bearer attributes to no
        # use case and is refused at attribution, which is what made this skip everywhere. The
        # report is then read with the governance token, which is who may read one.
        refused = await client.post(
            f"/v1beta/models/{model}:generateContent",
            headers=fixture.headers(),
            json={"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
        )
        assert refused.status_code == 404, refused.text

        response = await client.get(
            "/v1beta/reporting",
            params={
                "from": (now - timedelta(hours=1)).isoformat(),
                "to": (now + timedelta(hours=1)).isoformat(),
            },
            headers={"authorization": f"Bearer {governance_token}"},
        )
    assert response.status_code == 200, response.text

    report = response.json()
    by_outcome = {row["key"]: row["requests"] for row in report["by_outcome"]}
    assert by_outcome.get("model_not_found", 0) >= 1, (
        f"the refusal was stored but is not countable: {by_outcome}"
    )
