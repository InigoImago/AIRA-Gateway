"""Carrying out a decision to stop traffic (`FRD-503`).

Read on every request and written a handful of times a week, so this is a **cache** problem rather
than a shared-state one — see `FRD-503` §4.1, which amends `ADR-0014` §2 on exactly this point. The
authority is Postgres, which the gateway cannot serve a request without anyway; a suspension held
only in Redis would disappear when Redis did, and `FRD-405` already settled that the moment a
control stops working is the worst moment to stop applying it.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_common.anomalies import RuleAction, RuleTarget
from aira_common.logging import get_logger
from aira_gateway.db.models import AccessSuspension

_log = get_logger(__name__)

#: How long a loaded set of suspensions is trusted. A lift takes up to this long to reach every
#: instance, and for a control that *removes* a restriction, being slightly late is the harmless
#: direction. Applying one is not delayed: the rule that creates it writes and the next reload sees
#: it, which is the same few seconds.
CACHE_TTL_SECONDS = 5.0


class Suspended(Exception):
    """Raised at the pre-dispatch gate when this caller's traffic is stopped."""

    def __init__(self, message: str, retry_after: str, reason: str) -> None:
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Throttle:
    """A rate a suspended caller is held to, rather than being stopped outright."""

    label: str
    key: str
    limit_rpm: int


class SuspensionService:
    """Answers "is this caller stopped, and how" from a short-lived cache over Postgres."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        enforce: bool = True,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._enforce = enforce
        self._clock = clock
        self._cached: list[AccessSuspension] = []
        self._loaded_at: float | None = None

    def invalidate(self) -> None:
        """Drop the cache — used after writing one, so the writer sees its own decision."""
        self._loaded_at = None

    async def active(self) -> list[AccessSuspension]:
        now = self._clock()
        if self._loaded_at is None or now - self._loaded_at >= CACHE_TTL_SECONDS:
            async with self._sessionmaker() as session:
                self._cached = await _load(session)
            self._loaded_at = now
        moment = datetime.now(UTC)
        # Expiry is applied on read, not by a sweeper: a row that has run out must stop refusing
        # people the moment it does, without waiting for anything to tidy up.
        return [row for row in self._cached if _still_applies(row, moment)]

    async def check(
        self,
        use_case: str | None,
        subject: str | None,
        credential: str | None,
        person: str | None = None,
    ) -> list[Throttle]:
        """Raise :class:`Suspended` if this caller is blocked; return any throttles that apply.

        ``person`` is the name the same human is known by whichever credential they used
        (:func:`aira_gateway.scopes.person`), and it is here because **a kill switch aimed at a
        person was stopping half of them**. The two credentials answer "who is this" in different
        alphabets — an OIDC token's subject is a directory id, an API key's is its owner's
        username — so a suspension typed from a trace row stopped exactly the kind of credential
        that row happened to come from. Measured on 2026-08-30: `target_value: "alice"` blocked
        her key and served her browser.

        A **credential** target still matches the credential alone, and that separation is the
        point of having three targets: *"block this leaked key"* must not stop the person holding
        it, and *"stop this person"* must not depend on which of their credentials they reach for.
        """
        if not self._enforce:
            return []
        matching = [
            row
            for row in await self.active()
            if _matches(
                row,
                use_case=use_case,
                subject=subject,
                credential=credential,
                person=person,
            )
        ]
        blocks = [row for row in matching if row.action == RuleAction.BLOCK.value]
        if blocks:
            row = blocks[0]
            retry_after = _retry_after(row)
            _log.info(
                "request_suspended",
                use_case=use_case,
                subject=subject,
                target=row.target,
                author=row.author,
            )
            raise Suspended(
                # The message names the author, and it is now a **name**. It was
                # `user:{principal.subject}` — a directory id for a console user, which answers the
                # caller's first question ("who did this, so I can ask them") with a string nobody
                # can look anybody up by. Every other record of a person's act here already keeps
                # the name: `granted_by`, `deleted_by`, `issued_by`, `RequestLog.username`.
                f"Access for this {row.target.replace('_', ' ')} is suspended ({row.author}).",
                retry_after=retry_after,
                reason=row.reason or row.author,
            )
        return [
            Throttle(
                label=f"suspension {row.target}:{row.target_value}",
                key=f"suspension:{row.id}",
                limit_rpm=row.throttle_rpm or 1,
            )
            for row in matching
            if row.action == RuleAction.THROTTLE.value and row.throttle_rpm
        ]


async def _load(session: AsyncSession) -> list[AccessSuspension]:
    stmt = select(AccessSuspension).where(AccessSuspension.lifted_at.is_(None))
    return list((await session.execute(stmt)).scalars().all())


def _still_applies(row: AccessSuspension, moment: datetime) -> bool:
    if row.lifted_at is not None:
        return False
    if row.expires_at is None:
        return True
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=UTC)
    return expires > moment


def _matches(
    row: AccessSuspension,
    *,
    use_case: str | None,
    subject: str | None,
    credential: str | None,
    person: str | None = None,
) -> bool:
    if row.use_case is not None and row.use_case != use_case:
        return False
    target = RuleTarget(row.target)
    if target is RuleTarget.SUBJECT:
        # **Either alphabet.** The same widening `payloads.own_requests` and the findings list
        # already make, applied to the control that actually stops traffic — an identity read in
        # two alphabets has as many readers as there are comparisons, and this was the one where a
        # miss meant a caller kept being served (`LESSONS.md` §1).
        return row.target_value in {name for name in (subject, person) if name}
    if target is RuleTarget.CREDENTIAL:
        return credential is not None and row.target_value == credential
    return use_case is not None and row.target_value == use_case


def _retry_after(row: AccessSuspension) -> str:
    """Seconds until it lifts. A suspension with no expiry says a minute rather than forever —
    a client told to come back in a week simply stops, and a person will lift this one."""
    if row.expires_at is None:
        return "60"
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=UTC)
    seconds = int((expires - datetime.now(UTC)).total_seconds())
    return str(max(seconds, 1))


def suspension_from_rule(
    *,
    rule_name: str,
    use_case: str | None,
    target: str,
    target_value: str,
    action: str,
    minutes: int,
    throttle_rpm: int | None,
    detail: str,
    now: datetime,
) -> AccessSuspension:
    """The decision a fired rule writes. Always with an expiry — a rule cannot lift its own."""
    return AccessSuspension(
        use_case=use_case,
        target=target,
        target_value=target_value,
        action=action,
        throttle_rpm=throttle_rpm,
        expires_at=now + timedelta(minutes=max(minutes, 1)),
        author=f"rule:{rule_name}",
        reason=detail[:500],
    )


def as_dict(row: AccessSuspension) -> dict[str, object]:
    """The wire shape. Written once so the list endpoint and the create response agree."""
    return {
        "id": row.id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "use_case": row.use_case,
        "target": row.target,
        "target_value": row.target_value,
        "action": row.action,
        "throttle_rpm": row.throttle_rpm,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "author": row.author,
        "reason": row.reason,
        "lifted_at": row.lifted_at.isoformat() if row.lifted_at else None,
        "lifted_by": row.lifted_by,
    }


__all__: Sequence[str] = [
    "CACHE_TTL_SECONDS",
    "AccessSuspension",
    "Suspended",
    "SuspensionService",
    "Throttle",
    "as_dict",
    "suspension_from_rule",
]
