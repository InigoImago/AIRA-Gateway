"""Narrowing a report to one person selects exactly that person's rows, and an index can answer it.

`REQUEST_PERSON` is how rows are *grouped* by person: the name where the credential carried one,
the subject otherwise. Filtering by comparing that expression with a name selects the right rows
and reaches no index — on the largest table here, a scan of every row in the window, behind an
endpoint every credential holder may call. `request_person_is` is the indexable spelling, and the
two must never disagree about a row: a filter and a grouping that differ put one person's figures
in two places.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_gateway.db.base import build_engine, build_sessionmaker, create_all
from aira_gateway.db.models import RequestLog
from aira_gateway.db.models.owned import REQUEST_PERSON, request_person_is

WHEN = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)

#: (id, subject, username): every way a row can name its person.
ROWS = [
    ("key", "ada", "ada"),  # an API key: the subject *is* the name
    ("browser", "8f3c-uuid", "ada"),  # a sign-in: the directory id, and the name beside it
    ("unnamed", "ada", None),  # a credential that carried no name
    ("blank", "ada", ""),  # an empty name is absence, as `nullif` reads it
    ("other", "bob", "bob"),
    ("renamed", "ada", "bob"),  # the subject says ada, the name says bob: bob's
    ("idlike", "bob-uuid", None),
]


@pytest_asyncio.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    sessions = build_sessionmaker(engine)
    async with sessions() as session:
        for ident, subject, username in ROWS:
            session.add(
                RequestLog(
                    id=ident,
                    subject=subject,
                    username=username,
                    auth_method="api_key",
                    use_case="uc",
                    api="gemini",
                    operation="generateContent",
                    model="m",
                    status=200,
                    outcome="served",
                    created_at=WHEN,
                )
            )
        await session.commit()
    yield sessions
    await engine.dispose()


@pytest.mark.parametrize("person", ["ada", "bob", "8f3c-uuid", "bob-uuid", "nobody"])
async def test_the_filter_selects_the_rows_the_grouping_assigns(
    sessions: async_sessionmaker[AsyncSession], person: str
) -> None:
    async with sessions() as session:
        # `__eq__` rather than `==` only for the linter, which reads a column on the left as a
        # Yoda condition and offers to flip it.
        grouped = set(
            (
                await session.scalars(select(RequestLog.id).where(REQUEST_PERSON.__eq__(person)))
            ).all()
        )
        filtered = set(
            (await session.scalars(select(RequestLog.id).where(request_person_is(person)))).all()
        )

    assert filtered == grouped


async def test_ada_is_her_key_her_sign_in_and_her_unnamed_rows(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """The equality above holds trivially if both sides select nothing; this pins what it is."""
    async with sessions() as session:
        rows = set(
            (await session.scalars(select(RequestLog.id).where(request_person_is("ada")))).all()
        )

    assert rows == {"key", "browser", "unnamed", "blank"}


def test_the_filter_compares_columns_not_an_expression() -> None:
    """What an index can answer: each side of each comparison is a column or a value."""
    compiled = str(select(RequestLog.id).where(request_person_is("ada"))).lower()

    assert "coalesce" not in compiled
    assert "nullif" not in compiled
