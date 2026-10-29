"""Who a mail goes to, and at which address (`FRD-409` FR-3, FR-7).

The address is read from the directory when the mail is sent, by exact username, and kept only for
the round: a profile's address changes, and a copy kept here would go on writing to the old one.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aira_common.access import GrantRole
from aira_common.directory import DirectoryUnavailable, KeycloakDirectory
from aira_gateway.db.models import UseCaseGroupRead, UseCaseMemberRead

_log = structlog.get_logger(__name__)

#: The most members of one administering group a mail goes to. Administrators are a handful of
#: people; a grant to a group of thousands is a mistake a mail to all of them would compound, so
#: the log says when a group was cut short.
MAX_GROUP_RECIPIENTS = 200


@dataclass(frozen=True, slots=True)
class Recipient:
    """One person a mail goes to."""

    username: str
    address: str


@dataclass
class Addresses:
    """The directory's addresses, each asked at most once per round."""

    directory: KeycloakDirectory | None
    _known: dict[str, str] = field(default_factory=dict)

    async def of_person(self, username: str) -> Recipient | None:
        """``username``'s address, or ``None`` where their profile has none.

        Raises :class:`DirectoryUnavailable` when there is no directory to ask or asking failed:
        "this person has no address" is final, "nobody could look" is worth another round.
        """
        if username not in self._known:
            entry = await asyncio.to_thread(self._directory().find_user, username)
            self._known[username] = entry.detail.strip() if entry is not None else ""
        address = self._known[username]
        return Recipient(username, address) if address else None

    async def of_administrators(self, session: AsyncSession, use_case: str) -> list[Recipient]:
        """Everybody administering ``use_case``: granted the role by name, or a member of a group
        granted it. Each person once, whichever ways they hold it."""
        named = await session.execute(
            select(UseCaseMemberRead.subject).where(
                UseCaseMemberRead.use_case_slug == use_case,
                UseCaseMemberRead.role == GrantRole.ADMIN.value,
            )
        )
        groups = await session.execute(
            select(UseCaseGroupRead.group_path).where(
                UseCaseGroupRead.use_case_slug == use_case,
                UseCaseGroupRead.role == GrantRole.ADMIN.value,
            )
        )
        found: dict[str, Recipient] = {}
        for username in sorted(set(named.scalars())):
            recipient = await self.of_person(username)
            if recipient is not None:
                found[recipient.username] = recipient
        for path in sorted(set(groups.scalars())):
            members = await asyncio.to_thread(
                self._directory().group_members, path, MAX_GROUP_RECIPIENTS + 1
            )
            if len(members) > MAX_GROUP_RECIPIENTS:
                _log.warning("mail_group_cut_short", use_case=use_case, limit=MAX_GROUP_RECIPIENTS)
            for member in members[:MAX_GROUP_RECIPIENTS]:
                address = member.detail.strip()
                if address:
                    self._known.setdefault(member.id, address)
                    found.setdefault(member.id, Recipient(member.id, address))
        return [found[name] for name in sorted(found)]

    def _directory(self) -> KeycloakDirectory:
        if self.directory is None:
            raise DirectoryUnavailable("no directory account is configured")
        return self.directory
