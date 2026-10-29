"""Mail when a budget crosses its use case's thresholds, and when its anomaly rules find something
(`FRD-409`, `ADR-0050`).

A background task like the anomaly evaluation, off the request path, and correct with several
gateway instances for the same reasons:

- **the facts are shared**: the counters are `budget_usage`, the findings `anomaly_events`;
- **each mail owed is a row** in `mail_notices`, unique per counter, period and threshold or per
  finding, so a round that sees a crossing again owes nothing new;
- **one notifier per round**, claimed with a transaction-scoped advisory lock that a session of its
  own holds while the work commits notice by notice. A crash releases the lock with its connection
  and costs at most the mail in hand, which the next round sends again: at least once, never lost.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta, tzinfo
from email.message import EmailMessage

import structlog
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_common.directory import DirectoryUnavailable, KeycloakDirectory, directory_for
from aira_common.mail import KEEP_NOTICES_DAYS
from aira_gateway.budgets.keys import period_ends, period_key, period_of
from aira_gateway.budgets.store import slot_of
from aira_gateway.config import GatewaySettings
from aira_gateway.db.errors import loggable
from aira_gateway.db.models import (
    AnomalyEvent,
    BudgetRead,
    BudgetUsage,
    MailNotice,
    UseCaseRead,
)
from aira_gateway.mail.messages import BudgetState, anomaly_mail, budget_mail
from aira_gateway.mail.recipients import Addresses, Recipient
from aira_gateway.mail.transport import MAX_REASON, MailUndeliverable, Relay, SmtpRelay
from aira_gateway.scopes import PER_PERSON, USE_CASE, Scope, binding

_log = structlog.get_logger(__name__)

#: The advisory-lock key every instance claims each round, apart from the anomaly evaluation's:
#: the two run on their own clocks and must not wait for each other.
TICK_LOCK_KEY = 0x4149_5241_4D41_494C

#: How often the notifier wakes. A minute is soon enough for a budget, and the anomaly evaluation
#: writes its findings on the same rhythm.
INTERVAL_SECONDS = 60.0

#: How often a mail is tried before it is given up as `failed`, one round apart.
MAX_ATTEMPTS = 5

#: How far back a round looks for findings: longer than any pause between two rounds, so a
#: restart misses none, and short enough that switching the mail on does not send last week's.
ANOMALY_LOOKBACK = timedelta(hours=1)

#: How long a finding is worth a mail. Older, it is no longer news, and the console has it.
ANOMALY_NEWS = timedelta(hours=24)

#: How long a notice is kept after it expired, as the record of what was sent and what was not.
KEEP_NOTICES = timedelta(days=KEEP_NOTICES_DAYS)

BUDGET = "budget"
ANOMALY = "anomaly"

#: What became of a notice (`MailNotice.outcome`). Empty while the mail is owed.
OWED = ""
SENT = "sent"
SUPERSEDED = "superseded"
NO_RECIPIENT = "no_recipient"
FAILED = "failed"
EXPIRED = "expired"

#: Where each capped dimension of a budget is read: its limit on the budget, its use on the counter.
_DIMENSIONS = (
    ("cost", "limit_cost_nanos", "cost_nanos"),
    ("requests", "limit_requests", "requests"),
    ("tokens", "limit_tokens", "tokens"),
)


def _aware(moment: datetime) -> datetime:
    """``moment`` with its zone. SQLite hands a stored instant back without one."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


@dataclass
class MailNotifier:
    """Owes and sends the mail of `FRD-409`, one round a minute."""

    sessionmaker: async_sessionmaker[AsyncSession]
    relay: Relay
    sender: str
    directory: KeycloakDirectory | None
    #: The installation's clock (`FRD-629`): where a budget's period begins and ends.
    zone: tzinfo
    currency: str
    #: Whether budgets refuse at all here (`AIRA_ENFORCE_BUDGETS`), which a mail at 100 % says.
    enforced: bool
    console_url: str = ""
    interval_seconds: float = INTERVAL_SECONDS
    _task: asyncio.Task[None] | None = None

    # -- lifecycle -------------------------------------------------------------------------

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.interval_seconds)
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - defensive
                # A notifier that dies on one bad round tells nobody anything again.
                _log.warning("mail_round_failed", error=loggable(exc))

    # -- one round -------------------------------------------------------------------------

    async def tick(self, now: datetime | None = None) -> list[MailNotice]:
        """Owe what crossed since the last round, then send what is owed. Returns the notices this
        round settled, sent or not."""
        moment = now or datetime.now(UTC)
        async with self.sessionmaker() as claim:
            if not await self._claim_the_round(claim):
                return []
            async with self.sessionmaker() as session:
                use_cases = await self._live_use_cases(session)
                await self._owe_budget_mail(session, use_cases, moment)
                await self._owe_anomaly_mail(session, use_cases, moment)
                await session.commit()
                settled = await self._send_what_is_owed(session, moment)
                # Not synchronised with the session: nothing loaded is affected, and comparing in
                # Python would meet SQLite's instants without their zone.
                await session.execute(
                    delete(MailNotice)
                    .where(MailNotice.expires_at < moment - KEEP_NOTICES)
                    .execution_options(synchronize_session=False)
                )
                await session.commit()
        return settled

    async def _claim_the_round(self, session: AsyncSession) -> bool:
        """Whether this instance sends this round, fleet-wide. SQLite has no advisory locks and no
        second instance, so it always does."""
        if session.get_bind().dialect.name != "postgresql":
            return True
        claimed = await session.execute(select(func.pg_try_advisory_xact_lock(TICK_LOCK_KEY)))
        return bool(claimed.scalar())

    async def _live_use_cases(self, session: AsyncSession) -> dict[str, UseCaseRead]:
        rows = await session.execute(select(UseCaseRead).where(UseCaseRead.deleted_at.is_(None)))
        return {row.slug: row for row in rows.scalars()}

    # -- what is owed ----------------------------------------------------------------------

    async def _owe_budget_mail(
        self, session: AsyncSession, use_cases: dict[str, UseCaseRead], moment: datetime
    ) -> None:
        """A notice for every counter past a threshold of its use case, once (`FRD-409` FR-2–4)."""
        mailing = {slug: row for slug, row in use_cases.items() if row.mail_thresholds}
        if not mailing:
            return
        rows = await session.execute(select(BudgetRead).where(BudgetRead.use_case.in_(mailing)))
        budgets: dict[str, list[BudgetRead]] = defaultdict(list)
        for budget in rows.scalars():
            budgets[budget.use_case].append(budget)
        for slug, own in sorted(budgets.items()):
            use_case = mailing[slug]
            counters = await self._counters(session, slug, own, moment)
            if not counters:
                continue
            recorded = await self._recorded(session, slug, {pk for _, pk, _ in counters})
            for scope_key, pk, state in counters:
                crossed = [t for t in use_case.mail_thresholds if state.share >= t]
                self._owe(
                    session,
                    kind=BUDGET,
                    use_case=slug,
                    subject_key=scope_key,
                    period_key=pk,
                    crossed=crossed,
                    recorded=recorded.get((scope_key, pk), set()),
                    expires_at=state.resets_at,
                    moment=moment,
                )

    async def _counters(
        self, session: AsyncSession, slug: str, budgets: Sequence[BudgetRead], moment: datetime
    ) -> list[tuple[str, str, BudgetState]]:
        """Every counter of ``slug`` in its current period, against the budget that binds it."""
        keys = {
            period_key(period, moment, self.zone): period for period in {b.period for b in budgets}
        }
        shared, per_person = Scope(slug).usage_key, Scope(slug, "").usage_key
        rows = await session.execute(
            select(BudgetUsage).where(
                BudgetUsage.period_key.in_(sorted(keys)),
                or_(
                    BudgetUsage.scope_key == shared,
                    BudgetUsage.scope_key.startswith(per_person, autoescape=True),
                ),
            )
        )
        found: list[tuple[str, str, BudgetState]] = []
        for usage in rows.scalars():
            state = self._state(slug, budgets, usage, keys[usage.period_key], moment)
            if state is not None:
                found.append((usage.scope_key, usage.period_key, state))
        return found

    def _state(
        self,
        slug: str,
        budgets: Sequence[BudgetRead],
        usage: BudgetUsage,
        period: str,
        moment: datetime,
    ) -> BudgetState | None:
        """``usage`` against the enabled budget that binds it, or ``None`` where none does.

        A per-person counter is read against the row the hierarchy gives that person
        (`ADR-0044`), so somebody with a budget of their own hears of that one and not of the one
        it replaced; a lifted row stops nobody and mails nobody. Where several rows bind one
        counter, the one closest to its limit speaks.
        """
        per_person = Scope(slug, "").usage_key
        person = (
            usage.scope_key.removeprefix(per_person)
            if usage.scope_key.startswith(per_person)
            else None
        )
        rows = [
            row
            for row in binding(budgets, caller=person, slot=slot_of)
            if row.period == period
            and row.enabled
            and (row.scope in PER_PERSON if person is not None else row.scope == USE_CASE)
        ]
        best: tuple[int, BudgetRead, tuple[tuple[str, int, int], ...]] | None = None
        for row in rows:
            figures = tuple(
                (dimension, int(getattr(usage, used)), int(limit))
                for dimension, limit_field, used in _DIMENSIONS
                if (limit := getattr(row, limit_field)) is not None and limit > 0
            )
            if not figures:
                continue
            share = max(used * 100 // limit for _, used, limit in figures)
            if best is None or share > best[0]:
                best = (share, row, figures)
        if best is None:
            return None
        share, row, figures = best
        return BudgetState(
            use_case=slug,
            use_case_name="",
            scope=row.scope,
            period=period,
            share=share,
            figures=figures,
            resets_at=period_ends(period, moment, self.zone),
        )

    async def _recorded(
        self, session: AsyncSession, slug: str, period_keys: set[str]
    ) -> dict[tuple[str, str], set[int]]:
        """The thresholds already noted per counter of ``slug``, in one query rather than one per
        counter."""
        rows = await session.execute(
            select(MailNotice.subject_key, MailNotice.period_key, MailNotice.threshold).where(
                MailNotice.kind == BUDGET,
                MailNotice.use_case == slug,
                MailNotice.period_key.in_(sorted(period_keys)),
            )
        )
        recorded: dict[tuple[str, str], set[int]] = defaultdict(set)
        for subject_key, pk, threshold in rows.all():
            recorded[(subject_key, pk)].add(threshold)
        return recorded

    def _owe(
        self,
        session: AsyncSession,
        *,
        kind: str,
        use_case: str,
        subject_key: str,
        period_key: str,
        crossed: list[int],
        recorded: set[int],
        expires_at: datetime,
        moment: datetime,
    ) -> None:
        """A row per newly crossed threshold, and a mail for the highest only (`FRD-409` FR-4).

        The rest are `superseded`: two mails at once about one counter say the same thing twice.
        So is a threshold below one already mailed — added mid-period, say — because the person
        has heard of a higher share already.
        """
        fresh = [threshold for threshold in crossed if threshold not in recorded]
        if not fresh:
            return
        mailed = max(fresh) if max(fresh) > max(recorded, default=0) else None
        for threshold in fresh:
            session.add(
                MailNotice(
                    created_at=moment,
                    kind=kind,
                    use_case=use_case,
                    subject_key=subject_key,
                    period_key=period_key,
                    threshold=threshold,
                    expires_at=expires_at,
                    outcome=OWED if threshold == mailed else SUPERSEDED,
                )
            )

    async def _owe_anomaly_mail(
        self, session: AsyncSession, use_cases: dict[str, UseCaseRead], moment: datetime
    ) -> None:
        """A notice per new finding of a use case whose administrators asked (`FRD-409` FR-6)."""
        asking = sorted(slug for slug, row in use_cases.items() if row.mail_admins_on_anomalies)
        if not asking:
            return
        rows = await session.execute(
            select(AnomalyEvent.id, AnomalyEvent.use_case, AnomalyEvent.created_at).where(
                AnomalyEvent.use_case.in_(asking),
                AnomalyEvent.created_at >= moment - ANOMALY_LOOKBACK,
            )
        )
        findings = rows.all()
        if not findings:
            return
        known = await session.execute(
            select(MailNotice.subject_key).where(
                MailNotice.kind == ANOMALY,
                MailNotice.subject_key.in_([finding.id for finding in findings]),
            )
        )
        noted = set(known.scalars())
        for finding in findings:
            if finding.id not in noted:
                session.add(
                    MailNotice(
                        created_at=moment,
                        kind=ANOMALY,
                        use_case=finding.use_case,
                        subject_key=finding.id,
                        period_key="",
                        threshold=0,
                        expires_at=_aware(finding.created_at) + ANOMALY_NEWS,
                        outcome=OWED,
                    )
                )

    # -- sending ---------------------------------------------------------------------------

    async def _send_what_is_owed(self, session: AsyncSession, moment: datetime) -> list[MailNotice]:
        """Every owed notice tried once, each committed as it is settled."""
        owed = await session.execute(
            select(MailNotice)
            .where(MailNotice.outcome == OWED)
            .order_by(MailNotice.created_at, MailNotice.id)
        )
        addresses = Addresses(self.directory)
        findings: dict[str, list[MailNotice]] = defaultdict(list)
        settled: list[MailNotice] = []
        for notice in owed.scalars().all():
            if _aware(notice.expires_at) <= moment:
                # Late is wrong: a budget mail about a period that has ended, or old news.
                notice.outcome = EXPIRED
            elif notice.kind == ANOMALY:
                findings[notice.use_case].append(notice)
                continue
            else:
                await self._send_budget_mail(session, notice, addresses, moment)
            await session.commit()
            settled.append(notice)
        for slug, notices in sorted(findings.items()):
            await self._send_anomaly_mail(session, slug, notices, addresses, moment)
            await session.commit()
            settled.extend(notices)
        return settled

    async def _send_budget_mail(
        self, session: AsyncSession, notice: MailNotice, addresses: Addresses, moment: datetime
    ) -> None:
        use_case = await session.get(UseCaseRead, notice.use_case)
        usage = await session.get(BudgetUsage, (notice.subject_key, notice.period_key))
        state: BudgetState | None = None
        if use_case is not None and use_case.deleted_at is None and usage is not None:
            budgets = await session.execute(
                select(BudgetRead).where(BudgetRead.use_case == notice.use_case)
            )
            state = self._state(
                notice.use_case,
                list(budgets.scalars()),
                usage,
                period_of(notice.period_key),
                moment,
            )
        if (
            use_case is None
            or state is None
            or state.share < notice.threshold
            or notice.threshold not in use_case.mail_thresholds
        ):
            # No longer true, or no longer asked for, when it is sent — the budget was raised,
            # lifted or removed, the threshold dropped, or the use case retired — so it is not said.
            notice.outcome = SUPERSEDED
            return
        state = replace(state, use_case_name=use_case.name)
        try:
            if state.scope == USE_CASE:
                recipients = await addresses.of_administrators(session, notice.use_case)
            else:
                person = notice.subject_key.removeprefix(Scope(notice.use_case, "").usage_key)
                found = await addresses.of_person(person)
                recipients = [found] if found is not None else []
        except DirectoryUnavailable as exc:
            self._failed([notice], f"the directory could not be asked: {exc}")
            return
        await self._deliver(
            [notice],
            recipients,
            lambda to: budget_mail(
                state,
                to=to,
                sender=self.sender,
                thresholds=use_case.mail_thresholds,
                zone=self.zone,
                currency=self.currency,
                enforced=self.enforced,
                console_url=self.console_url,
            ),
            moment,
        )

    async def _send_anomaly_mail(
        self,
        session: AsyncSession,
        slug: str,
        notices: list[MailNotice],
        addresses: Addresses,
        moment: datetime,
    ) -> None:
        """One mail per use case and round, listing every new finding (`FRD-409` FR-6)."""
        use_case = await session.get(UseCaseRead, slug)
        if (
            use_case is None
            or use_case.deleted_at is not None
            or not use_case.mail_admins_on_anomalies
        ):
            # Switched off, or the use case retired, since the finding was noted.
            for notice in notices:
                notice.outcome = SUPERSEDED
            return
        rows = await session.execute(
            select(AnomalyEvent).where(AnomalyEvent.id.in_([n.subject_key for n in notices]))
        )
        events = list(rows.scalars())
        if not events:
            for notice in notices:
                notice.outcome = EXPIRED
            return
        try:
            recipients = await addresses.of_administrators(session, slug)
        except DirectoryUnavailable as exc:
            self._failed(notices, f"the directory could not be asked: {exc}")
            return
        await self._deliver(
            notices,
            recipients,
            lambda to: anomaly_mail(
                events,
                use_case=slug,
                use_case_name=use_case.name,
                to=to,
                sender=self.sender,
                zone=self.zone,
                console_url=self.console_url,
            ),
            moment,
        )

    async def _deliver(
        self,
        notices: list[MailNotice],
        recipients: list[Recipient],
        compose: Callable[[Recipient], EmailMessage],
        moment: datetime,
    ) -> None:
        """One mail per recipient, so nobody reads the others' addresses and one refusal does not
        stop the rest. Any failure leaves every notice owed for another round."""
        first = notices[0]
        if not recipients:
            for notice in notices:
                notice.outcome = NO_RECIPIENT
            _log.warning(
                "mail_not_sent",
                notice=first.id,
                kind=first.kind,
                use_case=first.use_case,
                why="nobody to send it to has an address in the directory",
            )
            return
        try:
            for recipient in recipients:
                await self.relay.send(compose(recipient))
        except MailUndeliverable as exc:
            self._failed(notices, str(exc))
            return
        for notice in notices:
            notice.outcome = SENT
            notice.recipients = len(recipients)
            notice.sent_at = moment
        _log.info(
            "mail_sent",
            notice=first.id,
            notices=len(notices),
            kind=first.kind,
            use_case=first.use_case,
            recipients=len(recipients),
        )

    def _failed(self, notices: list[MailNotice], why: str) -> None:
        for notice in notices:
            notice.attempts += 1
            notice.last_error = why[:MAX_REASON]
            if notice.attempts >= MAX_ATTEMPTS:
                notice.outcome = FAILED
        first = notices[0]
        _log.warning(
            "mail_failed",
            notice=first.id,
            kind=first.kind,
            use_case=first.use_case,
            attempt=first.attempts,
            gave_up=first.outcome == FAILED,
            why=why[:MAX_REASON],
        )


def build_notifier(
    settings: GatewaySettings,
    sessionmaker: async_sessionmaker[AsyncSession],
    zone: tzinfo,
) -> MailNotifier | None:
    """The notifier this installation configured, or ``None`` where it sends no mail (`FRD-409`
    FR-9). Never a refusal to start: mail is a convenience, and an installation without a relay
    serves as it always did. What is missing is said once, here."""
    if not settings.mail_enabled:
        if settings.smtp_host.strip():
            _log.warning("mail_off", why="AIRA_SMTP_HOST is set, AIRA_MAIL_FROM is not")
        return None
    issuers = settings.issuers()
    directory = directory_for(
        issuers[0][0] if issuers else "",
        settings.directory_url,
        settings.directory_client_id,
        settings.directory_client_secret,
    )
    if directory is None:
        _log.warning(
            "mail_without_directory",
            why="no directory account (AIRA_DIRECTORY_CLIENT_ID) or no Keycloak issuer: "
            "no mail will find an address",
        )
    return MailNotifier(
        sessionmaker,
        relay=SmtpRelay(
            settings.smtp_host.strip(),
            settings.smtp_port,
            settings.smtp_security,
            settings.smtp_username,
            settings.smtp_password,
        ),
        sender=settings.mail_from.strip(),
        directory=directory,
        zone=zone,
        currency=settings.currency,
        enforced=settings.enforce_budgets,
        console_url=settings.console_url,
    )
