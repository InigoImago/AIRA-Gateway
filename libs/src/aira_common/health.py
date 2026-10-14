"""Lightweight readiness checks shared by AIRA services: TCP reachability, stdlib only."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Sequence
from dataclasses import dataclass

from aira_common.logging import get_logger


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Outcome of a single readiness check."""

    name: str
    ok: bool
    detail: str | None = None


async def tcp_reachable(host: str, port: int, *, timeout: float = 1.0) -> bool:
    """Return True if a TCP connection to ``host:port`` succeeds within ``timeout``."""
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
    except TimeoutError, OSError:
        return False
    writer.close()
    with contextlib.suppress(OSError):
        await writer.wait_closed()
    return True


async def check_tcp(name: str, host: str, port: int, *, timeout: float = 1.0) -> CheckResult:
    """Run :func:`tcp_reachable` and wrap the outcome in a :class:`CheckResult`.

    ``/readyz`` is unauthenticated, so the failure detail names only the dependency, never its host
    and port (`ADR-0007`); the full address goes to the service log.
    """
    ok = await tcp_reachable(host, port, timeout=timeout)
    if ok:
        return CheckResult(name=name, ok=True, detail=None)
    get_logger("aira_common.health").warning(
        "dependency_unreachable", dependency=name, host=host, port=port
    )
    return CheckResult(name=name, ok=False, detail="unreachable")


def any_reachable(name: str, members: Sequence[CheckResult]) -> CheckResult:
    """One verdict for a cluster from the checks of its members: reachable while **any** member is.

    A Kafka client is given the whole broker list and needs one broker to answer. A readiness
    check of the first broker alone reported the cluster unreachable whenever that one broker was
    down. The detail counts the members that answered and never names them (`ADR-0007`).
    """
    answered = sum(1 for member in members if member.ok)
    if answered == 0:
        return CheckResult(name=name, ok=False, detail="unreachable")
    if answered == len(members):
        return CheckResult(name=name, ok=True, detail=None)
    return CheckResult(name=name, ok=True, detail=f"{answered} of {len(members)} reachable")
