"""Mail when a budget crosses its use case's thresholds, and when its anomaly rules find something
(`FRD-409`).

Driven through the real notifier, the real directory client over a `MockTransport` (Keycloak's own
shapes), and a relay that records what it was handed: what is under test is who is told what, once.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from zoneinfo import ZoneInfo

import httpx
import pytest
import pytest_asyncio
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_common.directory import KeycloakDirectory
from aira_gateway.config import GatewaySettings
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
from aira_gateway.mail import MailNotifier, MailUndeliverable, build_notifier
from aira_gateway.mail import notifier as notifier_module
from aira_gateway.mail.notifier import KEEP_NOTICES, MAX_ATTEMPTS

#: Mid-October in Berlin, so the month is `2026-10` and it resets on 1 November at local midnight.
NOW = datetime(2026, 10, 15, 10, 0, tzinfo=UTC)
BERLIN = ZoneInfo("Europe/Berlin")
EUR = 1_000_000_000  # nano-units

PEOPLE = {
    "ada": "ada@example.com",
    "bob": "bob@example.com",
    "carol": "carol@example.com",
    "nomail": "",
}
ADMIN_GROUP = "/aira-p-coding-admins"


class Outbox:
    """A relay that keeps what it was handed, or refuses it."""

    def __init__(self, *, refuse: bool = False) -> None:
        self.sent: list[EmailMessage] = []
        self.refuse = refuse

    async def send(self, message: EmailMessage) -> None:
        if self.refuse:
            raise MailUndeliverable("the relay could not be reached (ConnectionRefusedError)")
        self.sent.append(message)

    def to(self) -> list[str]:
        return [str(message["To"]) for message in self.sent]


def _keycloak(*, group_members: tuple[str, ...] = ("carol", "ada"), down: bool = False):
    def handler(request: httpx.Request) -> httpx.Response:
        if down:
            return httpx.Response(503)
        path = request.url.path
        if path == "/realms/aira/protocol/openid-connect/token":
            return httpx.Response(200, json={"access_token": "t"})
        if path == "/admin/realms/aira/users":
            name = request.url.params["username"]
            if name not in PEOPLE:
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=[{"username": name, "email": PEOPLE[name]}])
        if path == f"/admin/realms/aira/group-by-path{ADMIN_GROUP}":
            return httpx.Response(200, json={"id": "g1", "path": ADMIN_GROUP})
        if path == "/admin/realms/aira/groups/g1/members":
            return httpx.Response(
                200, json=[{"username": n, "email": PEOPLE[n]} for n in group_members]
            )
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


def _notifier(
    sessions: async_sessionmaker[AsyncSession],
    outbox: Outbox | None = None,
    *,
    directory: KeycloakDirectory | None = None,
    enforced: bool = True,
    console_url: str = "https://aira.example",
) -> MailNotifier:
    return MailNotifier(
        sessions,
        relay=outbox or Outbox(),
        sender="AIRA <aira@example.com>",
        directory=directory if directory is not None else _keycloak(),
        zone=BERLIN,
        currency="EUR",
        enforced=enforced,
        console_url=console_url,
    )


async def _given(sessions: async_sessionmaker[AsyncSession], *rows: object) -> None:
    async with sessions() as session:
        session.add_all(rows)
        await session.commit()


def _use_case(
    thresholds: list[int] | None = None, *, anomalies: bool = False, slug: str = "coding"
) -> UseCaseRead:
    return UseCaseRead(
        slug=slug,
        name="Coding assistant",
        budget_mail_thresholds=thresholds,
        mail_admins_on_anomalies=anomalies,
    )


def _budget(
    scope: str = "each_member",
    *,
    id: int = 1,
    period: str = "month",
    cost: int | None = 10 * EUR,
    tokens: int | None = None,
    subject: str = "",
    enabled: bool = True,
) -> BudgetRead:
    return BudgetRead(
        id=id,
        use_case="coding",
        scope=scope,
        subject=subject,
        period=period,
        limit_cost_nanos=cost,
        limit_tokens=tokens,
        limit_requests=None,
        enabled=enabled,
    )


def _spent(key: str, cost: float = 0, *, period: str = "2026-10", tokens: int = 0) -> BudgetUsage:
    return BudgetUsage(
        scope_key=key,
        period_key=period,
        tokens=tokens,
        requests=1,
        cost_nanos=round(cost * EUR),
        unpriced_requests=0,
    )


def _admins() -> list[object]:
    return [
        UseCaseMemberRead(use_case_slug="coding", subject="ada", role="admin"),
        UseCaseMemberRead(use_case_slug="coding", subject="bob", role="user"),
        UseCaseGroupRead(use_case_slug="coding", group_path=ADMIN_GROUP, role="admin"),
    ]


async def _notices(sessions: async_sessionmaker[AsyncSession]) -> list[MailNotice]:
    async with sessions() as session:
        rows = await session.execute(select(MailNotice).order_by(MailNotice.threshold))
        return list(rows.scalars())


def _body(message: EmailMessage) -> str:
    return message.get_content()


# ---- a person's own budget ---------------------------------------------------------------


async def test_a_member_past_a_threshold_is_mailed_once_with_the_share_and_the_reset(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _given(sessions, _use_case([80, 100]), _budget(), _spent("member:coding:ada", 8.2))
    outbox = Outbox()
    notifier = _notifier(sessions, outbox)

    await notifier.tick(NOW)
    await notifier.tick(NOW + timedelta(minutes=1))

    assert outbox.to() == ["ada@example.com"]
    mail = outbox.sent[0]
    assert "82%" in mail["Subject"] and "monthly" in mail["Subject"]
    body = _body(mail)
    assert "8.20 of 10.00 EUR" in body
    assert "It is the budget each member of the use case has." in body
    assert "1 November 2026 at 00:00 (Europe/Berlin)" in body
    assert "https://aira.example/use-cases/coding?tab=budgets" in body
    assert "at 80% and 100% of a budget" in body
    assert [(n.threshold, n.outcome, n.recipients) for n in await _notices(sessions)] == [
        (80, "sent", 1)
    ]


async def test_below_every_threshold_nobody_is_mailed_and_nothing_is_noted(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # 79.99 % is not 80 %: the share is rounded down.
    await _given(sessions, _use_case([80]), _budget(), _spent("member:coding:ada", 7.999))
    outbox = Outbox()

    await _notifier(sessions, outbox).tick(NOW)

    assert outbox.sent == []
    assert await _notices(sessions) == []


async def test_several_thresholds_crossed_at_once_mail_the_highest_only(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _given(sessions, _use_case([50, 80, 100]), _budget(), _spent("member:coding:ada", 10.4))
    outbox = Outbox()

    await _notifier(sessions, outbox).tick(NOW)

    assert len(outbox.sent) == 1
    assert "is used up" in outbox.sent[0]["Subject"]
    assert "Until then, your requests in this use case are refused." in _body(outbox.sent[0])
    assert [(n.threshold, n.outcome) for n in await _notices(sessions)] == [
        (50, "superseded"),
        (80, "superseded"),
        (100, "sent"),
    ]


async def test_the_next_threshold_is_mailed_when_it_is_reached(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _given(sessions, _use_case([80, 100]), _budget(), _spent("member:coding:ada", 8.5))
    outbox = Outbox()
    notifier = _notifier(sessions, outbox)
    await notifier.tick(NOW)

    async with sessions() as session:
        usage = await session.get(BudgetUsage, ("member:coding:ada", "2026-10"))
        assert usage is not None
        usage.cost_nanos = 10 * EUR
        await session.commit()
    await notifier.tick(NOW + timedelta(minutes=1))

    assert [m["Subject"] for m in outbox.sent] == [
        'AIRA: you have used 85% of your monthly budget in "Coding assistant"',
        'AIRA: your monthly budget in "Coding assistant" is used up',
    ]


async def test_a_threshold_added_below_one_already_mailed_is_noted_and_not_mailed(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _given(sessions, _use_case([100]), _budget(), _spent("member:coding:ada", 10))
    outbox = Outbox()
    notifier = _notifier(sessions, outbox)
    await notifier.tick(NOW)

    async with sessions() as session:
        use_case = await session.get(UseCaseRead, "coding")
        assert use_case is not None
        use_case.budget_mail_thresholds = [50, 100]
        await session.commit()
    await notifier.tick(NOW + timedelta(minutes=1))

    assert len(outbox.sent) == 1
    assert [(n.threshold, n.outcome) for n in await _notices(sessions)] == [
        (50, "superseded"),
        (100, "sent"),
    ]


async def test_a_person_with_a_budget_of_their_own_hears_of_that_one(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # 9 EUR is 90 % of the budget everybody has and 9 % of the one set for Ada (`ADR-0044`).
    await _given(
        sessions,
        _use_case([80]),
        _budget(),
        _budget("member", id=2, cost=100 * EUR, subject="ada"),
        _spent("member:coding:ada", 9),
        _spent("member:coding:bob", 9),
    )
    outbox = Outbox()

    await _notifier(sessions, outbox).tick(NOW)

    assert outbox.to() == ["bob@example.com"]


async def test_a_budget_set_for_one_person_says_so(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _given(
        sessions,
        _use_case([100]),
        _budget("member", cost=2 * EUR, subject="ada"),
        _spent("member:coding:ada", 2),
    )
    outbox = Outbox()

    await _notifier(sessions, outbox).tick(NOW)

    assert "It is a budget set for you alone." in _body(outbox.sent[0])


async def test_a_lifted_budget_stops_nobody_and_mails_nobody(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _given(sessions, _use_case([80]), _budget(enabled=False), _spent("member:coding:ada", 9))
    outbox = Outbox()

    await _notifier(sessions, outbox).tick(NOW)

    assert outbox.sent == []


async def test_a_use_case_without_thresholds_mails_nothing_and_notes_nothing(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _given(sessions, _use_case([]), _budget(), _spent("member:coding:ada", 12))
    outbox = Outbox()

    await _notifier(sessions, outbox).tick(NOW)

    assert outbox.sent == []
    assert await _notices(sessions) == []


async def test_a_use_case_that_never_said_mails_when_a_budget_is_used_up(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    # `None`: an event from before the setting, read as the default both planes share.
    await _given(
        sessions,
        _use_case(None),
        _budget(),
        _spent("member:coding:ada", 9.5),
        _spent("member:coding:bob", 10),
    )
    outbox = Outbox()

    await _notifier(sessions, outbox).tick(NOW)

    assert outbox.to() == ["bob@example.com"]


async def test_the_dimension_closest_to_its_limit_speaks(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _given(
        sessions,
        _use_case([80]),
        _budget(cost=10 * EUR, tokens=1000),
        _spent("member:coding:ada", 1, tokens=900),
    )
    outbox = Outbox()

    await _notifier(sessions, outbox).tick(NOW)

    body = _body(outbox.sent[0])
    assert "90%" in outbox.sent[0]["Subject"]
    assert "1.00 of 10.00 EUR" in body and "900 of 1,000" in body


async def test_a_daily_budget_is_today_and_resets_at_the_installations_midnight(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _given(
        sessions,
        _use_case([100]),
        _budget(period="day", cost=1 * EUR),
        _spent("member:coding:ada", 1, period="2026-10-15"),
    )
    outbox = Outbox()

    await _notifier(sessions, outbox).tick(NOW)

    body = _body(outbox.sent[0])
    assert "daily" in outbox.sent[0]["Subject"]
    assert "Today so far:" in body
    assert "16 October 2026 at 00:00 (Europe/Berlin)" in body


async def test_where_budgets_do_not_refuse_a_used_up_one_is_said_to_be_for_information(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _given(sessions, _use_case([100]), _budget(), _spent("member:coding:ada", 11))
    outbox = Outbox()

    await _notifier(sessions, outbox, enforced=False).tick(NOW)

    body = _body(outbox.sent[0])
    assert "does not refuse requests over a budget" in body
    assert "refused" not in body.split("does not refuse")[0]


async def test_without_a_console_address_the_mail_names_the_tab(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _given(sessions, _use_case([100]), _budget(), _spent("member:coding:ada", 10))
    outbox = Outbox()

    await _notifier(sessions, outbox, console_url="").tick(NOW)

    body = _body(outbox.sent[0])
    assert "Budgets tab in the console" in body and "http" not in body


# ---- the shared budget -------------------------------------------------------------------


async def test_the_shared_budget_reaches_every_administrator_once_and_no_member(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _given(sessions, _use_case([80]), _budget("use_case"), _spent("uc:coding", 8), *_admins())
    outbox = Outbox()

    await _notifier(sessions, outbox).tick(NOW)

    # Ada by name and through the group, once; Carol through the group; Bob is only a member.
    assert outbox.to() == ["ada@example.com", "carol@example.com"]
    assert all('"Coding assistant" has used 80%' in m["Subject"] for m in outbox.sent)
    assert "You receive this mail as an administrator" in _body(outbox.sent[0])
    assert [(n.outcome, n.recipients) for n in await _notices(sessions)] == [("sent", 2)]


# ---- delivery ----------------------------------------------------------------------------


async def test_a_relay_that_fails_is_tried_again_and_given_up_after_its_attempts(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _given(sessions, _use_case([100]), _budget(), _spent("member:coding:ada", 10))
    refusing = Outbox(refuse=True)
    notifier = _notifier(sessions, refusing)

    for minute in range(MAX_ATTEMPTS):
        await notifier.tick(NOW + timedelta(minutes=minute))
    (notice,) = await _notices(sessions)
    assert (notice.outcome, notice.attempts) == ("failed", MAX_ATTEMPTS)
    assert "ConnectionRefusedError" in notice.last_error
    assert "@" not in notice.last_error

    working = Outbox()
    await _notifier(sessions, working).tick(NOW + timedelta(minutes=MAX_ATTEMPTS))
    assert working.sent == []


async def test_a_relay_back_in_time_delivers_what_was_owed(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _given(sessions, _use_case([100]), _budget(), _spent("member:coding:ada", 10))
    await _notifier(sessions, Outbox(refuse=True)).tick(NOW)

    working = Outbox()
    await _notifier(sessions, working).tick(NOW + timedelta(minutes=1))

    assert working.to() == ["ada@example.com"]
    (notice,) = await _notices(sessions)
    assert (notice.outcome, notice.attempts) == ("sent", 1)


async def test_a_mail_owed_for_a_period_that_has_ended_is_not_sent_late(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _given(sessions, _use_case([100]), _budget(), _spent("member:coding:ada", 10))
    await _notifier(sessions, Outbox(refuse=True)).tick(NOW)

    working = Outbox()
    await _notifier(sessions, working).tick(datetime(2026, 11, 1, 0, 5, tzinfo=UTC))

    assert working.sent == []
    (notice,) = await _notices(sessions)
    assert notice.outcome == "expired"


async def test_somebody_without_an_address_is_noted_and_not_tried_again(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _given(sessions, _use_case([100]), _budget(), _spent("member:coding:nomail", 10))
    outbox = Outbox()

    await _notifier(sessions, outbox).tick(NOW)

    assert outbox.sent == []
    (notice,) = await _notices(sessions)
    assert notice.outcome == "no_recipient"


async def test_a_directory_that_cannot_be_asked_is_asked_again_next_round(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _given(sessions, _use_case([100]), _budget(), _spent("member:coding:ada", 10))

    await _notifier(sessions, directory=_keycloak(down=True)).tick(NOW)

    (notice,) = await _notices(sessions)
    assert (notice.outcome, notice.attempts) == ("", 1)
    assert notice.last_error.startswith("the directory could not be asked")


async def test_a_mail_no_longer_true_when_it_is_sent_is_not_said(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _given(sessions, _use_case([100]), _budget(), _spent("member:coding:ada", 10))
    await _notifier(sessions, Outbox(refuse=True)).tick(NOW)
    async with sessions() as session:
        budget = await session.get(BudgetRead, 1)
        assert budget is not None
        budget.limit_cost_nanos = 50 * EUR  # raised in the meantime
        await session.commit()

    working = Outbox()
    await _notifier(sessions, working).tick(NOW + timedelta(minutes=1))

    assert working.sent == []
    (notice,) = await _notices(sessions)
    assert notice.outcome == "superseded"


async def test_old_notices_are_forgotten_a_month_after_they_expired(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _given(sessions, _use_case([100]), _budget(), _spent("member:coding:ada", 10))
    notifier = _notifier(sessions)
    await notifier.tick(NOW)

    await notifier.tick(datetime(2026, 11, 1, tzinfo=UTC) + KEEP_NOTICES - timedelta(hours=2))
    assert len(await _notices(sessions)) == 1
    await notifier.tick(datetime(2026, 11, 1, tzinfo=UTC) + KEEP_NOTICES + timedelta(hours=1))
    assert await _notices(sessions) == []


# ---- anomaly findings --------------------------------------------------------------------


def _finding(
    *,
    id: str = "f1",
    use_case: str | None = "coding",
    target: str = "subject",
    target_value: str = "ada",
    kind: str = "refusal_rate",
    detail: str = "60% of requests refused over 20 requests in 15 min",
    minutes_ago: int = 1,
) -> AnomalyEvent:
    return AnomalyEvent(
        id=id,
        created_at=NOW - timedelta(minutes=minutes_ago),
        rule_id=7,
        rule_name="Refusal burst",
        kind=kind,
        use_case=use_case,
        target=target,
        target_value=target_value,
        observed=60,
        threshold=50,
        sample=20,
        window_minutes=15,
        action_taken="alert",
        detail=detail,
    )


async def test_a_finding_reaches_the_administrators_and_names_nobody(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _given(sessions, _use_case(anomalies=True), _finding(), *_admins())
    outbox = Outbox()

    await _notifier(sessions, outbox).tick(NOW)

    assert outbox.to() == ["ada@example.com", "carol@example.com"]
    body = _body(outbox.sent[0])
    assert 'rule "Refusal burst" (refusal_rate) measured 60 against a threshold of 50' in body
    assert "It concerned one person." in body
    assert "https://aira.example/use-cases/coding?tab=warnings" in body
    # The person is withheld from the mail whatever strict data mode says: "ada" appears only as
    # the address the mail is sent to.
    assert "ada" not in body.replace("ada@example.com", "")


async def test_a_new_address_finding_is_mailed_without_the_addresses(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    finding = _finding(
        kind="new_source_ip",
        target="credential",
        target_value="aira_abc",
        detail="used from 2 address(es) not seen before: 10.0.0.1, 10.0.0.2",
    )
    await _given(sessions, _use_case(anomalies=True), finding, *_admins())
    outbox = Outbox()

    await _notifier(sessions, outbox).tick(NOW)

    body = _body(outbox.sent[0])
    assert "10.0.0.1" not in body and "aira_abc" not in body
    assert "It concerned one API key." in body


async def test_several_findings_in_one_round_are_one_mail_per_administrator(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _given(
        sessions,
        _use_case(anomalies=True),
        _finding(id="f1"),
        _finding(id="f2", target="use_case", target_value="coding"),
        *_admins(),
    )
    outbox = Outbox()
    notifier = _notifier(sessions, outbox)

    await notifier.tick(NOW)
    await notifier.tick(NOW + timedelta(minutes=1))

    assert outbox.to() == ["ada@example.com", "carol@example.com"]
    assert outbox.sent[0]["Subject"] == 'AIRA: anomaly rules found 2 things in "Coding assistant"'
    assert "It concerned the use case as a whole." in _body(outbox.sent[0])


async def test_findings_of_a_use_case_that_did_not_ask_mail_nobody(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _given(sessions, _use_case(), _finding(), *_admins())
    outbox = Outbox()

    await _notifier(sessions, outbox).tick(NOW)

    assert outbox.sent == []
    assert await _notices(sessions) == []


async def test_a_finding_that_belongs_to_no_use_case_stays_in_the_security_console(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _given(sessions, _use_case(anomalies=True), _finding(use_case=None), *_admins())
    outbox = Outbox()

    await _notifier(sessions, outbox).tick(NOW)

    assert outbox.sent == []


async def test_a_finding_older_than_the_look_back_is_not_news(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _given(sessions, _use_case(anomalies=True), _finding(minutes_ago=90), *_admins())
    outbox = Outbox()

    await _notifier(sessions, outbox).tick(NOW)

    assert outbox.sent == []


async def test_a_switch_turned_off_before_the_mail_went_out_sends_nothing(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _given(sessions, _use_case(anomalies=True), _finding(), *_admins())
    await _notifier(sessions, Outbox(refuse=True)).tick(NOW)
    async with sessions() as session:
        use_case = await session.get(UseCaseRead, "coding")
        assert use_case is not None
        use_case.mail_admins_on_anomalies = False
        await session.commit()

    working = Outbox()
    await _notifier(sessions, working).tick(NOW + timedelta(minutes=1))

    assert working.sent == []
    (notice,) = await _notices(sessions)
    assert notice.outcome == "superseded"


# ---- configuration -----------------------------------------------------------------------


def _fresh_log(monkeypatch: pytest.MonkeyPatch) -> None:
    """A logger first used inside the capture: the module's own may already be cached with the
    processors an earlier test configured, and would log past it."""
    monkeypatch.setattr(notifier_module, "_log", structlog.get_logger(notifier_module.__name__))


def _settings(**values: object) -> GatewaySettings:
    return GatewaySettings(
        oidc_issuer="https://sso.example/realms/aira",
        directory_client_id="aira-directory",
        directory_client_secret="s3cret",
        **values,  # type: ignore[arg-type]
    )


def test_without_a_relay_there_is_no_notifier(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    assert build_notifier(_settings(), sessions, BERLIN) is None


def test_a_relay_without_a_sender_sends_nothing_and_says_so_once(
    sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    with structlog.testing.capture_logs() as logs:
        _fresh_log(monkeypatch)
        notifier = build_notifier(_settings(smtp_host="relay.example"), sessions, BERLIN)

    assert notifier is None
    assert [entry["event"] for entry in logs] == ["mail_off"]


def test_a_relay_and_a_sender_make_a_notifier_with_the_installations_directory(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    notifier = build_notifier(
        _settings(smtp_host="relay.example", mail_from="AIRA <aira@example.com>"),
        sessions,
        BERLIN,
    )

    assert notifier is not None
    assert notifier.directory is not None
    assert notifier.sender == "AIRA <aira@example.com>"


def test_mail_without_a_directory_is_said_at_start(
    sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    with structlog.testing.capture_logs() as logs:
        _fresh_log(monkeypatch)
        notifier = build_notifier(
            GatewaySettings(smtp_host="relay.example", mail_from="aira@example.com"),
            sessions,
            BERLIN,
        )

    assert notifier is not None and notifier.directory is None
    assert [entry["event"] for entry in logs] == ["mail_without_directory"]


@pytest.mark.parametrize("settings_relay", ["", "   "])
def test_a_blank_relay_is_no_relay(
    sessions: async_sessionmaker[AsyncSession], settings_relay: str
) -> None:
    assert (
        build_notifier(_settings(smtp_host=settings_relay, mail_from="a@b"), sessions, BERLIN)
        is None
    )
