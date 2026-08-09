"""Who may read a stored prompt, and what happens when they do (`FRD-505`).

`ADR-0009` deferred this view, and said why: browsing individual requests shows stored prompts to
people who are deliberately not members of the use case that produced them, and content redaction
(`FRD-406`) was what would make it safe. `FRD-406` then delivered its **credential** half and
declined its PII half on purpose — names and customer numbers are what a payload is stored *for*,
and a redactor that mangles them ends with storage switched off entirely.

So the deferral cannot be resolved by a redactor. It is resolved by an **owner decision**
(2026-08-09), and this module is where that decision lives:

- **Global Administrator and IT Security** may read any stored payload, and **every read is
  recorded** — who, which request, when, under which role. That record is what makes the access
  reviewable afterwards, which is the whole argument for granting it at all.
- **IT Steuerung may not.** It sees every figure about every use case and no content: *visibility
  and content are different answers*, the same split `FRD-206` had to make between seeing a use
  case and administering one.
- **A use-case administrator** sees the payloads of their own use case.
- **A use-case user** sees them too, unless that use case has been set to restrict members to
  their own requests — a switch its administrator owns.

Three refusals are **not** the same as one, and this module keeps them apart. "This use case does
not store payloads", "it stored them and they have since expired", and "you may not read this" are
three different facts, and a screen that renders one message for all three teaches its reader to
distrust the view: `FRD-502` had to make the same distinction between "nothing happened" and "you
see nothing".
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aira_common.access import GrantRole
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


#: Refusals that are about **authority**. The rest are about the data being absent, and the
#: difference decides the status code: 403 for the first group, 200-with-a-reason for the second,
#: because "you may not" and "there is nothing" are not the same answer and a reader who is told
#: the wrong one draws the wrong conclusion about the system.
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
        "Prompts and responses are available to IT Security, Global Administrators, and the "
        "members of the use case that produced them. Oversight roles see the figures."
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
    #: The authority the read rests on, recorded with the access. `incident` and `member` are
    #: different grounds for the same act, and a review asks which one it was.
    ground: str = ""

    @property
    def is_authority_refusal(self) -> bool:
        return self.refusal in AUTHORITY_REFUSALS if self.refusal else False

    @property
    def message(self) -> str:
        return MESSAGES[self.refusal] if self.refusal else ""


async def grant_role_in(session: AsyncSession, principal: Principal, use_case: str) -> str | None:
    """This caller's role **inside** ``use_case``, or ``None`` if they hold no grant there.

    Reads `use_case_members`, which the consumer has been maintaining since `FRD-204` and which
    nothing had ever read — the read-model half of the pattern this repository keeps finding: two
    correct halves and no wire between them.

    Deliberately a query and not a token claim. Object-level authority is not in the token
    (`FRD-206` established that for the console), it is in the grants — and this runs on a console
    endpoint, never on the request path, so a lookup is affordable here in a way it would not be
    in front of a model call.
    """
    if use_case not in principal.use_cases:
        return None
    row = (
        await session.execute(
            select(UseCaseMemberRead.role).where(
                UseCaseMemberRead.use_case_slug == use_case,
                UseCaseMemberRead.subject == principal.subject,
            )
        )
    ).scalar_one_or_none()
    # Membership by the `/use-cases/<slug>` group convention carries no row and no role, and it is
    # membership all the same (`FRD-102`). It grants the lesser of the two, because a grant that
    # was never written down cannot be read as the stronger one.
    return str(row) if row else GrantRole.USER.value


async def may_read_payload(
    session: AsyncSession, principal: Principal, row: RequestLog
) -> PayloadVerdict:
    """The whole decision, in one place and in one order.

    Authority first, then whether the data exists. That order matters: telling somebody who may not
    look that a payload *would* have been there is itself a disclosure about a use case they have
    no access to.
    """
    ground = await _authority(session, principal, row)
    if isinstance(ground, PayloadRefusal):
        return PayloadVerdict(allowed=False, refusal=ground)

    use_case = (
        await session.execute(select(UseCaseRead).where(UseCaseRead.slug == row.use_case))
    ).scalar_one_or_none()

    if row.request_payload is None and row.response_payload is None:
        # Three ways to have nothing, and they are three different facts about the installation.
        if use_case is not None and not use_case.store_payloads:
            return PayloadVerdict(allowed=False, refusal=PayloadRefusal.NOT_STORED)
        if row.status is not None and 200 <= row.status < 300:
            # It was served, so a payload existed and something removed it — retention is the only
            # thing that does (`FRD-404`).
            return PayloadVerdict(allowed=False, refusal=PayloadRefusal.EXPIRED)
        return PayloadVerdict(allowed=False, refusal=PayloadRefusal.NEVER_HAD_ONE)

    return PayloadVerdict(allowed=True, ground=ground)


async def _authority(
    session: AsyncSession, principal: Principal, row: RequestLog
) -> str | PayloadRefusal:
    """The ground on which this caller may read content, or the reason they may not."""
    if principal.may_act_on_incidents:
        return "incident"

    if not row.use_case or row.use_case not in principal.use_cases:
        # An oversight role that is not an incident role lands here and is told what it *is*
        # rather than "not found": it can see this request's figures on the very same screen, so
        # "there is no such request" would be a lie it could check.
        if principal.is_oversight:
            return PayloadRefusal.NOT_A_CONTENT_ROLE
        return PayloadRefusal.OUT_OF_SCOPE

    role = await grant_role_in(session, principal, row.use_case)
    if role == GrantRole.ADMIN.value:
        return "use_case_admin"

    use_case = (
        await session.execute(select(UseCaseRead).where(UseCaseRead.slug == row.use_case))
    ).scalar_one_or_none()
    restricted = bool(use_case is not None and use_case.restrict_members_to_own_requests)
    if restricted and row.subject != principal.subject:
        return PayloadRefusal.OTHERS_REQUEST
    return "use_case_member"


def payload_body(row: RequestLog) -> dict[str, Any]:
    """What is handed to the reader.

    An **allow-list**, like every other field list on this path: the row carries more than a prompt,
    and a column added tomorrow must not appear in a content view because nobody remembered to
    exclude it.
    """
    return {
        "id": row.id,
        "request": row.request_payload,
        "response": row.response_payload,
    }


async def restricted_use_cases(session: AsyncSession, principal: Principal) -> list[str]:
    """Use cases in which this caller may see **only their own** requests.

    The list view needs this as much as the payload view does. Restricting the content while
    leaving the row visible would tell a user exactly who else is calling, how often and at what
    cost — the interesting half of what the restriction exists to withhold — and would read as a
    broken feature rather than a boundary.

    An incident role is never restricted; a use-case administrator is never restricted inside their
    own use case. Everything else is decided per use case, because one caller can be an
    administrator of one and a user of another.
    """
    if principal.may_act_on_incidents or not principal.use_cases:
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
                    UseCaseMemberRead.subject == principal.subject,
                    UseCaseMemberRead.use_case_slug.in_(list(restricted_slugs)),
                    UseCaseMemberRead.role == GrantRole.ADMIN.value,
                )
            )
        ).scalars()
    }
    return sorted(restricted_slugs - administered)
