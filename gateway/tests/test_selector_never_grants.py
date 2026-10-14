"""A selector chooses among what a caller has; it never adds to it.

Written after a live round proved the opposite on one of the two surfaces. The KIRA surface asked
`if memberships and header not in memberships`, so an **empty** membership list meant "anything
goes" rather than "nothing": a caller belonging to no use case at all could send
`X-AIRA-Use-Case: somebody-elses`, get a real answer, and have the tokens billed to that use case's
budget and written into its audit trail. The Gemini surface refused the identical request.

The rule now lives in one function both surfaces call. These tests exist so it stays that way —
they are written against the **surfaces**, not against the function, because a shared rule that a
surface forgets to call is the same defect with an extra step.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aira_gateway.api.kira import errors
from aira_gateway.app import create_app
from aira_gateway.auth.dependencies import require_principal, use_case_refusal
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings

BODY = {"contents": [{"parts": [{"text": "hi"}]}]}
KIRA_BODY = {"model_id": 1, "request": {"parts": [{"text": "hi"}]}}


def _client(principal: Principal) -> TestClient:
    app = create_app(GatewaySettings(auth_required=True, test_database=True))
    app.dependency_overrides[require_principal] = lambda: principal
    return TestClient(app)


# ---- the rule itself ---------------------------------------------------------------------


def test_a_caller_who_belongs_to_nothing_is_refused_everything() -> None:
    """The correction. An empty membership list is "nothing", not "anything"."""
    nobody = Principal(subject="ada", method="oidc")

    assert use_case_refusal(nobody, "somebody-elses") is not None


def test_a_member_is_allowed_their_own() -> None:
    member = Principal(subject="ada", method="oidc", use_cases=("uc-a",))

    assert use_case_refusal(member, "uc-a") is None
    assert use_case_refusal(member, "uc-b") is not None


def test_a_bound_api_key_may_touch_only_what_it_was_issued_for() -> None:
    key = Principal(subject="app", method="api_key", use_cases=("uc-a",))

    assert use_case_refusal(key, "uc-a") is None
    assert use_case_refusal(key, "uc-b") is not None


def test_an_unbound_api_key_stays_unrestricted() -> None:
    """The deliberate exception, and the reason this is not simply "empty means refuse".

    The CLI break-glass key is minted by an operator with database access, for the moment when the
    control plane is unavailable. Removing that would have been a functional regression dressed up
    as a fix.
    """
    breakglass = Principal(subject="operator", method="api_key")

    assert use_case_refusal(breakglass, "any-use-case") is None


# ---- both surfaces, side by side ----------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "body", "header"),
    [
        ("/v1beta/models/mock-1:generateContent", BODY, "X-AIRA-Use-Case"),
        ("/kira/api/external/chat", KIRA_BODY, "X-AIRA-Use-Case"),
    ],
)
def test_neither_surface_lets_a_non_member_name_a_use_case(path, body, header) -> None:
    """The property, asserted per surface rather than on the shared function.

    A rule only one caller remembers to call is the defect this replaced.
    """
    nobody = Principal(subject="ada", method="oidc")

    with _client(nobody) as client:
        response = client.post(path, json=body, headers={header: "somebody-elses"})

    assert response.status_code == 403, response.text
    assert "somebody-elses" in response.text


def test_the_kira_surface_refuses_in_its_own_envelope() -> None:
    """Sharing the rule must not leak the other surface's error shape — a KIRA client parses
    `code`, and a Gemini-shaped body would be an outage for them."""
    nobody = Principal(subject="ada", method="oidc")

    with _client(nobody) as client:
        response = client.post(
            "/kira/api/external/chat", json=KIRA_BODY, headers={"X-AIRA-Use-Case": "other"}
        )

    assert response.status_code == 403
    assert response.json()["code"] == errors.STANDARD_USER_PERMISSION_REQUIRED


def test_the_gemini_path_selector_is_authorised_too() -> None:
    """`/uc/<slug>` is a third way to name one, and it was already right — asserted so a future
    change cannot make it the odd one out."""
    nobody = Principal(subject="ada", method="oidc")

    with _client(nobody) as client:
        response = client.post("/uc/other/v1beta/models/mock-1:generateContent", json=BODY)

    assert response.status_code == 403


async def test_a_member_still_gets_through_on_both_surfaces() -> None:
    """The other half of "keep the functionality": the fix must refuse the right people only."""
    from aira_gateway.db.models import ModelRead, UseCaseRead

    member = Principal(subject="ada", method="oidc", use_cases=("uc-a",))

    with _client(member) as client:
        async with client.app.state.db_sessionmaker() as session:  # type: ignore[attr-defined]
            session.add(ModelRead(model="mock-1", numeric_id=1, capabilities=["generate"]))
            # Known to the gateway: a use case it has never heard of serves nothing.
            session.add(UseCaseRead(slug="uc-a", name="uc-a"))
            await session.commit()
        gemini = client.post(
            "/v1beta/models/mock-1:generateContent",
            json=BODY,
            headers={"X-AIRA-Use-Case": "uc-a"},
        )
        kira = client.post(
            "/kira/api/external/chat", json=KIRA_BODY, headers={"X-AIRA-Use-Case": "uc-a"}
        )

    assert gemini.status_code == 200, gemini.text
    assert kira.status_code == 200, kira.text


@pytest.mark.parametrize("method", ["mtls", "service-account", ""])
def test_a_credential_type_this_gateway_does_not_know_is_admitted_nowhere(method: str) -> None:
    """The decision named two cases to refuse and admitted everything else, so a fourth credential
    type added later would have reached every use case by default — refusal by exception, the
    shape that fails open."""
    from aira_gateway.auth.dependencies import use_case_refusal
    from aira_gateway.auth.principal import Principal

    assert use_case_refusal(Principal(subject="x", method=method), "uc-a") is not None


def test_the_three_known_credential_types_keep_their_rules() -> None:
    from aira_gateway.auth.dependencies import use_case_refusal
    from aira_gateway.auth.principal import Principal

    assert use_case_refusal(Principal(subject="demo", method="demo"), "uc-a") is None
    assert use_case_refusal(Principal(subject="op", method="api_key"), "uc-a") is None
    member = Principal(subject="ada", method="oidc", use_cases=("uc-a",))
    assert use_case_refusal(member, "uc-a") is None
