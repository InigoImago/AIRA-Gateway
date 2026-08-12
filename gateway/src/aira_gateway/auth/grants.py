"""Resolving a token's Keycloak groups into the use cases they reach (`FRD-209`).

The gateway never asks Management on the request path (`FRD-204`), so group grants arrive over
Kafka into `use_case_groups` and are read from there. That is a read on **every** authenticated
OIDC request against a table written rarely — a cache problem, not shared state, so it is cached
in-process for a few seconds. Exactly the shape `FRD-503` §4.1 settled for suspensions, and for the
same reason.

Two properties worth stating, because both are decisions rather than consequences:

**Degradation refuses rather than admits.** If the table cannot be read, the `/use-cases/<slug>`
convention still resolves — it needs no lookup — and a caller who was a member *only* by group
grant is refused. The moment a control cannot be evaluated is the worst moment to assume it passes;
`FRD-405` settled that for rate limits and `FRD-125` for the injection filter.

**The whole table is cached, not one query per caller.** Grants are configuration: tens of rows,
not thousands, and every caller's answer comes from the same set. A per-caller cache would be a
cache with one entry per person and a miss for everybody at once when it expired.
"""

from __future__ import annotations

import time
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_common.access import resolve
from aira_common.logging import get_logger
from aira_gateway.db.models import UseCaseGroupRead

#: How long a loaded set of grants is trusted. Short enough that granting access feels immediate,
#: long enough that a busy gateway reads the table once a period rather than once a request.
CACHE_TTL_SECONDS = 5.0

_log = get_logger("aira_gateway.grants")


class GroupGrantResolver:
    """Answers "which use cases does this set of group paths reach, and as what"."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker
        self._grants: tuple[tuple[str, str, str], ...] = ()
        self._loaded_at = 0.0
        #: False until the first successful load. Distinct from "loaded and empty": an
        #: installation with no group grants is a normal state, an unreadable table is not, and
        #: only one of the two should make anybody look at a log.
        self._ready = False

    async def use_cases(self, group_paths: Iterable[str]) -> dict[str, str]:
        """The use cases these group paths reach, mapped to the strongest role each grants."""
        held = list(group_paths)
        grants = await self._current()
        return resolve(held, grants)

    async def _current(self) -> tuple[tuple[str, str, str], ...]:
        now = time.monotonic()
        if self._ready and now - self._loaded_at < CACHE_TTL_SECONDS:
            return self._grants
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
            self._grants = tuple((str(a), str(b), str(c)) for a, b, c in rows)
            self._loaded_at = now
            self._ready = True
        except Exception as exc:  # the database is not reachable, or the table is not there yet
            # Deliberately not raising: a caller whose membership comes from the `/use-cases/<slug>`
            # convention is unaffected, and refusing *everybody* because a read-model table could
            # not be read would turn a config-distribution problem into a total outage. The caller
            # who *was* a member only by grant is refused, which is the safe half.
            #
            # **Dropped, not served stale**, and that is the deliberate half: a review on
            # 2026-08-12 proposed keeping the last good copy — one blink of the database takes
            # access from every group-granted caller, which reads like a fault-tolerance gap — and
            # `test_grants_are_dropped_rather_than_served_stale_when_the_read_fails` refused the
            # change. It is right to. A grant is *permission*, so the safe direction is the
            # opposite of a rate limit's: the moment this table stops being readable, its last
            # answer stops being evidence, and handing it out anyway lets access outlive the row
            # that justified it. `TokenSource` serving through a failed refresh is not the same
            # case — a credential we already hold is still ours; a permission we can no longer
            # verify is not still granted.
            _log.warning("group_grants_unavailable", error=str(exc), error_type=type(exc).__name__)
            self._grants = ()
        return self._grants
