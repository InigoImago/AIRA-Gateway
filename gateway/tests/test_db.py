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
