"""Both surfaces read the same selector, and answer the same.

`UseCasePathMiddleware` is mounted before every route and puts ``/uc/<slug>`` into the scope for
whatever comes after it; ``resolve_use_case`` reads header-then-path, and the Gemini routes have
always used it. The KIRA surface rewrote the header half by hand and never looked at the path — so
the prefix worked on one surface and was **invisible** on the other.

Measured on 2026-08-13 against the running stack, one token holding two memberships:

    GET /uc/kundenservice/v1beta/…                200
    GET /uc/kundenservice/kira/api/external/…     403

and the refusal read *"send the `X-AIRA-Use-Case` header"* — so the prefix was not rejected, it was
never seen. It is the surface where the prefix matters most: a migrating client is often something
whose base URL is configurable and whose headers are not, which is the case the prefix exists for.

**The tests are written as a pair on purpose.** Each case is asserted on *both* surfaces, because
the property is not "KIRA honours the prefix" but "the two agree about what a selector is". A test
of one surface alone is how the two came to differ.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from aira_gateway.app import create_app
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings
from aira_gateway.db.models import ModelRead

KIRA = "/kira/api/external/chat"
GEMINI = "/v1beta/models/mock-1:generateContent"
TOKEN = {"authorization": "Bearer tok"}

_KIRA_BODY = {"request": {"parts": [{"text": "hi"}]}, "model_id": 1004}
_GEMINI_BODY = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}


class _OidcStub:
    """A caller in whichever use cases the case needs, and in nothing else."""

    def __init__(self, use_cases: tuple[str, ...]) -> None:
        self._use_cases = use_cases

    def validate(self, token: str) -> Principal | None:
        if token != "tok":
            return None
        return Principal("oidc-user", "oidc", use_cases=self._use_cases)


async def _client(*use_cases: str, require_use_case: bool = False) -> TestClient:
    app = create_app(
        GatewaySettings(auth_required=True, log_queue_size=0, require_use_case=require_use_case)
    )
    app.state.oidc_validator = _OidcStub(use_cases)
    client = TestClient(app)
    client.__enter__()
    async with app.state.db_sessionmaker() as session:
        session.add(
            ModelRead(
                model="mock-1",
                numeric_id=1004,
                capabilities=["generate", "embed"],
                publisher="google",
            )
        )
        await session.commit()
    return client


def _both(client: TestClient, prefix: str, headers: dict[str, str]) -> dict[str, Any]:
    """The same logical request through each surface, as `{surface: status}`."""
    return {
        "kira": client.post(f"{prefix}{KIRA}", json=_KIRA_BODY, headers=headers).status_code,
        "gemini": client.post(f"{prefix}{GEMINI}", json=_GEMINI_BODY, headers=headers).status_code,
    }


async def test_the_path_prefix_selects_a_use_case_on_both_surfaces() -> None:
    """The defect, stated as the property it broke. A caller in two use cases has to name one, and
    the prefix is the way to do it when the client's headers are not yours to set."""
    client = await _client("demo-uc", "other-uc")
    with client:
        assert _both(client, "/uc/demo-uc", TOKEN) == {"kira": 200, "gemini": 200}


async def test_the_prefix_naming_a_use_case_the_caller_is_not_in_is_refused_on_both() -> None:
    """A selector chooses among what a caller already reaches and never adds to it. Refused rather
    than ignored — an ignored selector would attribute the traffic to the wrong budget."""
    client = await _client("demo-uc")
    with client:
        assert _both(client, "/uc/somebody-else", TOKEN) == {"kira": 403, "gemini": 403}


async def test_a_caller_in_several_use_cases_and_no_selector_is_refused_on_both() -> None:
    """A guess would bill somebody else's budget; an "unattributed" bucket would be a hole in every
    control at once. Both surfaces refuse, and each says so in its own envelope."""
    client = await _client("demo-uc", "other-uc", require_use_case=True)
    with client:
        answers = _both(client, "", TOKEN)
    assert answers["kira"] == 403
    assert answers["gemini"] in (400, 403)


async def test_the_refusal_names_the_prefix_as_well_as_the_header() -> None:
    """The message is the fix. A caller who *cannot* set a header must not be told to set one —
    which is what it said, on the surface where that caller is most likely to be."""
    client = await _client("demo-uc", "other-uc")
    with client:
        response = client.post(KIRA, json=_KIRA_BODY, headers=TOKEN)

    assert response.status_code == 403
    message = response.json()["message"]
    assert "/uc/<use-case>" in message, message
    assert "X-AIRA-Use-Case" in message, message


async def test_the_header_wins_over_the_prefix_on_both_surfaces() -> None:
    """Documented precedence, and it now comes from one function rather than from two readings that
    happened to agree. The prefix names a use case the caller is not in and the header names one
    they are: served means the header was read."""
    client = await _client("demo-uc")
    with client:
        answers = _both(client, "/uc/somebody-else", {**TOKEN, "x-aira-use-case": "demo-uc"})

    assert answers == {"kira": 200, "gemini": 200}


async def test_a_prefix_that_is_not_a_slug_is_refused_rather_than_carried() -> None:
    """Client input that would otherwise reach the audit log, the read-model lookups and the trace
    attributes (`ADR-0007`). The KIRA surface validated the header and never saw the path, so the
    same string was bounded through one door and not the other."""
    client = await _client("demo-uc")
    with client:
        response = client.post(f"/uc/NOT A SLUG{KIRA}", json=_KIRA_BODY, headers=TOKEN)

    assert response.status_code in (400, 403), response.text
    assert response.status_code != 200


async def test_a_bound_api_key_still_needs_no_selector() -> None:
    """The common case must not have changed. A key belongs to one use case and carries it, so the
    prefix is not something a migrating client is now obliged to add."""
    client = await _client()
    with client:
        response = client.post(KIRA, json=_KIRA_BODY, headers=TOKEN)

    # No membership, no selector, and `require_use_case` off: served, exactly as before.
    assert response.status_code == 200, response.text
