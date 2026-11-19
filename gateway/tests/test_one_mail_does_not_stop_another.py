"""One mail that cannot go out does not stop another, and nobody gets the same mail twice.

The notifier sends what is owed oldest first, one mail per recipient. Two failures of one mail
became failures of all of them:

- **a header that cannot be built.** A use case is named by its administrators, and a name with a
  line break in it — pasted, or sent through the API; also a form feed or U+2028 — made the email
  package refuse the `Subject` with a `ValueError`. Only a relay's refusal was caught, so the round
  ended there, and the next one started with the same mail: every budget mail of every use case
  waited until that mail's period ended, and anomaly mail, sent after the budget mail, never went;
- **a recipient the relay refuses** — a mailbox deleted while its owner still administers the use
  case. The first refusal ended the notice's sending, so the administrators sorted after it were
  never mailed, and each round sent the ones before it the same mail again.

So a header is one line whatever it is built from, a mail that cannot be built or sent is that
recipient's failure only, and a recipient who has a mail is not sent it again. Only a relay that
cannot be reached ends a round's sending early: everybody after would wait for the same timeout.

Driven like `test_budget_and_anomaly_mail.py`: the real notifier, the real directory client over a
`MockTransport`, and a relay that records what it was handed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from zoneinfo import ZoneInfo

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_common.directory import KeycloakDirectory
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all
from aira_gateway.db.models import (
    AnomalyEvent,
    BudgetRead,
    BudgetUsage,
    MailNotice,
    UseCaseGroupRead,
    UseCaseMemberRead,
    UseCaseRead,
)
from aira_gateway.mail import MailNotifier, MailUndeliverable
from aira_gateway.mail import notifier as notifier_module
from aira_gateway.mail.notifier import MAX_ATTEMPTS
from aira_gateway.mail.transport import RelayUnavailable

NOW = datetime(2026, 10, 15, 10, 0, tzinfo=UTC)
EUR = 1_000_000_000
PEOPLE = {
    "ada": "ada@example.com",
    "bob": "bob@example.com",
    "carol": "carol@example.com",
    "dan": "dan@example.com",
}
ADMIN_GROUP = "/aira-p-coding-admins"


class Relay:
    """Records what it was handed; refuses the addresses it is told to, as a relay does that checks
    its recipients, or cannot be reached at all."""

    def __init__(self, *, refusing: tuple[str, ...] = (), unreachable: bool = False) -> None:
        self.refusing = refusing
        self.unreachable = unreachable
        self.sent: list[EmailMessage] = []
        self.tried = 0

    async def send(self, message: EmailMessage) -> None:
        self.tried += 1
        if self.unreachable:
            raise RelayUnavailable("the relay could not be reached (ConnectionRefusedError)")
        if str(message["To"]) in self.refusing:
            # What `SmtpRelay` raises when the relay answers `RCPT TO` with a refusal.
            raise MailUndeliverable("the relay refused the recipient")
        self.sent.append(message)

    def to(self) -> list[str]:
        return [str(message["To"]) for message in self.sent]


def _keycloak() -> KeycloakDirectory:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/realms/aira/protocol/openid-connect/token":
            return httpx.Response(200, json={"access_token": "t"})
        if path == "/admin/realms/aira/users":
            name = request.url.params["username"]
            found = [{"username": name, "email": PEOPLE[name]}] if name in PEOPLE else []
            return httpx.Response(200, json=found)
        if path == f"/admin/realms/aira/group-by-path{ADMIN_GROUP}":
            return httpx.Response(200, json={"id": "g1", "path": ADMIN_GROUP})
        if path == "/admin/realms/aira/groups/g1/members":
            members = ("carol", "ada")
            return httpx.Response(200, json=[{"username": n, "email": PEOPLE[n]} for n in members])
        return httpx.Response(404)

    return KeycloakDirectory(
        "https://sso.example",
        "aira",
        "aira-directory",
        "s3cret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


@pytest_asyncio.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    yield build_sessionmaker(engine)
    await engine.dispose()


def _notifier(sessions: async_sessionmaker[AsyncSession], relay: Relay) -> MailNotifier:
    return MailNotifier(
        sessions,
        relay=relay,
        sender="AIRA <aira@example.com>",
        directory=_keycloak(),
        zone=ZoneInfo("Europe/Berlin"),
        currency="EUR",
        enforced=True,
        console_url="https://aira.example",
    )


def _shared_budget(slug: str, budget_id: int) -> BudgetRead:
    return BudgetRead(
        id=budget_id,
        use_case=slug,
        scope="use_case",
        subject="",
        period="month",
        limit_cost_nanos=10 * EUR,
        limit_tokens=None,
        limit_requests=None,
        enabled=True,
    )


def _spent(slug: str, cost: int) -> BudgetUsage:
    return BudgetUsage(
        scope_key=f"uc:{slug}",
        period_key="2026-10",
        tokens=0,
        requests=1,
        cost_nanos=cost * EUR,
        unpriced_requests=0,
    )


def _finding(slug: str) -> AnomalyEvent:
    return AnomalyEvent(
        id=f"f-{slug}",
        created_at=NOW + timedelta(seconds=30),
        rule_id=7,
        rule_name="Refusal burst",
        kind="refusal_rate",
        use_case=slug,
        target="use_case",
        target_value=slug,
        observed=60,
        threshold=50,
        sample=20,
        window_minutes=15,
        action_taken="alert",
        detail="60% of requests refused over 20 requests in 15 min",
    )


async def _given(sessions: async_sessionmaker[AsyncSession], *rows: object) -> None:
    async with sessions() as session:
        session.add_all(rows)
        await session.commit()


async def _notices(sessions: async_sessionmaker[AsyncSession]) -> dict[str, MailNotice]:
    async with sessions() as session:
        rows = (await session.execute(select(MailNotice))).scalars()
        return {f"{n.use_case}:{n.kind}": n for n in rows}


# ---- a mail that cannot be built --------------------------------------------------------


@pytest.mark.parametrize("separator", ["\n", "\r\n", " ", " ", "\x0c", "\x85"])
async def test_a_name_that_breaks_a_line_is_mailed_on_one_line(
    sessions: async_sessionmaker[AsyncSession], separator: str
) -> None:
    await _given(
        sessions,
        UseCaseRead(slug="alpha", name=f"Alpha{separator}team", budget_mail_thresholds=[80]),
        _shared_budget("alpha", 1),
        _spent("alpha", 9),
        UseCaseMemberRead(use_case_slug="alpha", subject="ada", role="admin"),
    )
    relay = Relay()

    await _notifier(sessions, relay).tick(NOW)

    (mail,) = relay.sent
    assert mail["Subject"] == 'AIRA: "Alpha team" has used 90% of its shared monthly budget'
    assert (await _notices(sessions))["alpha:budget"].outcome == "sent"


async def test_a_mail_that_cannot_be_built_stops_no_other(
    sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whatever stops one mail from being written — the header is only the one that happened —
    is that mail's failure: the older notice is first in every round, and the round must still
    reach the next use case's budget mail and the anomaly mail sent after all of them."""
    await _given(
        sessions,
        UseCaseRead(slug="alpha", name="Alpha", budget_mail_thresholds=[80]),
        UseCaseRead(
            slug="beta", name="Beta", budget_mail_thresholds=[80], mail_admins_on_anomalies=True
        ),
        _shared_budget("alpha", 1),
        _shared_budget("beta", 2),
        _spent("alpha", 9),
        UseCaseMemberRead(use_case_slug="alpha", subject="ada", role="admin"),
        UseCaseMemberRead(use_case_slug="beta", subject="bob", role="admin"),
    )
    real = notifier_module.budget_mail

    def budget_mail(state, **kwargs):  # noqa: ANN001, ANN202
        if state.use_case == "alpha":
            raise ValueError("Header values may not contain linefeed or carriage return characters")
        return real(state, **kwargs)

    monkeypatch.setattr(notifier_module, "budget_mail", budget_mail)
    relay = Relay()
    notifier = _notifier(sessions, relay)

    await notifier.tick(NOW)
    await _given(sessions, _spent("beta", 9), _finding("beta"))
    await notifier.tick(NOW + timedelta(minutes=1))

    assert relay.to() == ["bob@example.com", "bob@example.com"]
    assert [m["Subject"] for m in relay.sent] == [
        'AIRA: "Beta" has used 90% of its shared monthly budget',
        'AIRA: an anomaly rule found something in "Beta"',
    ]
    alpha = (await _notices(sessions))["alpha:budget"]
    assert (alpha.outcome, alpha.attempts) == ("", 2), "tried again next round, and counted"
    assert alpha.last_error == "the mail could not be written (ValueError)"


async def test_an_address_that_is_not_one_line_is_not_written_to(
    sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A subject is text, and folded onto one line it still says the same. An address is not:
    folded, a line break between two addresses leaves one header naming both. So the directory's
    address is used as it is or not at all — that recipient's failure, never the others'."""
    monkeypatch.setitem(PEOPLE, "eve", "eve@example.com\nmallory@example.com")
    await _given(
        sessions,
        UseCaseRead(slug="coding", name="Coding assistant", budget_mail_thresholds=[80]),
        _shared_budget("coding", 1),
        _spent("coding", 8),
        UseCaseMemberRead(use_case_slug="coding", subject="bob", role="admin"),
        UseCaseMemberRead(use_case_slug="coding", subject="eve", role="admin"),
    )
    relay = Relay()

    await _notifier(sessions, relay).tick(NOW)

    assert relay.to() == ["bob@example.com"]
    notice = (await _notices(sessions))["coding:budget"]
    assert (notice.outcome, notice.recipients) == ("", 1)
    assert notice.last_error == "the mail could not be written (ValueError)"


# ---- one recipient the relay refuses ----------------------------------------------------


async def _coding_with_three_administrators(sessions: async_sessionmaker[AsyncSession]) -> None:
    """Ada and Bob by name, Carol through the group (with Ada again): sorted Ada, Bob, Carol."""
    await _given(
        sessions,
        UseCaseRead(slug="coding", name="Coding assistant", budget_mail_thresholds=[80]),
        _shared_budget("coding", 1),
        _spent("coding", 8),
        UseCaseMemberRead(use_case_slug="coding", subject="ada", role="admin"),
        UseCaseMemberRead(use_case_slug="coding", subject="bob", role="admin"),
        UseCaseGroupRead(use_case_slug="coding", group_path=ADMIN_GROUP, role="admin"),
    )


async def test_the_administrators_after_a_refused_one_are_mailed(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _coding_with_three_administrators(sessions)
    relay = Relay(refusing=("ada@example.com",))

    await _notifier(sessions, relay).tick(NOW)

    assert relay.to() == ["bob@example.com", "carol@example.com"]


async def test_nobody_is_sent_a_mail_they_already_have(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """The refused address is tried again each round, until the notice's attempts are spent; the
    two who have the mail are not sent it again in any of them."""
    await _coding_with_three_administrators(sessions)
    relay = Relay(refusing=("bob@example.com",))
    notifier = _notifier(sessions, relay)

    for minute in range(MAX_ATTEMPTS + 2):
        await notifier.tick(NOW + timedelta(minutes=minute))

    assert relay.to() == ["ada@example.com", "carol@example.com"]
    notice = (await _notices(sessions))["coding:budget"]
    assert (notice.outcome, notice.attempts, notice.recipients) == ("failed", MAX_ATTEMPTS, 2)
    assert notice.last_error == "the relay refused the recipient"


async def test_a_refusal_that_passes_lets_the_mail_through_to_the_last_one(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _coding_with_three_administrators(sessions)
    relay = Relay(refusing=("bob@example.com",))
    notifier = _notifier(sessions, relay)

    await notifier.tick(NOW)
    relay.refusing = ()
    await notifier.tick(NOW + timedelta(minutes=1))

    assert relay.to() == ["ada@example.com", "carol@example.com", "bob@example.com"]
    notice = (await _notices(sessions))["coding:budget"]
    assert (notice.outcome, notice.recipients) == ("sent", 3)


async def test_a_relay_that_cannot_be_reached_is_tried_once_a_round(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Every recipient after the first would wait for the same connection timeout — fifteen
    seconds each, for each member of an administering group."""
    await _coding_with_three_administrators(sessions)
    relay = Relay(unreachable=True)

    await _notifier(sessions, relay).tick(NOW)

    assert relay.tried == 1
    notice = (await _notices(sessions))["coding:budget"]
    assert (notice.outcome, notice.attempts) == ("", 1)
