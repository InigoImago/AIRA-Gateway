"""A budget used up is mailed to the person, end to end (`FRD-409`).

What the unit layer cannot see: that the deployed gateway runs the round, that the directory
account reads the person's address from the development realm, and that the relay takes the mail.
The relay is the showcase stack's mail catcher, read through its own API.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
import stack_addresses
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .governed import MOCK_MODEL, Governed

pytestmark = pytest.mark.integration

#: The showcase's mail catcher (`docker-compose.showcase.yml`), as the host reaches it.
MAILPIT = stack_addresses.url("mailpit")

#: A person of the development realm, with the address Keycloak holds for them.
PERSON = "ucuser"
ADDRESS = "ucuser@demo.aira"

#: The round runs once a minute; a mail is due within one round of the booking that crossed.
WITHIN_SECONDS = 150.0


async def _mails_about(slug: str) -> list[dict]:
    """The messages to ``ADDRESS`` that are about this test's use case — its link names the slug."""
    async with httpx.AsyncClient(base_url=MAILPIT, timeout=10.0) as client:
        listed = (await client.get("/api/v1/search", params={"query": f"to:{ADDRESS}"})).json()
        found = []
        for summary in listed.get("messages") or []:
            message = (await client.get(f"/api/v1/message/{summary['ID']}")).json()
            if f"/use-cases/{slug}?tab=budgets" in message.get("Text", ""):
                found.append(message)
        return found


async def test_a_member_whose_budget_is_used_up_is_mailed_at_their_keycloak_address(
    governed: Governed, engine: AsyncEngine
) -> None:
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            await client.get(f"{MAILPIT}/api/v1/info")
        except httpx.HTTPError:
            pytest.fail("no mail catcher at :8025 — the stack runs it with the showcase overlay")

    # A new use case mails at 100 % without being told (`[100]`, the default both planes share).
    await governed.budget(requests=1, scope="each_member", period="month")
    theirs = await governed.key_for(PERSON)
    await governed.settle()

    served = await governed.generate_as(
        theirs, {"contents": [{"role": "user", "parts": [{"text": "hello"}]}]}, model=MOCK_MODEL
    )
    assert served.status_code == 200, served.text

    deadline = time.monotonic() + WITHIN_SECONDS
    mails = await _mails_about(governed.slug)
    while not mails and time.monotonic() < deadline:
        await asyncio.sleep(5)
        mails = await _mails_about(governed.slug)

    assert len(mails) == 1, f"no mail to {ADDRESS} about {governed.slug} within {WITHIN_SECONDS}s"
    (mail,) = mails
    assert "is used up" in mail["Subject"]
    assert "Requests  1 of 1" in mail["Text"]
    assert "It is the budget each member of the use case has." in mail["Text"]

    async with engine.connect() as connection:
        notices = (
            await connection.execute(
                text(
                    "SELECT threshold, outcome, recipients FROM mail_notices"
                    " WHERE use_case = :slug AND kind = 'budget'"
                ),
                {"slug": governed.slug},
            )
        ).all()
    assert [tuple(row) for row in notices] == [(100, "sent", 1)]
