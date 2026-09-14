"""Who may read a stored prompt, and what happens when they do (`FRD-505`).

`ADR-0009` deferred this view until content could be redacted; `FRD-406` then declined its PII half
(`ADR-0016`) — names and customer numbers are what a payload is stored *for*. So access is decided
by role, and every read is recorded:

- **Global Administrator and IT Security** may read any stored payload.
- **IT Steuerung may not**: every figure, no content (the split `FRD-206` made).
- **A use-case administrator** sees the payloads of their own use case.
- **A use-case user** sees them too, unless the use case restricts members to their own requests.

"Not stored", "expired" and "you may not" are three different facts and are never collapsed into
one refusal.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from aira_common.access import GrantRole, strongest
from aira_common.permissions import Permission
from aira_gateway.auth.principal import Principal
from aira_gateway.db.models import RequestLog, UseCaseMemberRead, UseCaseRead


class PayloadRefusal(StrEnum):
    """Why a payload is not being shown. Never collapsed into one message."""

    #: The caller has no business with this use case at all.
    OUT_OF_SCOPE = "out_of_scope"
    #: An oversight role that sees figures and not content (IT Steuerung).
    NOT_A_CONTENT_ROLE = "not_a_content_role"
    #: A member restricted to their own requests, looking at somebody else's.
    OTHERS_REQUEST = "others_request"
    #: The use case has payload storage switched off (`FRD-404`).
    NOT_STORED = "not_stored"
    #: Stored once, and removed by retention since (`FRD-404`).
    EXPIRED = "expired"
    #: This request never had one — a refusal recorded before dispatch has nothing to show.
    NEVER_HAD_ONE = "never_had_one"


#: Refusals about **authority** (403); the rest are about the data being absent (200 with a
#: reason). "You may not" and "there is nothing" lead a reader to different conclusions.
AUTHORITY_REFUSALS = frozenset(
    {
        PayloadRefusal.OUT_OF_SCOPE,
        PayloadRefusal.NOT_A_CONTENT_ROLE,
        PayloadRefusal.OTHERS_REQUEST,
    }
)

MESSAGES = {
    PayloadRefusal.OUT_OF_SCOPE: "This request belongs to a use case you do not have access to.",
    PayloadRefusal.NOT_A_CONTENT_ROLE: (
        "Reading prompts and responses needs the permission to read stored content "
        "(payload.read_any), or membership of the use case that produced them. Seeing its "
        "figures does not include it."
    ),
    PayloadRefusal.OTHERS_REQUEST: (
        "This use case shows each member their own requests only. Its administrator can change "
        "that."
    ),
    PayloadRefusal.NOT_STORED: (
        "This use case does not store prompts and responses. Its administrator can turn storage "
        "on; it does not apply retroactively."
    ),
    PayloadRefusal.EXPIRED: (
        "The prompt and response were stored and have since passed this use case's retention "
        "period."
    ),
    PayloadRefusal.NEVER_HAD_ONE: (
        "This request never reached a model, so there is no prompt or response to show."
    ),
}


@dataclass(frozen=True, slots=True)
class PayloadVerdict:
    """Whether the payload may be shown, and — when it may not — precisely why."""

    allowed: bool
    refusal: PayloadRefusal | None = None
    #: The authority the read rests on, recorded with the access: `incident` and `member` are
    #: different grounds for the same act, and a review asks which one it was.
    ground: str = ""

    @property
    def is_authority_refusal(self) -> bool:
        return self.refusal in AUTHORITY_REFUSALS if self.refusal else False

    @property
    def message(self) -> str:
        return MESSAGES[self.refusal] if self.refusal else ""


# The key `use_case_members` is matched on. Its `subject` column holds a **username** (Management
# emits `username` on `membership.upserted`), so it is compared with `Principal.person` — never with
# an OIDC subject, which is a directory id.
def _member_key(principal: Principal) -> str | None:
    return principal.person


def _row_person(subject: str | None, username: str | None) -> str | None:
    """Whose request a stored row is: :func:`aira_gateway.scopes.person` asked of a row.

    `RequestLog.username` (`FRD-606`) is NULL on rows written before it existed, hence the subject.
    """
    return username or subject


def is_own_request(principal: Principal, row: RequestLog) -> bool:
    """Whether ``row`` is **this caller's own** request — whichever credential made it.

    The raw subject still matches (older rows carry no name), and so does the person's name, so a
    member reading API-key traffic through the console sees their own requests. Nothing compares
    one person's name with another's subject; that rests on a directory in which nobody can rename
    themselves onto a colleague (`docs/INTEGRATIONS.md` §2).
    """
    if row.subject == principal.subject:
        return True
    person = principal.person
    return bool(person and _row_person(row.subject, row.username) == person)


def own_requests(principal: Principal) -> ColumnElement[bool]:
    """:func:`is_own_request` as a filter, for the list that asks it of every row.

    The two forms must select the same rows (`test_own_requests_are_the_persons.py`): a predicate
    and a query that disagree refuse a payload on a screen that lists the row.
    """
    person = principal.person
    own: list[ColumnElement[bool]] = [RequestLog.subject == principal.subject]
    if person:
        # Written as indexable disjuncts rather than `coalesce(username, subject) = person`, which
        # selects the same rows and reaches neither index on the largest table here. The name
        # clause is unconditional: for an API key the subject already *is* the name, and
        # `is_own_request` applies the comparison regardless.
        own.append(RequestLog.username == person)
    if person and person != principal.subject:
        own.append(
            and_(
                # `IS NULL` **or** empty, because `_row_person` reads the name for its truth and
                # SQL does not. Only where the person is not the subject — otherwise the first
                # clause already selects these rows.
                or_(RequestLog.username.is_(None), RequestLog.username == ""),
                RequestLog.subject == person,
            )
        )
    return or_(*own)


async def grant_role_in(session: AsyncSession, principal: Principal, use_case: str) -> str | None:
    """This caller's role **inside** ``use_case``, or ``None`` if they hold no grant there.

    A query rather than a token claim: object-level authority lives in the grants (`FRD-206`), and
    this runs on console endpoints, never on the request path.
    """
    if use_case not in principal.use_cases:
        return None
    row = (
        await session.execute(
            select(UseCaseMemberRead.role).where(
                UseCaseMemberRead.use_case_slug == use_case,
                UseCaseMemberRead.subject == _member_key(principal),
            )
        )
    ).scalar_one_or_none()
    # Both routes a grant arrives by: a member row, and the role the resolver put on the principal
    # (a *group* grant writes no member row). `strongest` so read order cannot decide access; with
    # neither — membership by the `/use-cases/<slug>` convention (`FRD-102`) — it answers `user`.
    return strongest([role for role in (dict(principal.grants).get(use_case), row) if role])


async def may_read_payload(
    session: AsyncSession, principal: Principal, row: RequestLog
) -> PayloadVerdict:
    """The whole decision, in one place and in one order.

    Authority first, then whether the data exists: telling somebody who may not look that a payload
    *would* have been there is itself a disclosure.
    """
    ground = await _authority(session, principal, row)
    if isinstance(ground, PayloadRefusal):
        return PayloadVerdict(allowed=False, refusal=ground)

    use_case = (
        await session.execute(select(UseCaseRead).where(UseCaseRead.slug == row.use_case))
    ).scalar_one_or_none()

    if row.request_payload is None and row.response_payload is None:
        if use_case is not None and not use_case.store_payloads:
            return PayloadVerdict(allowed=False, refusal=PayloadRefusal.NOT_STORED)
        if row.status is not None and 200 <= row.status < 300:
            # Served, so a payload existed and retention removed it (`FRD-404`).
            return PayloadVerdict(allowed=False, refusal=PayloadRefusal.EXPIRED)
        return PayloadVerdict(allowed=False, refusal=PayloadRefusal.NEVER_HAD_ONE)

    return PayloadVerdict(allowed=True, ground=ground)


async def _authority(
    session: AsyncSession, principal: Principal, row: RequestLog
) -> str | PayloadRefusal:
    """The ground on which this caller may read content, or the reason they may not."""
    if principal.allows(Permission.PAYLOAD_READ_ANY):
        return "incident"

    if not row.use_case or row.use_case not in principal.use_cases:
        # An oversight role sees this request's figures on the same screen, so it is told what it
        # *is* rather than "not found".
        if principal.allows(Permission.TRACE_READ_ALL):
            return PayloadRefusal.NOT_A_CONTENT_ROLE
        return PayloadRefusal.OUT_OF_SCOPE

    role = await grant_role_in(session, principal, row.use_case)
    if role == GrantRole.ADMIN.value:
        return "use_case_admin"

    use_case = (
        await session.execute(select(UseCaseRead).where(UseCaseRead.slug == row.use_case))
    ).scalar_one_or_none()
    restricted = bool(use_case is not None and use_case.restrict_members_to_own_requests)
    if restricted and not is_own_request(principal, row):
        return PayloadRefusal.OTHERS_REQUEST
    return "use_case_member"


def payload_body(row: RequestLog) -> dict[str, Any]:
    """What is handed to the reader — an **allow-list**, so a column added tomorrow does not appear
    in a content view because nobody remembered to exclude it."""
    return {
        "id": row.id,
        "request": row.request_payload,
        "response": row.response_payload,
    }


async def restricted_use_cases(session: AsyncSession, principal: Principal) -> list[str]:
    """Use cases in which this caller may see **only their own** requests.

    The list view needs this as much as the payload view: a visible row tells a user who else is
    calling, how often and at what cost. An incident role is never restricted, nor is a use-case
    administrator inside their own use case; the rest is decided per use case.
    """
    if principal.allows(Permission.INCIDENT_INVESTIGATE) or not principal.use_cases:
        return []
    slugs = list(principal.use_cases)
    restricted_slugs = {
        str(row)
        for row in (
            await session.execute(
                select(UseCaseRead.slug).where(
                    UseCaseRead.slug.in_(slugs),
                    UseCaseRead.restrict_members_to_own_requests.is_(True),
                )
            )
        ).scalars()
    }
    if not restricted_slugs:
        return []
    administered = {
        str(row)
        for row in (
            await session.execute(
                select(UseCaseMemberRead.use_case_slug).where(
                    UseCaseMemberRead.subject == _member_key(principal),
                    UseCaseMemberRead.use_case_slug.in_(list(restricted_slugs)),
                    UseCaseMemberRead.role == GrantRole.ADMIN.value,
                )
            )
        ).scalars()
    }
    # And administrators by *group* grant, which writes no member row (see `grant_role_in`).
    administered |= {slug for slug, role in principal.grants if role == GrantRole.ADMIN.value}
    return sorted(restricted_slugs - administered)
