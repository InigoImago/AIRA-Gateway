"""IT Security reads every figure, on every endpoint that shows one (2026-08-15).

`OVERSIGHT_ROLES` and `GOVERNANCE_ROLES` differ by exactly one role, and `reporting.visible_scope`
records the correction being made once:

> **`is_oversight`, not `is_governance` — corrected 2026-08-08.** […] Asking the narrower predicate
> meant the role whose job is investigating an incident saw an **empty** reporting screen and an
> **empty** trace list — not a refusal, which would at least have been a question, but nothing.

Two call sites were not carried with it, and both said "oversight" while asking "governance":

    api/usage.py         docstring: "Oversight reads; everybody else has to be a member"
    kira ki_usage        message:   "This endpoint needs … (report.read_all)."

IT Security is deliberately a member of nothing (`ADR-0007`), so the fallback in each case refused
them every time. A message naming one rule while the code applies another is the worse of the two
failures: the reader concludes their directory is wrong.

This file asks the same question of every endpoint that reports a figure, so a fifth one cannot be
written against the narrower predicate without an answer.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aira_gateway.app import create_app
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings

#: The per-use-case ladder, which decides while strict data mode is off (`FRD-627`).
pytestmark = pytest.mark.usefixtures("strict_data_off")

#: The three organisation-wide roles, and what each is entitled to see. `it-security` is the row
#: this file exists for; the other two are here so a fix that widened everything would fail.
GLOBAL_ADMIN = Principal(subject="root", method="oidc", roles=("global-admin",))
IT_SECURITY = Principal(subject="sec", method="oidc", roles=("it-security",))
IT_STEUERUNG = Principal(subject="gov", method="oidc", roles=("it-steuerung",))
#: A member of one use case, and of nothing else. Not an oversight role.
MEMBER = Principal(subject="ada", method="oidc", username="ada", use_cases=("uc-a",))

#: Every endpoint that shows a figure about somebody's consumption, and how to ask it.
FIGURES = {
    "reporting": "/v1beta/reporting",
    "traces": "/v1beta/traces",
    "anomalies": "/v1beta/anomalies",
    "usage": "/v1beta/usage/uc-a",
    "ki-usage": "/kira/api/external/ki-usage?startDatum=2026-08-01&endDatum=2026-08-31",
}


def _client(principal: Principal) -> TestClient:
    app = create_app(GatewaySettings(auth_required=True, log_queue_size=0))
    app.dependency_overrides[require_principal] = lambda: principal
    return TestClient(app)


@pytest.mark.parametrize("where", sorted(FIGURES))
@pytest.mark.parametrize(
    "principal", [GLOBAL_ADMIN, IT_SECURITY, IT_STEUERUNG], ids=["admin", "security", "steuerung"]
)
def test_an_oversight_role_is_never_refused_a_figure(principal: Principal, where: str) -> None:
    """PRD §154 gives all three every figure. The answer may be empty — an installation with no
    traffic has nothing to show — but it may not be a refusal."""
    with _client(principal) as client:
        response = client.get(FIGURES[where])

    assert response.status_code == 200, (
        f"{where} refused {principal.roles[0]} with {response.status_code}: {response.text}"
    )


@pytest.mark.parametrize("where", ["usage", "ki-usage"])
def test_a_member_is_still_held_to_their_own_use_case(where: str) -> None:
    """Widening to oversight must not widen to everybody. `usage` narrows to what a member may act
    on; `ki-usage` reports the whole installation and stays closed to them entirely."""
    with _client(MEMBER) as client:
        response = client.get(FIGURES[where])

    if where == "usage":
        assert response.status_code == 200, "their own use case"
    else:
        assert response.status_code == 403


def test_a_member_may_not_read_another_use_cases_usage() -> None:
    """The rule the widening had to leave alone: a selector never grants access."""
    with _client(MEMBER) as client:
        assert client.get("/v1beta/usage/uc-somebody-else").status_code == 403


def test_the_compatibility_surface_says_the_rule_it_applies() -> None:
    """The message named `oversight` while the check asked `governance`. Whichever way that is
    fixed, the two have to agree — a refusal that names a role the caller holds sends them to
    their directory administrator for a problem that is not there."""
    with _client(MEMBER) as client:
        refusal = client.get(FIGURES["ki-usage"]).json()

    assert refusal["code"] == "ADMIN_PERMISSION_REQUIRED"
    assert "report.read_all" in refusal["message"]


async def test_ki_usage_answers_once_there_is_traffic_to_report() -> None:
    """The figures above are asked of an empty installation, where every report is empty — and an
    endpoint that fails as soon as one request exists passes all of them.

    `/ki-usage` reports per user with model id `0` (the report does not cross-tabulate user and
    model). The response row reused the *request* validator for a model id, which starts at `1`, so
    the first row ever reported turned the answer into a 500 for an authorised caller.
    """
    from datetime import UTC, datetime

    from aira_gateway.db.models import RequestLog

    with _client(GLOBAL_ADMIN) as client:
        async with client.app.state.db_sessionmaker() as session:
            session.add(
                RequestLog(
                    subject="ada",
                    auth_method="oidc",
                    use_case="uc-a",
                    api="kira",
                    operation="chat",
                    model="mock-1",
                    status=200,
                    outcome="served",
                    prompt_tokens=11,
                    completion_tokens=7,
                    total_tokens=18,
                    created_at=datetime(2026, 8, 14, 12, 0, tzinfo=UTC),
                )
            )
            await session.commit()
        response = client.get(FIGURES["ki-usage"])

    assert response.status_code == 200, response.text
    assert response.json() == [
        {
            "user_id": "ada",
            "model_id": 0,
            "entry_count": 1,
            "token_input_sum": 11,
            "token_output_sum": 7,
        }
    ]
