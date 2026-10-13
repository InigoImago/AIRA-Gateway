from sqlalchemy.ext.asyncio import AsyncEngine

from aira_gateway.db.base import build_engine


async def test_build_engine_postgres_url_does_not_connect() -> None:
    # create_async_engine builds lazily; no connection is opened here.
    engine = build_engine("postgresql+psycopg://u:p@localhost:5432/aira_gateway")
    assert isinstance(engine, AsyncEngine)
    assert engine.url.get_backend_name() == "postgresql"
    await engine.dispose()


async def test_build_engine_sqlite_url() -> None:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    assert isinstance(engine, AsyncEngine)
    await engine.dispose()


async def test_a_database_error_names_the_statement_and_not_the_values() -> None:
    """**A failed write must not log what it was writing.** SQLAlchemy puts the bound values into
    the text of every database error, and every place that logs one logs `str(exc)`: a failed
    audit row would carry the subject, the source address and the start of the prompt and the
    answer into a log that has no retention clock and no role gate (`FRD-406`) — and a failed
    configuration event the values of fields the consumer deliberately logs by name only.

    The statement and the error stay; they are what somebody debugging needs.
    """
    from sqlalchemy import text
    from sqlalchemy.exc import StatementError

    engine = build_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.execute(text("create table t (secret text not null unique)"))
            await connection.execute(text("insert into t values (:v)"), {"v": "the prompt text"})
            try:
                await connection.execute(
                    text("insert into t values (:v)"), {"v": "the prompt text"}
                )
            except StatementError as exc:
                rendered = str(exc)
            else:  # pragma: no cover - the second insert must fail
                raise AssertionError("the duplicate was accepted")
    finally:
        await engine.dispose()

    assert "UNIQUE" in rendered and "insert into t" in rendered, rendered
    assert "the prompt text" not in rendered, rendered


async def test_every_engine_hides_the_values_it_binds() -> None:
    """The Postgres engine as well — the one whose errors carry the most, built without
    connecting."""
    for url in ("postgresql+psycopg://u:p@localhost:5432/aira_gateway", "sqlite+aiosqlite://"):
        engine = build_engine(url)
        try:
            assert engine.sync_engine.hide_parameters is True, url
        finally:
            await engine.dispose()


def test_a_database_detail_is_not_logged_and_the_rest_is() -> None:
    """Postgres's own `DETAIL` quotes the key a unique constraint refused, or the **whole row** a
    NOT NULL constraint refused — a prompt and an answer among it — and a value with a newline
    runs it over several lines. What a log line may keep is the error and the statement."""
    from aira_gateway.db.errors import loggable

    error = Exception(
        '(psycopg.errors.NotNullViolation) null value in column "api" violates not-null constraint'
        "\nDETAIL:  Failing row contains (row-1, alice, the prompt text\nand its second line)."
        "\n[SQL: INSERT INTO request_logs (id, subject) VALUES (%(id)s, %(subject)s)]"
        "\n[SQL parameters hidden due to hide_parameters=True]"
    )

    logged = loggable(error)

    assert "the prompt text" not in logged and "second line" not in logged, logged
    assert "violates not-null constraint" in logged
    assert "[SQL: INSERT INTO request_logs" in logged
    assert loggable(Exception("integer out of range")) == "integer out of range"
