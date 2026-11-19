"""An audit row is not lost to a connection the database closed.

After a database restart or failover the pool still holds connections the server has closed, and
the first row the writer tried on one failed and was dropped — `request_log_write_failed` in the
log, and a served request with no record. The pool now tests a connection before handing it out,
and a row whose write fails for a reason of the database's own is written once more, on a fresh
connection. A row the database refuses for what it carries is still not tried twice.

SQLite stands in for the server: its connection is closed underneath the pool, which is what a
server-side termination looks like to the client, and a statement listener raises what psycopg
raises.
"""

from __future__ import annotations

from typing import Any

import psycopg.errors
import pytest
from sqlalchemy import event, select

from aira_gateway.config import GatewaySettings
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all
from aira_gateway.db.models import RequestLog
from aira_gateway.persistence import writer as writer_module
from aira_gateway.persistence.redaction import NoOpRedactor
from aira_gateway.persistence.writer import PendingLog, RequestLogWriter


@pytest.fixture
async def engine(tmp_path: Any) -> Any:
    built = build_engine(f"sqlite+aiosqlite:///{tmp_path / 'gateway.db'}")
    await create_all(built)
    yield built
    await built.dispose()


def _row(latency: int) -> PendingLog:
    return PendingLog(
        subject="ada",
        auth_method="api_key",
        use_case=None,
        source_ip="10.0.0.1",
        operation="generateContent",
        model="mock-1",
        status=200,
        usage=None,
        latency_ms=latency,
        trace_id=None,
        request_payload=None,
        response_payload=None,
        cost_nanos=1,
        api="gemini",
    )


class _Inserts:
    """Fails the first ``failing`` INSERTs into `request_logs` with ``error``, noting each id."""

    def __init__(self, error: Exception, failing: int = 1) -> None:
        self.error = error
        self.failing = failing
        self.ids: list[str] = []

    def listener(
        self,
        _conn: Any,
        _cursor: Any,
        statement: str,
        parameters: Any,
        _context: Any,
        executemany: bool,
    ) -> None:
        if not statement.lstrip().upper().startswith("INSERT INTO REQUEST_LOGS"):
            return
        row = parameters[0] if executemany else parameters
        # SQLite binds by position, and the id is the statement's first column.
        self.ids.append(row["id"] if isinstance(row, dict) else row[0])
        if len(self.ids) <= self.failing:
            raise self.error


async def _latencies(sessions: Any) -> list[int]:
    async with sessions() as session:
        return sorted((await session.execute(select(RequestLog.latency_ms))).scalars())


async def test_the_first_row_after_the_server_closed_the_connection_is_written(
    engine: Any,
) -> None:
    sessions = build_sessionmaker(engine)
    writer = RequestLogWriter(sessions, GatewaySettings(), NoOpRedactor(), max_queue=8)
    await writer.start()

    await writer.submit(_row(1))
    await writer.drain()
    # The server ends the session; the pool still holds the connection as if it were good.
    engine.sync_engine.pool.connection.dbapi_connection._connection._connection.close()
    await writer.submit(_row(2))
    await writer.drain()
    await writer.stop()

    assert await _latencies(sessions) == [1, 2]


async def test_a_row_the_database_failed_for_its_own_reason_is_written_once_more(
    engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The moment the pool's test cannot cover — the connection dies between the test and the
    statement — with nothing about the row at fault. Inline, so the write is the caller's."""
    inserts = _Inserts(psycopg.errors.AdminShutdown("terminating connection"))
    event.listen(engine.sync_engine, "before_cursor_execute", inserts.listener)
    retried: list[str] = []
    monkeypatch.setattr(
        writer_module._log, "warning", lambda event, **_fields: retried.append(event)
    )
    sessions = build_sessionmaker(engine)

    await RequestLogWriter(sessions, GatewaySettings(), NoOpRedactor(), max_queue=0).submit(_row(7))

    assert await _latencies(sessions) == [7]
    assert retried == ["request_log_write_retried"]
    assert len(inserts.ids) == 2 and inserts.ids[0] == inserts.ids[1], (
        "the second attempt must write the same row, so a first that landed cannot make two"
    )


async def test_a_row_the_database_refuses_for_what_it_carries_is_not_tried_twice(
    engine: Any,
) -> None:
    inserts = _Inserts(psycopg.errors.StringDataRightTruncation("value too long"))
    event.listen(engine.sync_engine, "before_cursor_execute", inserts.listener)
    sessions = build_sessionmaker(engine)
    writer = RequestLogWriter(sessions, GatewaySettings(), NoOpRedactor(), max_queue=8)
    await writer.start()

    await writer.submit(_row(3))
    await writer.drain()
    await writer.stop()

    assert len(inserts.ids) == 1
    assert await _latencies(sessions) == []
