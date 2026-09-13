"""Resolving a caller's Keycloak groups and name into the use cases they reach (`FRD-209`).

The gateway never asks Management on the request path (`FRD-204`): grants arrive over Kafka into
`use_case_groups` and `use_case_members`. Read on every OIDC request and written rarely, so the
whole of both tables is cached in-process for a few seconds — the `FRD-503` §4.1 shape. A
per-caller cache would hold one entry per person and miss for everybody at once when it expired.

**Degradation refuses rather than admits.** If the tables cannot be read, the `/use-cases/<slug>`
convention still resolves (it needs no lookup) and a caller who was a member only by grant is
refused (`FRD-405`, `FRD-125`).
"""

from __future__ import annotations

import time
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_common.access import resolve
from aira_common.logging import get_logger
from aira_gateway.db.models import UseCaseGroupRead, UseCaseMemberRead

#: How long a loaded set of grants is trusted. Short enough that granting access feels immediate,
#: long enough that a busy gateway reads the table once a period rather than once a request.
CACHE_TTL_SECONDS = 5.0

_log = get_logger("aira_gateway.grants")


class GroupGrantResolver:
    """Answers "which use cases does this caller reach, and as what" — by a grant to one of their
    groups or a grant naming them (`FRD-209` §2.1)."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker
        self._grants: tuple[tuple[str, str, str], ...] = ()
        self._members: tuple[tuple[str, str, str], ...] = ()
        self._loaded_at = 0.0
        #: False until the first successful load — distinct from "loaded and empty", which is a
        #: normal state.
        self._ready = False

    async def use_cases(
        self, group_paths: Iterable[str], username: str | None = None
    ) -> dict[str, str]:
        """The use cases this caller reaches, mapped to the strongest role each grants.

        ``username`` rather than a subject: Management keys a membership to a Django user and the
        read model stores its username; an OIDC token's `sub` appears in neither.
        """
        held = list(group_paths)
        grants, members = await self._current()
        direct = [
            (use_case, role) for name, use_case, role in members if username and name == username
        ]
        return resolve(held, grants, direct)

    async def _current(
        self,
    ) -> tuple[tuple[tuple[str, str, str], ...], tuple[tuple[str, str, str], ...]]:
        now = time.monotonic()
        if self._ready and now - self._loaded_at < CACHE_TTL_SECONDS:
            return self._grants, self._members
        try:
            async with self._sessionmaker() as session:
                rows = (
                    await session.execute(
                        select(
                            UseCaseGroupRead.group_path,
                            UseCaseGroupRead.use_case_slug,
                            UseCaseGroupRead.role,
                        )
                    )
                ).all()
                # One row per person per use case, so this table can grow. At six figures the
                # answer is an indexed lookup on `subject` with its own short cache.
                member_rows = (
                    await session.execute(
                        select(
                            UseCaseMemberRead.subject,
                            UseCaseMemberRead.use_case_slug,
                            UseCaseMemberRead.role,
                        )
                    )
                ).all()
            self._grants = tuple((str(a), str(b), str(c)) for a, b, c in rows)
            self._members = tuple((str(a), str(b), str(c)) for a, b, c in member_rows)
            self._loaded_at = now
            self._ready = True
        except Exception as exc:  # the database is not reachable, or the table is not there yet
            # Not raising: `/use-cases/<slug>` members are unaffected, and refusing everybody would
            # turn a config-distribution problem into an outage. **Dropped, not served stale**: a
            # grant is permission, and an answer the table can no longer back is not evidence
            # (`test_grants_are_dropped_rather_than_served_stale_when_the_read_fails`).
            _log.warning("group_grants_unavailable", error=str(exc), error_type=type(exc).__name__)
            self._grants = ()
            self._members = ()
        return self._grants, self._members
