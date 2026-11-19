import asyncio

import aira_common.health as health_module
from aira_common.health import CheckResult, check_tcp, tcp_reachable


async def test_tcp_reachable_true_for_listening_socket() -> None:
    server = await asyncio.start_server(lambda r, w: None, host="127.0.0.1", port=0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        assert await tcp_reachable("127.0.0.1", port) is True


async def test_tcp_reachable_false_for_closed_port() -> None:
    # port 1 is virtually never open to an unprivileged local connect
    assert await tcp_reachable("127.0.0.1", 1, timeout=0.5) is False


async def test_check_tcp_ok() -> None:
    server = await asyncio.start_server(lambda r, w: None, host="127.0.0.1", port=0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        result = await check_tcp("db", "127.0.0.1", port)
    assert result == CheckResult(name="db", ok=True, detail=None)


async def test_check_tcp_failure_has_detail() -> None:
    result = await check_tcp("db", "127.0.0.1", 1, timeout=0.5)
    assert result.ok is False
    assert result.detail is not None
    assert "unreachable" in result.detail


async def test_tcp_reachable_swallows_close_errors(monkeypatch) -> None:
    """A successful connect still returns True even if socket cleanup errors."""

    class _Writer:
        def close(self) -> None:  # noqa: D401
            pass

        async def wait_closed(self) -> None:
            raise OSError("already gone")

    async def fake_open_connection(host: str, port: int):
        return object(), _Writer()

    monkeypatch.setattr(health_module.asyncio, "open_connection", fake_open_connection)
    # An address, so the check reaches the connection without asking a resolver about a name.
    assert await tcp_reachable("127.0.0.1", 1234) is True


async def test_check_tcp_failure_hides_internal_address() -> None:
    """/readyz is unauthenticated — it must not map the internal network (ADR-0007)."""
    result = await check_tcp("db", "postgres.internal", 1, timeout=0.5)
    assert result.detail == "unreachable"
    assert "postgres.internal" not in str(result)


async def test_a_check_does_not_wait_behind_the_default_executor() -> None:
    """Readiness answers for the database, not for whatever else holds the event loop's threads.

    The default executor serves the audit writer, mail and — on the standard event loop — every
    name resolution. A check resolving its host there waits behind them, times out in the queue,
    and reports a database that answers as unreachable: `/readyz` is a 503, and an orchestrator
    takes every busy instance out of rotation at once. Here the default executor is held by work
    that outlasts the check's timeout; ``localhost`` is a name, so the old check had to queue for
    it, and the listener is the test's own.
    """
    import socket as sockets
    import threading
    from concurrent.futures import ThreadPoolExecutor

    release = threading.Event()
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=2))
    busy = [loop.run_in_executor(None, release.wait, 5.0) for _ in range(2)]
    try:
        with sockets.socket() as database:
            database.bind(("127.0.0.1", 0))
            database.listen(8)
            port = database.getsockname()[1]
            result = await check_tcp("postgres", "localhost", port, timeout=0.5)
    finally:
        release.set()
        await asyncio.gather(*busy)

    assert result.ok, "the check queued behind the default executor and timed out there"
