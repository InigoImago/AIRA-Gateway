"""Lightweight readiness checks shared by AIRA services.

The skeleton uses TCP reachability probes (stdlib only, no driver dependencies) to
gate ``/readyz``. As real clients (DB driver, Kafka client) are introduced, dedicated
checks can replace or augment these.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass


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
    """Run :func:`tcp_reachable` and wrap the outcome in a :class:`CheckResult`."""
    ok = await tcp_reachable(host, port, timeout=timeout)
    return CheckResult(name=name, ok=ok, detail=None if ok else f"{host}:{port} unreachable")
