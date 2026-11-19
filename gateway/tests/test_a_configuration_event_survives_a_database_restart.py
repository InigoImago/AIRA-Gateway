"""A configuration event is applied even when the database could not take it the first time.

The consumer applies each event in a transaction of its own and skips one it cannot apply, so that
one bad event cannot freeze the read-model. It skipped **every** failure that way, and a database
that restarts, fails over, or has its idle connection cut by a proxy fails the next event through
no fault of the event: the pooled connection is dead, and only the next statement finds out. With
the offset committed on a timer, the skipped event never came back. If it was an `api_key.revoked`,
the key went on working at the gateway while the console showed it revoked — the harm the skip
exists to prevent, caused by the skip.

Four properties, each tested where it can fail on its own:

- a pooled connection the server has closed is replaced before it is used, so the first statement
  after a restart does not fail at all (`db.base`);
- a failure that comes from the database's state is held and tried again, with a pause that grows
  to a ceiling, until the event applies; one that comes from the event is still skipped
  (`consumer.worker`, `db.transient`);
- an offset is committed by the loop once its event was applied or deliberately skipped, never by a
  timer that moves past an event still being tried;
- the classification reads the error's type and SQLSTATE, never its message.

SQLite stands in for the server, as everywhere in this suite: its connection is closed underneath
the pool, which is what a server-side termination looks like to the client, and a statement listener
raises the errors psycopg raises.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import psycopg
import psycopg.errors
import pytest
from aiokafka.structs import TopicPartition
from sqlalchemy import event, select, text
from sqlalchemy import exc as sa_exc

from aira_common.kafka import API_KEY_TOPIC, EVENT_TYPE_HEADER, USECASE_TOPIC, consumer_group
from aira_gateway.config import GatewaySettings
from aira_gateway.consumer.worker import (
    RETRY_LONGEST_SECONDS,
    apply_one_message,
    consume_forever,
    consumer_options,
)
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all
from aira_gateway.db.models import ApiKey, UseCaseRead
from aira_gateway.db.transient import is_transient


@dataclass
class _Message:
    value: bytes
    event_type: str
    topic: str
    offset: int
    partition: int = 0
    headers: list[tuple[str, bytes]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.headers = [(EVENT_TYPE_HEADER, self.event_type.encode())]


def _revocation(offset: int = 41) -> _Message:
    return _Message(b'{"prefix": "aira_k1"}', "api_key.revoked", API_KEY_TOPIC, offset)


def _use_case(offset: int = 7) -> _Message:
    return _Message(
        b'{"slug": "coding", "name": "Coding"}', "usecase.upserted", USECASE_TOPIC, offset
    )


@dataclass
class _Database:
    """The server, as the consumer's statements meet it: down for the next ``down_for`` of them.

    Raises what psycopg raises for a terminated session (`AdminShutdown`, SQLSTATE `57P01`) or, when
    given, another error — so a test can say *which* failure the database answers with.
    """

    down_for: int = 0
    error: Exception = field(
        default_factory=lambda: psycopg.errors.AdminShutdown(
            "terminating connection due to administrator command"
        )
    )
    refused: int = 0

    def listener(self, *_args: Any) -> None:  # before_cursor_execute
        if self.refused < self.down_for:
            self.refused += 1
            raise self.error


class _Sleeps:
    """Stands in for ``asyncio.sleep``: notes each pause, and what was committed when it began."""

    def __init__(self, broker: _Broker | None = None) -> None:
        self.delays: list[float] = []
        self.committed_meanwhile: list[list[dict[TopicPartition, int]]] = []
        self._broker = broker

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        if self._broker is not None:
            self.committed_meanwhile.append(list(self._broker.committed))


class _Broker:
    """A consumer group's member as `consume_forever` drives it, recording what it commits."""

    def __init__(self, messages: list[_Message]) -> None:
        self._messages = messages
        self.committed: list[dict[TopicPartition, int]] = []

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def commit(self, offsets: dict[TopicPartition, int]) -> None:
        self.committed.append(dict(offsets))

    async def __aiter__(self):  # noqa: ANN204
        for message in self._messages:
            yield message


@pytest.fixture
async def engine(tmp_path: Any) -> Any:
    """The production engine on a file, so a connection closed underneath can be opened again."""
    built = build_engine(f"sqlite+aiosqlite:///{tmp_path / 'gateway.db'}")
    await create_all(built)
    yield built
    await built.dispose()


@pytest.fixture
async def sessions(engine: Any) -> Any:
    maker = build_sessionmaker(engine)
    async with maker() as session:
        session.add(ApiKey(prefix="aira_k1", key_hash="h", subject="ada", use_case="coding"))
        await session.commit()
    return maker


async def _key_is_active(sessions: Any) -> bool:
    async with sessions() as session:
        return bool((await session.execute(select(ApiKey.is_active))).scalar_one())


# ---- the pool ------------------------------------------------------------------------------


async def test_a_pooled_connection_the_server_closed_is_replaced_before_it_is_used(
    engine: Any, sessions: Any
) -> None:
    """The consumer is idle most of the time, so its pooled connection is exactly the one a
    restart leaves dead — and without a test on checkout, the next event is the one that finds
    out. Asserted on a plain statement, with no retry anywhere above it: the pool alone must
    hand out a working connection."""
    async with sessions() as session:
        await session.execute(text("select 1"))

    # What a server-side termination looks like to the client: the connection is gone under the
    # pool, which still holds it as if it were good.
    pooled = engine.sync_engine.pool.connection.dbapi_connection
    pooled._connection._connection.close()

    async with sessions() as session:
        assert (await session.execute(select(ApiKey.prefix))).scalar_one() == "aira_k1"


# ---- holding an event --------------------------------------------------------------------


async def test_an_event_the_database_could_not_take_is_applied_once_it_can(
    engine: Any, sessions: Any
) -> None:
    database = _Database(down_for=3)
    event.listen(engine.sync_engine, "before_cursor_execute", database.listener)
    sleeps = _Sleeps()

    applied = await apply_one_message(sessions, _revocation(), sleep=sleeps)

    assert applied == "api_key.revoked"
    assert await _key_is_active(sessions) is False, "the revocation was skipped: the key works"
    assert len(sleeps.delays) == 3, "tried again once per refusal, and not again after it applied"


async def test_the_pause_between_tries_grows_and_stops_growing(engine: Any, sessions: Any) -> None:
    """Long enough not to hammer a database that is coming back, short enough that one which is
    back is noticed within the ceiling — however long it was away."""
    database = _Database(down_for=12)
    event.listen(engine.sync_engine, "before_cursor_execute", database.listener)
    sleeps = _Sleeps()

    assert await apply_one_message(sessions, _revocation(), sleep=sleeps) == "api_key.revoked"

    assert sleeps.delays == sorted(sleeps.delays), "the pause never shrinks while it fails"
    assert sleeps.delays[0] < sleeps.delays[3]
    assert max(sleeps.delays) == RETRY_LONGEST_SECONDS
    assert sleeps.delays[-3:] == [RETRY_LONGEST_SECONDS] * 3


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(psycopg.errors.DeadlockDetected("deadlock detected"), id="deadlock"),
        pytest.param(
            psycopg.errors.SerializationFailure("could not serialize"), id="serialization"
        ),
        pytest.param(psycopg.errors.QueryCanceled("statement timeout"), id="statement-timeout"),
        pytest.param(psycopg.errors.LockNotAvailable("lock timeout"), id="lock-timeout"),
        pytest.param(psycopg.errors.TooManyConnections("too many clients"), id="too-many"),
        pytest.param(psycopg.errors.CannotConnectNow("starting up"), id="starting-up"),
        pytest.param(psycopg.OperationalError("connection refused"), id="no-server-at-all"),
    ],
)
async def test_every_failure_of_the_databases_state_holds_the_event(
    engine: Any, sessions: Any, error: Exception
) -> None:
    database = _Database(down_for=1, error=error)
    event.listen(engine.sync_engine, "before_cursor_execute", database.listener)

    assert await apply_one_message(sessions, _revocation(), sleep=_Sleeps()) == "api_key.revoked"
    assert await _key_is_active(sessions) is False


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(psycopg.errors.StringDataRightTruncation("value too long"), id="too-long"),
        pytest.param(psycopg.errors.UniqueViolation("duplicate key"), id="unique"),
        pytest.param(psycopg.errors.ProgramLimitExceeded("index row too big"), id="limit"),
    ],
)
async def test_a_failure_that_comes_from_the_event_is_still_skipped_at_once(
    engine: Any, sessions: Any, error: Exception
) -> None:
    """The other half, which keeps the containment: an event the database refuses for what it
    carries is refused the same way every time, and holding it would stop every change behind it.
    `ProgramLimitExceeded` is an `OperationalError` to psycopg and permanent all the same, which
    is why the class decides and not the exception's base."""
    database = _Database(down_for=1, error=error)
    event.listen(engine.sync_engine, "before_cursor_execute", database.listener)
    sleeps = _Sleeps()

    assert await apply_one_message(sessions, _revocation(), sleep=sleeps) is None
    assert sleeps.delays == []


# ---- the offset ----------------------------------------------------------------------------


async def test_an_offset_is_committed_only_once_its_event_was_applied(
    engine: Any, sessions: Any
) -> None:
    """The commit is what decides whether a restart in the middle loses the event. While the
    revocation is being tried again nothing of it is committed; once it applied, the position
    after it is — and the next event's after that."""
    database = _Database(down_for=2)
    event.listen(engine.sync_engine, "before_cursor_execute", database.listener)
    broker = _Broker([_revocation(offset=41), _use_case(offset=7)])
    sleeps = _Sleeps(broker)

    seen = await consume_forever(broker, sessions, sleep=sleeps)

    assert seen == 2
    assert sleeps.committed_meanwhile == [[], []], "committed past an event still being tried"
    assert broker.committed == [
        {TopicPartition(API_KEY_TOPIC, 0): 42},
        {TopicPartition(USECASE_TOPIC, 0): 8},
    ]
    assert await _key_is_active(sessions) is False
    async with sessions() as session:
        assert await session.get(UseCaseRead, "coding") is not None


async def test_a_skipped_events_offset_is_committed_like_an_applied_ones(sessions: Any) -> None:
    """Skipping is a decision about the event, so the position moves past it: delivered again it
    would be refused again."""
    broker = _Broker([_Message(b"not json", "usecase.upserted", USECASE_TOPIC, offset=3)])

    await consume_forever(broker, sessions, sleep=_Sleeps())

    assert broker.committed == [{TopicPartition(USECASE_TOPIC, 0): 4}]


def test_the_client_commits_nothing_on_a_timer() -> None:
    """aiokafka's default commits whatever it has handed out every five seconds — past an event
    the loop is still trying — and on a restart in that window the event is gone."""
    options = consumer_options(GatewaySettings(test_database=True))

    assert options["enable_auto_commit"] is False
    assert options["group_id"] == consumer_group("")


# ---- telling the two apart -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "transient"),
    [
        (psycopg.errors.AdminShutdown("terminating connection"), True),
        (psycopg.errors.CrashShutdown("crash"), True),
        (psycopg.errors.ConnectionFailure("lost"), True),
        (psycopg.errors.DeadlockDetected("deadlock"), True),
        (psycopg.errors.DiskFull("disk full"), True),
        (psycopg.errors.IoError("io"), True),
        (psycopg.OperationalError("server closed the connection unexpectedly"), True),
        (psycopg.InterfaceError("the connection is closed"), True),
        (sa_exc.DisconnectionError("pool"), True),
        (sa_exc.TimeoutError("QueuePool limit reached"), True),
        (psycopg.errors.ObjectInUse("in use"), False),
        (psycopg.errors.ProgramLimitExceeded("limit"), False),
        (psycopg.errors.InvalidTextRepresentation("bad input"), False),
        (psycopg.errors.UndefinedColumn("no such column"), False),
        (KeyError("slug"), False),
        (ValueError("not a JSON object"), False),
    ],
)
def test_the_type_and_the_state_decide(error: BaseException, transient: bool) -> None:
    assert is_transient(error) is transient


def test_a_wrapped_error_is_read_through_its_wrapper() -> None:
    """What SQLAlchemy hands the caller: its own class around the driver's — read for the driver's
    state, and for its own finding that the connection is dead."""
    shutdown = psycopg.errors.AdminShutdown("terminating connection")
    truncated = psycopg.errors.StringDataRightTruncation("value too long")

    assert is_transient(sa_exc.OperationalError("SELECT 1", {}, shutdown))
    assert not is_transient(sa_exc.DataError("INSERT", {}, truncated))
    assert is_transient(
        sa_exc.DBAPIError("SELECT 1", {}, Exception("gone"), connection_invalidated=True)
    )
