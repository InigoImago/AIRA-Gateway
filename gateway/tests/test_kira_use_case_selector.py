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

And both now agree that a model call **names** its use case, whoever makes it (`FRD-149`): the
KIRA surface also took a caller's only membership as the selector, which made a client's requests
mean something else the day its caller was given a second use case.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from aira_common.apikeys import generate_api_key
from aira_gateway.app import create_app
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings
from aira_gateway.db.models import ApiKey, BudgetRead, ModelRead, UseCaseRead

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
        # Known to the gateway: a use case it has never heard of serves nothing.
        session.add_all(UseCaseRead(slug=slug, name=slug) for slug in use_cases)
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


async def test_a_caller_in_one_use_case_names_it_too_on_both() -> None:
    """**The shortcut is gone** (`FRD-149`): a caller whose only membership the KIRA surface used to
    pick is refused like everybody else when they name none, and served once they do — the same
    answer on both surfaces, so a client means one thing whatever its caller reaches."""
    client = await _client("demo-uc", require_use_case=True)
    with client:
        unnamed = _both(client, "", TOKEN)
        named = _both(client, "/uc/demo-uc", TOKEN)
        kira = client.post(KIRA, json=_KIRA_BODY, headers=TOKEN).json()

    assert unnamed == {"kira": 403, "gemini": 400}
    assert named == {"kira": 200, "gemini": 200}
    assert kira["code"] == "STANDARD_USER_PERMISSION_REQUIRED"
    # What to name is in the refusal: the one use case this caller reaches.
    assert "demo-uc" in kira["message"], kira["message"]


async def test_the_refusal_names_the_prefix_as_well_as_the_header() -> None:
    """The message is the fix. A caller who *cannot* set a header must not be told to set one —
    which is what it said, on the surface where that caller is most likely to be."""
    client = await _client("demo-uc", "other-uc", require_use_case=True)
    with client:
        kira = client.post(KIRA, json=_KIRA_BODY, headers=TOKEN)
        gemini = client.post(GEMINI, json=_GEMINI_BODY, headers=TOKEN)

    assert kira.status_code == 403
    # One sentence for both surfaces, in each one's envelope.
    for message in (kira.json()["message"], gemini.json()["error"]["message"]):
        assert "/uc/<use-case>" in message, message
        assert "X-AIRA-Use-Case" in message, message
        assert "demo-uc" in message and "other-uc" in message, message


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


async def test_a_bound_key_names_its_use_case_on_both_surfaces() -> None:
    """A key is bound to one use case, and its model calls name it like anybody's (`FRD-149`). The
    refusal says which — the key's own — so the fix is one prefix in the client's base URL. A
    reading names nothing and is still answered: the KIRA model listing spends nothing."""
    client = await _client("bound-a", require_use_case=True)
    with client:
        full, prefix, key_hash = generate_api_key()
        async with client.app.state.db_sessionmaker() as session:  # type: ignore[attr-defined]
            session.add(
                ApiKey(
                    id=f"{prefix}-bound",
                    prefix=prefix,
                    key_hash=key_hash,
                    subject="alice",
                    use_case="bound-a",
                    label="bound",
                    is_active=True,
                )
            )
            await session.commit()
        key = {"x-goog-api-key": full}

        unnamed = _both(client, "", key)
        named = _both(client, "/uc/bound-a", key)
        refusal = client.post(KIRA, json=_KIRA_BODY, headers=key).json()
        listing = client.get("/kira/api/external/models", headers=key)

    assert unnamed == {"kira": 403, "gemini": 400}
    assert named == {"kira": 200, "gemini": 200}
    assert "/uc/bound-a" in refusal["message"], refusal["message"]
    assert listing.status_code == 200, listing.text


async def test_without_the_requirement_an_unnamed_call_goes_unattributed_on_both() -> None:
    """`AIRA_REQUIRE_USE_CASE=false` is refused anywhere but `local` (`security.py`); there, a
    person's call that names nothing goes unattributed on either surface — never to a use case
    picked for them. Their only use case has a one-request budget, and both calls are served: had
    either surface picked it, the second would have been refused."""
    client = await _client("demo-uc")
    with client:
        async with client.app.state.db_sessionmaker() as session:  # type: ignore[attr-defined]
            session.add(
                BudgetRead(
                    id=1, use_case="demo-uc", scope="use_case", period="day", limit_requests=1
                )
            )
            await session.commit()
        assert _both(client, "", TOKEN) == {"kira": 200, "gemini": 200}
