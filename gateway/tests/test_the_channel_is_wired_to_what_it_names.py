"""Each system the channel names is actually reached from the code that talks to it (`FRD-617`).

This project's most-repeated defect is *two correct halves and no wire* (`LESSONS.md` §1): the
channel has its own tests, every call site reads correctly, and every one of those reviews passes
against a wiring that was never added. So these drive the **real** functions — the consumer loop,
the engine builder, the settings — with a fake far end, and ask whether the line appeared.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

from aira_common.integration_debug import configure_integration_debug
from aira_common.logging import configure_logging
from aira_gateway.config import GatewaySettings
from aira_gateway.consumer.worker import GROUP_ID, consume_forever
from aira_gateway.db.base import build_engine, build_sessionmaker


@pytest.fixture(autouse=True)
def _channel() -> Iterator[None]:
    configure_logging("INFO", json_output=True)
    configure_integration_debug("all")
    yield
    configure_integration_debug("")


def calls(capsys: Any) -> list[dict]:
    return [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.strip().startswith("{") and '"integration_call"' in line
    ]


# --- kafka, the consumer end --------------------------------------------------------------------


class Message:
    def __init__(self, offset: int, event_type: bytes | None = b"use_case.upserted") -> None:
        self.topic = "aira.usecases"
        self.partition = 0
        self.offset = offset
        self.headers = [("event_type", event_type)] if event_type else []
        self.value = {"slug": "kundenservice", "name": "Kundenservice", "active": True}


class FakeConsumer:
    def __init__(self, messages: list[Message]) -> None:
        self._messages = messages
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def __aiter__(self) -> AsyncIterator[Message]:
        for message in self._messages:
            yield message


async def _sessionmaker() -> Any:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    from aira_gateway.db.base import create_all

    await create_all(engine)
    return build_sessionmaker(engine)


async def test_the_consumer_says_it_connected_and_to_which_group(capsys: Any) -> None:
    """`consumer.start()` is where a SASL mechanism, a trust store and a broker address are first
    proven against a real broker, and it said nothing at all."""
    consumer = FakeConsumer([])
    await consume_forever(consumer, await _sessionmaker(), "broker:9093")

    (line,) = [c for c in calls(capsys) if c["operation"] == "consumer.start"]
    assert line["outcome"] == "ok"
    assert line["target"] == "broker:9093"
    assert line["group"] == GROUP_ID
    assert consumer.started and consumer.stopped


async def test_an_arriving_event_is_reported_with_its_offset(capsys: Any) -> None:
    """The only line that says *an event arrived at all*. `apply_one_message` speaks up when it
    cannot apply one; nothing spoke when it could."""
    seen = await consume_forever(FakeConsumer([Message(17)]), await _sessionmaker())
    assert seen == 1

    (line,) = [c for c in calls(capsys) if c["operation"] == "consumer.receive"]
    assert (line["topic"], line["partition"], line["offset"]) == ("aira.usecases", 0, 17)
    assert line["applied"] is True
    assert line["event_type"] == "use_case.upserted"


async def test_an_event_that_could_not_be_applied_is_reported_as_not_applied(capsys: Any) -> None:
    """`apply_one_message` swallows its own failures on purpose — one bad event must not take the
    consumer down — so `applied` is what carries the difference, not an exception."""
    await consume_forever(FakeConsumer([Message(18, event_type=None)]), await _sessionmaker())

    (line,) = [c for c in calls(capsys) if c["operation"] == "consumer.receive"]
    assert line["applied"] is False


async def test_a_broker_that_will_not_accept_us_is_reported_and_the_error_propagates(
    capsys: Any,
) -> None:
    class Refusing(FakeConsumer):
        async def start(self) -> None:
            raise ConnectionError("KafkaConnectionError: Unable to bootstrap from [('b', 9093)]")

    with pytest.raises(ConnectionError):
        await consume_forever(Refusing([]), await _sessionmaker(), "b:9093")

    (line,) = [c for c in calls(capsys) if c["operation"] == "consumer.start"]
    assert line["outcome"] == "failed"
    assert "Unable to bootstrap" in line["error"]


# --- postgres -----------------------------------------------------------------------------------


async def test_opening_a_database_connection_says_where_to(capsys: Any) -> None:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.exec_driver_sql("select 1")

    (line,) = [c for c in calls(capsys) if c["operation"] == "connect"]
    assert line["system"] == "postgres"
    assert line["target"] == "sqlite+aiosqlite:///:memory:"


async def test_a_closed_port_is_reported_with_the_drivers_own_words(capsys: Any) -> None:
    """The moment the pool finds a wrong host, a closed port or a rejected password — which is the
    whole of what the first day of an integration consists of.

    Against the **real** dialect the gateway uses (`postgresql+psycopg`, see
    `GatewaySettings.database_url`) and port 1, which nothing listens on. A stubbed dialect would
    not prove that a driver's connection failure reaches SQLAlchemy's `handle_error` at all.
    """
    engine = build_engine("postgresql+psycopg://aira:s3cret@127.0.0.1:1/aira_gateway")
    with pytest.raises(OperationalError):
        async with engine.begin() as connection:
            await connection.exec_driver_sql("select 1")

    failures = [c for c in calls(capsys) if c["operation"] == "error"]
    assert failures, "a connection that cannot be established must reach the channel"
    assert failures[0]["outcome"] == "failed"
    assert "Connection refused" in failures[0]["error"]
    # Redacted twice over, by two mechanisms that do not know about each other: SQLAlchemy renders
    # the URL with `hide_password=True`, and the channel redacts a `user:password@` in any address
    # it is handed. Either alone is enough; the point is that neither is the only one.
    assert "s3cret" not in json.dumps(failures[0])
    assert failures[0]["target"] == "postgresql+psycopg://aira:REDACTED@127.0.0.1:1/aira_gateway"


async def test_a_database_file_that_cannot_be_opened_is_reported(capsys: Any) -> None:
    """The other branch: SQLAlchemy reports it with no connection in hand rather than as a
    disconnect, so a filter written on `is_disconnect` alone would miss it."""
    engine = build_engine("sqlite+aiosqlite:////nonexistent-dir/aira.db")
    with pytest.raises(OperationalError):
        async with engine.begin() as connection:
            await connection.exec_driver_sql("select 1")

    (line,) = [c for c in calls(capsys) if c["operation"] == "error"]
    assert line["is_disconnect"] is False
    assert "unable to open database file" in line["error"]


async def test_an_ordinary_query_error_is_not_reported_as_an_outage(capsys: Any) -> None:
    """A wrong table on a working database is a correct answer from a reachable one. Reporting it
    here would bury the four lines that matter under thousands that do not."""
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    with pytest.raises(OperationalError):
        async with engine.begin() as connection:
            await connection.exec_driver_sql("select * from no_such_table")

    assert [c for c in calls(capsys) if c["operation"] == "error"] == []


# --- the switch itself, through the settings -----------------------------------------------------


def test_the_setting_is_off_by_default() -> None:
    assert GatewaySettings(test_database=True).debug_integrations == ""


def test_a_misspelled_system_refuses_the_settings() -> None:
    """A startup refusal rather than a channel that silently watches nothing — the operator would
    otherwise conclude the feature is broken (`LESSONS.md` §3)."""
    with pytest.raises(ValueError, match="kafak"):
        GatewaySettings(test_database=True, debug_integrations="kafak")


def test_nothing_is_said_when_the_channel_is_off(capsys: Any) -> None:
    configure_integration_debug("")
    build_engine("sqlite+aiosqlite:///:memory:")
    assert calls(capsys) == []
