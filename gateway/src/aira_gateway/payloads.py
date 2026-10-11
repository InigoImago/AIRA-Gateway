"""Who may read a stored prompt, and what happens when they do (`FRD-505`).

`ADR-0009` deferred this view until content could be redacted; `FRD-406` then declined its PII half
(`ADR-0016`) — names and customer numbers are what a payload is stored *for*. So access is decided
by role, and every read is recorded:

- **Global Administrator and IT Security** may read any stored payload.
- **IT Steuerung may not**: every figure, no content (the split `FRD-206` made).
- **A use-case administrator** sees the payloads of their own use case.
- **A use-case user** sees them too, unless the use case restricts members to their own requests.

...and a use case may take the last two away: `PayloadVisibility.OVERSIGHT_ONLY` leaves content to
the two installation roles above and to nobody inside the use case, its administrators included
(`FRD-622` FR-1). Three values, one question; `store_payloads` answers the different question of
whether anything is written at all.

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


class PayloadVisibility(StrEnum):
    """Who, **inside a use case**, may open a stored prompt and answer (`FRD-622` FR-1).

    One value rather than a pair of booleans: it is one question with three answers, and two
    booleans also spell combinations that mean nothing. Whether anything is stored at all is a
    different question (`store_payloads`) — it decides what is *written*, and retention reads it as
    a period of zero.

    Content only, in every value. Which rows a member sees is what `OWN_REQUESTS_ONLY` decides,
    and the figures a use case needs to run itself — status, latency, cost — are unaffected.
    """

    #: Everybody in the use case reads every stored prompt. The behaviour that existed first, and
    #: still the default: a restriction is imposed, never assumed.
    EVERY_MEMBER = "members"
    #: Administrators of the use case read all of them; everybody else only their own requests.
    OWN_REQUESTS_ONLY = "own"
    #: **Nobody in the use case**, not even its administrators. Only the installation roles that
    #: may read any content — Global Administrator and IT Security (`payload.read_any`).
    OVERSIGHT_ONLY = "oversight"


#: The values this gateway understands. A value outside it came from a newer Management.
VISIBILITIES = frozenset(value.value for value in PayloadVisibility)


class PayloadRefusal(StrEnum):
    """Why a payload is not being shown. Never collapsed into one message."""

    #: The caller has no business with this use case at all.
    OUT_OF_SCOPE = "out_of_scope"
    #: An oversight role that sees figures and not content (IT Steuerung).
    NOT_A_CONTENT_ROLE = "not_a_content_role"
    #: A member restricted to their own requests, looking at somebody else's.
    OTHERS_REQUEST = "others_request"
    #: The use case leaves content to the installation's oversight roles — nobody inside it reads
    #: a prompt, its administrators included.
    OVERSIGHT_ONLY = "oversight_only"
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
        PayloadRefusal.OVERSIGHT_ONLY,
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
    PayloadRefusal.OVERSIGHT_ONLY: (
        "This use case keeps prompts and responses for oversight only: inside the use case nobody "
        "reads them, including its administrators. A Global Administrator or IT Security can, and "
        "every such read is recorded. Its administrator chose this and can change it."
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

    use_case = (
        await session.execute(select(UseCaseRead).where(UseCaseRead.slug == row.use_case))
    ).scalar_one_or_none()
    visibility = use_case.payload_visibility if use_case is not None else None

    # **Before the administrator check**, which is the whole of this mode: the use case has left
    # its content to the installation's oversight roles, and an administrator of the use case is
    # not one of them. Anybody who *is* one has already returned above, on `payload.read_any`.
    if visibility == PayloadVisibility.OVERSIGHT_ONLY:
        return PayloadRefusal.OVERSIGHT_ONLY

    role = await grant_role_in(session, principal, row.use_case)
    if role == GrantRole.ADMIN.value:
        return "use_case_admin"

    if visibility == PayloadVisibility.OWN_REQUESTS_ONLY and not is_own_request(principal, row):
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

    **`oversight` is not here, on purpose.** It hides the prompt and the answer, not the row: a use
    case still has to be able to see its own traffic — what failed, what it cost, who is calling
    too much — and taking that away would answer a question nobody asked (`FRD-622` FR-1c). Only
    `own` restricts which rows a member sees.
    """
    return await _narrowed(
        session,
        principal,
        (PayloadVisibility.OWN_REQUESTS_ONLY,),
        administrators_exempt=True,
    )


async def people_restricted_use_cases(session: AsyncSession, principal: Principal) -> list[str]:
    """Use cases in which this caller may see **only their own** figures per person (`FRD-603`).

    The second question a works council asks, and for two months the answer was *"it does not
    apply"*: requests, tokens, cost and response times **per person** were visible to every member
    of a use case whatever its content setting said, which the privacy notice stated as a gap
    rather than a decision. A screen that withholds a colleague's prompt and prints how many
    requests they made at what latency has not withheld much.

    So the ladder is the content's, one dimension lower — it is about a *split by person*, never
    about totals:

    - `members`: everybody in the use case sees everybody's figures, as before;
    - `own`: a member sees their own, an **administrator** of the use case sees all of them, which
      is what that mode already means for rows and content;
    - `oversight`: **nobody inside the use case** sees a split by person, its administrators
      included — the same people who may not read a prompt there.

    What stays in every mode: the use case's **totals**, every other breakdown, and each person's
    own figures. A use case has to be able to see what it costs and what fails, and a reader must
    be able to see what they themselves used (`FRD-626`). Roles that may read every figure
    (`report.read_all`) are not narrowed at all — the oversight the mode leaves content to.
    """
    # Two walks, because the **exemption differs by mode** and that is the whole distinction: on
    # `own` an administrator of the use case sees everybody's figures, as they see everybody's rows
    # and everybody's prompts; on `oversight` they do not, because that mode is defined by its own
    # administrators being inside it. One call with both visibilities would have to pick one
    # exemption for both, and either choice is wrong for one of them.
    own_only = await _narrowed(
        session, principal, (PayloadVisibility.OWN_REQUESTS_ONLY,), administrators_exempt=True
    )
    oversight = await _narrowed(
        session, principal, (PayloadVisibility.OVERSIGHT_ONLY,), administrators_exempt=False
    )
    return sorted(set(own_only) | set(oversight))


async def _narrowed(
    session: AsyncSession,
    principal: Principal,
    visibilities: tuple[PayloadVisibility, ...],
    *,
    administrators_exempt: bool,
) -> list[str]:
    """The use cases among this caller's own whose visibility is one of ``visibilities``.

    One walk for both questions above, because the *exemptions* are the part that goes wrong when
    copied: an incident role is never narrowed, a use-case administrator is an administrator both
    by member row and by **group grant** (which writes no member row), and a caller with no
    memberships has nothing to narrow. Two copies of that is one copy that will keep the group
    grant and one that will forget it.

    ``administrators_exempt`` is the only difference, and it is the one `oversight` turns on: that
    mode is defined by the use case's own administrators being inside it rather than above it.
    """
    if principal.allows(Permission.INCIDENT_INVESTIGATE) or not principal.use_cases:
        return []
    slugs = list(principal.use_cases)
    narrowed = {
        str(row)
        for row in (
            await session.execute(
                select(UseCaseRead.slug).where(
                    UseCaseRead.slug.in_(slugs),
                    UseCaseRead.payload_visibility.in_([value.value for value in visibilities]),
                )
            )
        ).scalars()
    }
    if not narrowed or not administrators_exempt:
        return sorted(narrowed)
    administered = {
        str(row)
        for row in (
            await session.execute(
                select(UseCaseMemberRead.use_case_slug).where(
                    UseCaseMemberRead.subject == _member_key(principal),
                    UseCaseMemberRead.use_case_slug.in_(list(narrowed)),
                    UseCaseMemberRead.role == GrantRole.ADMIN.value,
                )
            )
        ).scalars()
    }
    # And administrators by *group* grant, which writes no member row (see `grant_role_in`).
    administered |= {slug for slug, role in principal.grants if role == GrantRole.ADMIN.value}
    return sorted(narrowed - administered)
