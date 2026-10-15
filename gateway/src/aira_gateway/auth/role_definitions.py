"""What each role may do, read from the `roles` read model (`FRD-614` FR-8).

The gateway never asks Management on the request path (`FRD-204`): stored roles arrive over
`aira.roles`. Read on every OIDC request and written rarely, so the whole table is cached for a few
seconds — the `GroupGrantResolver` shape.

**Degradation refuses rather than admits.** When the table cannot be read, stored roles — IT
Steuerung and every role an installation defined — confer nothing. The Global Administrator and IT
Security come from code and are unaffected, so the people who may repair the installation are not
locked out of it.
"""

from __future__ import annotations

import asyncio
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_common.logging import get_logger
from aira_common.permissions import RoleDefinition, builtin_roles, parse_permissions
from aira_common.roles import Role
from aira_gateway.db.models import RoleRead

#: How long a loaded table is trusted. A withdrawn permission holds within this, without a new
#: token (`FRD-614` FR-8).
CACHE_TTL_SECONDS = 5.0

#: How long a failed read's answer — the fixed roles only — stands before the table is read again;
#: the `GroupGrantResolver` reasoning, for the same shape of cache.
RETRY_AFTER_FAILURE_SECONDS = 1.0

#: The names a stored role may not take: a row calling itself `global-admin` is not that role.
_BUILTIN_SLUGS = frozenset(str(role) for role in Role)

_log = get_logger("aira_gateway.roles")


class RoleResolver:
    """Answers "which roles exist, and what may each do" for :func:`effective`."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        mapping: dict[Role, tuple[str, ...]],
        *,
        strict_data: bool = True,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._mapping = mapping
        #: `AIRA_STRICT_DATA_MODE` (`FRD-627`): stored roles cannot hold what sees a person.
        self._strict_data = strict_data
        self._definitions = self._fixed_only()
        self._loaded_at = 0.0
        #: False until the first successful load, and again after a failed one.
        self._ready = False
        #: Until when a failed read's answer stands (`RETRY_AFTER_FAILURE_SECONDS`).
        self._retry_at = 0.0
        #: One reload at a time (`GroupGrantResolver._reload`).
        self._reload = asyncio.Lock()

    def _settled(self, now: float) -> bool:
        fresh = self._ready and now - self._loaded_at < CACHE_TTL_SECONDS
        return fresh or now < self._retry_at

    def _fixed_only(self) -> tuple[RoleDefinition, ...]:
        """What holds while nothing stored can be read: IT Steuerung confers nothing."""
        return builtin_roles(self._mapping, it_steuerung=frozenset(), strict_data=self._strict_data)

    async def definitions(self) -> tuple[RoleDefinition, ...]:
        if self._settled(time.monotonic()):
            return self._definitions
        async with self._reload:
            now = time.monotonic()
            if not self._settled(now):
                await self._load(now)
        return self._definitions

    async def _load(self, now: float) -> None:
        try:
            async with self._sessionmaker() as session:
                rows = (
                    await session.execute(
                        select(
                            RoleRead.slug,
                            RoleRead.label,
                            RoleRead.group_path,
                            RoleRead.permissions,
                            RoleRead.builtin,
                        )
                    )
                ).all()
        except Exception as exc:  # the database is not reachable, or the table is not there yet
            # Not raising: the fixed roles still decide, and refusing everybody would turn a
            # config-distribution problem into an outage. **Dropped, not served stale**: a stored
            # permission the table can no longer back is not evidence.
            _log.warning("roles_unavailable", error=str(exc), error_type=type(exc).__name__)
            self._definitions = self._fixed_only()
            self._ready = False
            self._retry_at = now + RETRY_AFTER_FAILURE_SECONDS
            return

        steuerung = next(
            (row for row in rows if row.slug == str(Role.IT_STEUERUNG) and row.builtin), None
        )
        strict = self._strict_data
        stored_set = (
            parse_permissions(steuerung.permissions or [], strict_data=strict)
            if steuerung
            else None
        )
        custom = tuple(
            RoleDefinition(
                slug=row.slug,
                label=row.label or row.slug,
                group_paths=(row.group_path,) if row.group_path else (),
                permissions=parse_permissions(row.permissions or [], strict_data=strict),
            )
            for row in rows
            if not row.builtin and row.slug not in _BUILTIN_SLUGS
        )
        self._definitions = builtin_roles(self._mapping, stored_set, strict_data=strict) + custom
        self._loaded_at = now
        self._ready = True
