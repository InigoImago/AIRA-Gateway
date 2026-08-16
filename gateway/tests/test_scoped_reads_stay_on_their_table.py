"""A restriction is written over the table it restricts (2026-08-15).

`/v1beta/traces` and `/v1beta/anomalies` both narrow their result for a caller whose use case shows
its members only their own requests (`FRD-505` FR-4). The second was the first one's condition,
pasted onto a different `select`:

    stmt = select(AnomalyEvent)                                    # over anomaly_events
    ...
    stmt = stmt.where(or_(RequestLog.use_case.notin_(restricted),  # over request_logs
                          RequestLog.subject == principal.subject))

SQLAlchemy resolves the foreign columns by adding their table to the FROM clause with **no join
predicate**, so the statement rendered as ``FROM anomaly_events, request_logs``. Two failures in
one line, and they point in opposite directions:

- **it fails open.** The condition asks a question about *unrelated rows*, so as long as one
  request log anywhere satisfies it, every finding passes — including the ones naming other
  people. The endpoint next door hides those correctly, which is what made it invisible.
- **it is a cartesian product.** One page of fifty costs *findings × request logs* rows, on the
  largest table this system has, reachable by any console user who can see the security screen.

So two guards. The behavioural one asserts the restriction; the structural one asserts that **no**
scoped read in `reporting.py` names a table it is not selecting from — because this is a defect
that a green suite cannot see and a reviewer only finds by rendering the SQL.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_gateway.api.reporting import _about_this_caller
from aira_gateway.app import create_app
from aira_gateway.auth.keys import generate_api_key
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings
from aira_gateway.db.models import AnomalyEvent, ApiKey, RequestLog, UseCaseRead

SOURCE = Path(__file__).resolve().parents[1] / "src" / "aira_gateway" / "api" / "reporting.py"


# == the statement ==============================================================================


def test_the_restriction_selects_from_one_table() -> None:
    """The measurement that found it, kept as the test. `get_final_froms()` is what a reviewer had
    to run by hand; run here it is the difference between a page and a cross join."""
    caller = Principal(subject="sub-1", method="oidc", username="ada", credential="console")
    stmt = select(AnomalyEvent).where(_about_this_caller(["uc-a"], caller))

    tables = {table.name for table in stmt.get_final_froms()}

    assert tables == {"anomaly_events"}, (
        f"the findings query reads from {sorted(tables)}. A second table with no join predicate is "
        "a cartesian product, and a condition over it is a question about rows this statement is "
        "not about."
    )


def test_the_restriction_keeps_what_is_about_this_caller() -> None:
    """Translated rather than copied, because a finding is not a request.

    What the restriction withholds is **other people's activity**, and a finding says who it is
    about in two columns. A `use_case` finding names nobody: withholding it would blind a use
    case's own members to its health while hiding nothing the rule is for.
    """
    caller = Principal(subject="sub-1", method="oidc", username="ada", credential="console")
    rendered = str(select(AnomalyEvent).where(_about_this_caller(["uc-a"], caller)))

    assert "anomaly_events.target =" in rendered
    assert "anomaly_events.target_value =" in rendered
    assert "request_logs" not in rendered


def test_a_caller_with_no_credential_still_sees_its_own_and_the_use_cases_own() -> None:
    """An API key has a prefix and an OIDC token may have no client id at all. Neither absence may
    become a condition that matches everything or nothing."""
    nameless = Principal(subject="sub-2", method="oidc")
    rendered = str(select(AnomalyEvent).where(_about_this_caller(["uc-a"], nameless)))

    assert "request_logs" not in rendered
    assert rendered.count("anomaly_events.target =") == 2, "use_case and subject, not credential"


# == the endpoint ===============================================================================


def _app():  # noqa: ANN201
    """Authentication **on**, because the restriction only exists for a caller who has a use case.

    The demo principal belongs to nothing, so `restricted_use_cases` returns an empty list for it
    and the property under test is never reached — a setup that never gets to the path it is named
    after, which `CLAUDE.md` names as one of the two traps that have cost real defects here.
    """
    return create_app(GatewaySettings(auth_required=True, log_queue_size=0))


async def _bound_key(app, *, subject: str, slug: str) -> str:  # noqa: ANN001
    """A Management-issued key: one subject, bound to one use case (`FRD-205`)."""
    token, prefix, key_hash = generate_api_key()
    async with app.state.db_sessionmaker() as session:
        session.add(
            ApiKey(prefix=prefix, key_hash=key_hash, subject=subject, use_case=slug, is_active=True)
        )
        await session.commit()
    return token


async def _restricted_use_case(app, slug: str) -> None:  # noqa: ANN001
    async with app.state.db_sessionmaker() as session:
        session.add(UseCaseRead(slug=slug, name=slug, restrict_members_to_own_requests=True))
        await session.commit()


async def _finding(app, **fields: Any) -> None:  # noqa: ANN001
    defaults: dict[str, Any] = {
        "rule_id": 1,
        "rule_name": "refusals",
        "kind": "refusal_rate",
        "target": "subject",
        "observed": 90,
        "threshold": 50,
        "sample": 10,
        "window_minutes": 15,
        "created_at": datetime.now(UTC),
    }
    async with app.state.db_sessionmaker() as session:
        session.add(AnomalyEvent(**{**defaults, **fields}))
        await session.commit()


async def _request_log(app, **fields: Any) -> None:  # noqa: ANN001
    defaults: dict[str, Any] = {
        "subject": "somebody-else",
        "auth_method": "oidc",
        "use_case": "uc-a",
        "api": "gemini",
        "operation": "generateContent",
        "model": "mock-1",
        "status": 200,
        "created_at": datetime.now(UTC),
    }
    async with app.state.db_sessionmaker() as session:
        session.add(RequestLog(**{**defaults, **fields}))
        await session.commit()


async def test_a_restricted_member_is_not_shown_findings_about_a_colleague() -> None:
    """The property, end to end. The old condition let every one of these through as soon as a
    single unrelated request log matched — which on any real installation is always."""
    app = _app()
    with TestClient(app) as client:
        await _restricted_use_case(app, "uc-a")
        token = await _bound_key(app, subject="ada", slug="uc-a")
        # The row that used to satisfy the condition for everybody: it belongs to somebody else and
        # has nothing to do with any finding.
        await _request_log(app)
        await _finding(app, use_case="uc-a", target="subject", target_value="ada")
        await _finding(app, use_case="uc-a", target="subject", target_value="somebody-else")
        await _finding(app, use_case="uc-a", target="use_case", target_value="uc-a")

        events = client.get(
            "/v1beta/anomalies", headers={"Authorization": f"Bearer {token}"}
        ).json()["events"]

    seen = {(row["target"], row["target_value"]) for row in events}
    assert ("subject", "ada") in seen, "their own finding"
    assert ("use_case", "uc-a") in seen, "and the one that names nobody"
    assert ("subject", "somebody-else") not in seen, "a colleague's is withheld"


async def test_an_unrestricted_use_case_shows_everybodys() -> None:
    """Nothing may regress for the ordinary case: the restriction is a switch an administrator
    turns on, and off is the default and the behaviour that already existed."""
    app = _app()
    with TestClient(app) as client:
        async with app.state.db_sessionmaker() as session:
            session.add(UseCaseRead(slug="uc-open", name="uc-open"))
            await session.commit()
        token = await _bound_key(app, subject="ada", slug="uc-open")
        await _finding(app, use_case="uc-open", target="subject", target_value="somebody-else")

        events = client.get(
            "/v1beta/anomalies", headers={"Authorization": f"Bearer {token}"}
        ).json()["events"]

    assert [row["target_value"] for row in events] == ["somebody-else"]


# == the structural guard =======================================================================


def test_no_query_in_reporting_conditions_on_a_table_it_does_not_select() -> None:
    """A fifth scoped read cannot repeat this by being written next to the fourth.

    Deliberately crude — it compares the model names a `select(...)` mentions against the ones its
    `.where(...)` chain mentions, in the same function. A condition over a table the statement does
    not select from is either a cartesian product or a subquery somebody forgot to write, and this
    module has no subqueries.
    """
    tree = ast.parse(SOURCE.read_text())
    models = {"RequestLog", "AnomalyEvent", "PayloadAccess", "UseCaseRead"}
    offenders: list[str] = []

    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        selected: set[str] = set()
        conditioned: set[str] = set()
        for node in ast.walk(function):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name | ast.Attribute):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr
            if name not in {"select", "where"}:
                continue
            mentioned = {
                child.value.id
                for child in ast.walk(node)
                if isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name)
            } | {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}
            (selected if name == "select" else conditioned).update(mentioned & models)
        stray = conditioned - selected
        if selected and stray:
            offenders.append(
                f"{function.name}: selects {sorted(selected)}, conditions on {sorted(stray)}"
            )

    assert not offenders, (
        "a condition names a table the statement does not select from, which SQLAlchemy resolves "
        "by adding it to the FROM clause with no join predicate — a cartesian product, and a "
        "filter that asks about unrelated rows:\n  " + "\n  ".join(offenders)
    )
