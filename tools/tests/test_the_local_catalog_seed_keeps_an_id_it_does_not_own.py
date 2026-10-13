"""`seed_local_catalog.py` does not take a numeric id away from the model that holds it, unasked.

The seed writes the gateway's read-model directly, and its chat declaration carries `1004` — the id
KIRA clients send. Run for another chat model it released `1004` from the one holding it, without a
word: on the running stack the read-model ended with `qwen3:0.6b` at `NULL` while Management and
every outbox event said `1004`, so every KIRA request for the demo's chat model was refused with
`MODEL_NOT_FOUND`, and the integration cases that expected a validation error passed for that reason
instead. Management never repaired it, because its next event is about whatever it changes next.

The move stays possible — the id names a *role*, and two rows claiming it leave the resolver unable
to answer at all — but only when asked for.
"""

from __future__ import annotations

from typing import Any

import pytest
import seed_local_catalog as seed
from sqlalchemy import text

from aira_gateway.db.base import build_engine, create_all

HOLDER = "qwen3:0.6b"
NEWCOMER = "qwen3:4b"


def _chat_declaration(model: str) -> dict[str, Any]:
    return {**seed.DECLARATIONS[0], "model": model}


async def _catalog(connection: Any) -> dict[str, int | None]:
    rows = await connection.execute(text("SELECT model, numeric_id FROM model_catalog"))
    return {str(model): numeric_id for model, numeric_id in rows.all()}


async def _with_the_holder_declared(test: Any) -> Any:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    try:
        async with engine.begin() as connection:
            await seed.declare(connection, [_chat_declaration(HOLDER)])
            return await test(connection)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_an_id_another_model_holds_is_refused_and_nothing_is_written() -> None:
    async def attempt(connection: Any) -> dict[str, int | None]:
        with pytest.raises(seed.IdTaken, match=HOLDER):
            await seed.declare(connection, [_chat_declaration(NEWCOMER)])
        return await _catalog(connection)

    assert await _with_the_holder_declared(attempt) == {HOLDER: seed.CHAT_NUMERIC_ID}


@pytest.mark.asyncio
async def test_the_id_moves_when_that_is_what_was_asked() -> None:
    async def move(connection: Any) -> dict[str, int | None]:
        await seed.declare(connection, [_chat_declaration(NEWCOMER)], move_id=True)
        return await _catalog(connection)

    assert await _with_the_holder_declared(move) == {
        HOLDER: None,
        NEWCOMER: seed.CHAT_NUMERIC_ID,
    }


@pytest.mark.asyncio
async def test_declaring_the_holder_again_is_not_a_move() -> None:
    """A re-run for the same model is the ordinary case and must keep working without a flag."""

    async def again(connection: Any) -> dict[str, int | None]:
        await seed.declare(connection, [_chat_declaration(HOLDER)])
        return await _catalog(connection)

    assert await _with_the_holder_declared(again) == {HOLDER: seed.CHAT_NUMERIC_ID}


def test_a_declaration_is_not_changed_by_being_declared() -> None:
    """The measured capabilities are applied to a copy: twice through, `tools` is listed once."""
    first = seed._measured(_chat_declaration(HOLDER))
    second = seed._measured(_chat_declaration(HOLDER))

    assert first["capabilities"].count("tools") == 1
    assert second == first
    assert "tools" not in seed.DECLARATIONS[0]["capabilities"]
