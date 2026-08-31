"""What a credential says about who is behind it, and what it does not (`FRD-613`, `FRD-604`).

An API key is the one credential in this system whose identity is **chosen by somebody else**: a
key names its owner, Management decides who that may be, and the gateway then treats every request
made with it as that person's. Three separate things follow from one field, and each was a live
defect somewhere until the round that produced this file:

- the audit row says the owner made the request;
- the owner's per-head allowance is what it spends;
- the owner's grant inside the use case is what it may read on the console endpoints.

The last is the one worth writing down rather than assuming, because it is a **transfer of
authority through a form field**. The control on it is on the other plane — only an administrator
of the use case may name somebody else, and only somebody who actually holds a grant may be named
(`management/backend/tests/test_access_ends_completely.py`) — and this file is where the
consequence it controls is stated, so that removing that control has somewhere to fail.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_gateway.auth.dependencies import (
    must_name_a_use_case,
    use_case_refusal,
)
from aira_gateway.auth.keys import DEMO_API_KEY, is_aira_key, parse_prefix
from aira_gateway.auth.principal import Principal
from aira_gateway.auth.service import ApiKeyService
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all
from aira_gateway.db.models import ApiKey


@pytest_asyncio.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    yield build_sessionmaker(engine)
    await engine.dispose()


async def _issue(
    sessions: async_sessionmaker[AsyncSession],
    *,
    subject: str = "ada",
    use_case: str | None = "uc-a",
    expires_at: datetime | None = None,
    is_active: bool = True,
) -> str:
    from aira_common.apikeys import generate_api_key

    full, prefix, key_hash = generate_api_key()
    async with sessions() as session:
        session.add(
            ApiKey(
                prefix=prefix,
                key_hash=key_hash,
                subject=subject,
                use_case=use_case,
                expires_at=expires_at,
                is_active=is_active,
            )
        )
        await session.commit()
    return full


# ═══ what the key resolves to ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_key_speaks_as_its_owner(sessions: async_sessionmaker[AsyncSession]) -> None:
    """Both alphabets coincide here, and that is the fact the rest of the system rests on: a key's
    subject already **is** its owner's username, so a rule written by name binds it."""
    full = await _issue(sessions, subject="ada")
    async with sessions() as session:
        principal = await ApiKeyService(session).verify(full)
    assert principal is not None
    assert (principal.subject, principal.username, principal.person) == ("ada", "ada", "ada")


@pytest.mark.asyncio
async def test_a_key_carries_its_prefix_as_the_credential_and_never_the_secret(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    full = await _issue(sessions)
    async with sessions() as session:
        principal = await ApiKeyService(session).verify(full)
    assert principal is not None
    assert principal.credential == parse_prefix(full)
    assert principal.credential is not None
    assert principal.credential not in full.split("_")[2]


@pytest.mark.asyncio
async def test_a_key_holds_no_organisation_wide_role(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """A key is issued for a use case, not for a person with a standing in the organisation. So
    oversight, incident authority and governance are all *withheld*, however privileged its owner
    is in the console."""
    full = await _issue(sessions, subject="admin")
    async with sessions() as session:
        principal = await ApiKeyService(session).verify(full)
    assert principal is not None
    assert principal.roles == ()
    assert not principal.is_oversight
    assert not principal.is_governance
    assert not principal.may_act_on_incidents


@pytest.mark.asyncio
async def test_a_key_reaches_exactly_the_use_case_it_was_issued_for(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    full = await _issue(sessions, use_case="uc-a")
    async with sessions() as session:
        principal = await ApiKeyService(session).verify(full)
    assert principal is not None
    assert principal.use_cases == ("uc-a",)
    assert use_case_refusal(principal, "uc-a") is None
    assert use_case_refusal(principal, "uc-b") is not None


@pytest.mark.asyncio
async def test_an_unbound_key_is_the_break_glass_credential(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Deliberately unrestricted, and deliberately the only such credential: it exists for the
    moment the control plane is unavailable, and a key that needs a use case *from Management* is
    no use when Management is what is broken (`ADR-0015`)."""
    full = await _issue(sessions, use_case=None)
    async with sessions() as session:
        principal = await ApiKeyService(session).verify(full)
    assert principal is not None
    assert principal.use_cases == ()
    assert use_case_refusal(principal, "anything") is None


@pytest.mark.asyncio
async def test_an_expired_key_is_a_refused_credential_not_a_missing_one(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    full = await _issue(sessions, expires_at=datetime.now(UTC) - timedelta(seconds=1))
    async with sessions() as session:
        assert await ApiKeyService(session).verify(full) is None


@pytest.mark.asyncio
async def test_a_key_expiring_in_a_moment_still_works(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    full = await _issue(sessions, expires_at=datetime.now(UTC) + timedelta(seconds=30))
    async with sessions() as session:
        assert await ApiKeyService(session).verify(full) is not None


@pytest.mark.asyncio
async def test_a_revoked_key_authenticates_nobody(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    full = await _issue(sessions, is_active=False)
    async with sessions() as session:
        assert await ApiKeyService(session).verify(full) is None


@pytest.mark.asyncio
async def test_the_right_prefix_with_the_wrong_secret_is_refused(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """The lookup is by prefix and the decision is by hash. A key whose prefix collides with a real
    one must not be admitted on the strength of the half that is public."""
    full = await _issue(sessions)
    namespace, prefix, _secret = full.split("_")
    forged = f"{namespace}_{prefix}_{'0' * 48}"
    async with sessions() as session:
        assert await ApiKeyService(session).verify(forged) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "presented",
    ["", "aira_", "aira_abcd", "aira_abcd_secret_extra", "notaira_abcd_secret", "aira__secret"],
    ids=["empty", "namespace-only", "two-parts", "four-parts", "wrong-namespace", "empty-prefix"],
)
async def test_a_malformed_key_is_refused_rather_than_raising(
    sessions: async_sessionmaker[AsyncSession], presented: str
) -> None:
    """A caller's own value must never become a server error, and a credential is the first value
    any caller sends."""
    async with sessions() as session:
        assert await ApiKeyService(session).verify(presented) is None


def test_a_bearer_token_is_not_mistaken_for_a_key() -> None:
    assert not is_aira_key("eyJhbGciOiJSUzI1NiJ9.e30.sig")
    assert is_aira_key(DEMO_API_KEY)


# ═══ what a key must name ═══════════════════════════════════════════════════════════════════════


class _Request:
    """The two things `must_name_a_use_case` reads."""

    def __init__(self, method: str = "POST", require: bool = True) -> None:
        self.method = method

        class _Settings:
            require_use_case = require

        class _State:
            settings = _Settings()

        self.app = type("App", (), {"state": _State()})()


BOUND = Principal(subject="ada", method="api_key", username="ada", use_cases=("uc-a",))
UNBOUND = Principal(subject="ops", method="api_key", username="ops")
SIGNED_IN = Principal(subject="uuid-1", method="oidc", username="ada")
DEMO = Principal(subject="demo", method="demo")


@pytest.mark.parametrize(
    ("principal", "expected"),
    [(BOUND, True), (UNBOUND, False), (SIGNED_IN, True), (DEMO, False)],
    ids=["bound-key", "break-glass-key", "oidc", "demo"],
)
def test_who_has_to_name_a_use_case(principal: Principal, expected: bool) -> None:
    assert must_name_a_use_case(_Request(), principal) is expected  # type: ignore[arg-type]


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_a_reading_is_not_a_model_call(method: str) -> None:
    """The requirement exists to attribute **spend**. A listing has nothing to attribute, and
    demanding a use case for it made the console's catalogue read `400` for a Global Administrator,
    who is a member of nothing by design."""
    assert not must_name_a_use_case(_Request(method), SIGNED_IN)  # type: ignore[arg-type]


def test_the_requirement_can_be_switched_off_wholesale() -> None:
    assert not must_name_a_use_case(_Request(require=False), SIGNED_IN)  # type: ignore[arg-type]


# ═══ a selector never grants ════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("principal", "wanted", "refused"),
    [
        (Principal(subject="u", method="oidc", use_cases=()), "uc-a", True),
        (Principal(subject="u", method="oidc", use_cases=("uc-b",)), "uc-a", True),
        (Principal(subject="u", method="oidc", use_cases=("uc-a",)), "uc-a", False),
        (Principal(subject="u", method="api_key", use_cases=("uc-a",)), "uc-b", True),
        (Principal(subject="u", method="api_key", use_cases=()), "uc-b", False),
        (Principal(subject="u", method="demo"), "uc-a", False),
    ],
    ids=[
        "oidc-member-of-nothing",
        "oidc-member-elsewhere",
        "oidc-member",
        "key-bound-elsewhere",
        "break-glass",
        "demo",
    ],
)
def test_a_selector_chooses_among_what_you_have(
    principal: Principal, wanted: str, refused: bool
) -> None:
    """An **empty** membership list means nothing, not anything. The KIRA surface read it the other
    way for months, so a caller belonging to no use case could name somebody else's and have the
    tokens billed to it."""
    assert (use_case_refusal(principal, wanted) is not None) is refused
