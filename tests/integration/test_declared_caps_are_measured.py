"""The output cap the catalog declares is a fact about the model, not a plausible number.

`FRD-114` FR-7 is usually met from one side: *undeclared means unsupported*, so a capability nobody
measured is a capability nobody gets. This is the other side, and it costs traffic rather than
correctness — **a declared bound that is lower than the model's** refuses requests the model would
have served, with a message that reads like the model's own limit.

It happened. `max_output_tokens` was declared `4096` for `qwen3:0.6b`, sitting between two fields
that each carry a "measured on …" note and having none of its own. The model's context window is
**40960**, and the runtime accepts any `max_tokens` at all — 32 000, 40 961, 100 000 — truncating at
the window rather than refusing. So the first ordinary request from an agentic coding client, which
asks for a large output budget as a matter of course, was answered

    maxOutputTokens 32000 exceeds the 4096 this model accepts

on the very use case `FRD-132` set up for that client. The control was working. The number it
enforced was invented.

**Asked of the runtime, not of a constant here.** A test that hard-coded 40960 would be a second
place to write the same guess, and would go stale the day somebody pulls a different model — the
defect one file over. It asks Ollama what the model says about itself and compares that with what
the catalog claims, which is what makes it a measurement rather than a restatement.
"""

from __future__ import annotations

import httpx
import pytest
import stack_addresses
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .governed import CHAT_MODEL

pytestmark = pytest.mark.integration

#: Where the model runtime is, as the stack configures it. Read from the gateway's own setting so a
#: deployment that moves Ollama does not have to remember this file.
OLLAMA_URL = stack_addresses.url("ollama")


def _runtime_context_length(model: str) -> int | None:
    """What the runtime says the model's context window is, or ``None`` if it cannot be asked."""
    try:
        response = httpx.post(f"{OLLAMA_URL}/api/show", json={"model": model}, timeout=20.0)
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    info = response.json().get("model_info", {})
    for key, value in info.items():
        if key.endswith(".context_length"):
            return int(value)
    return None


async def _declared_cap(engine: AsyncEngine, model: str) -> int | None:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text("SELECT max_output_tokens FROM model_catalog WHERE model = :model"),
                {"model": model},
            )
        ).first()
    if row is None:
        pytest.skip(f"{model} is not catalogued in this deployment")
    return None if row[0] is None else int(row[0])


async def test_the_declared_output_cap_matches_what_the_runtime_reports(
    engine: AsyncEngine,
) -> None:
    """The catalog's ceiling for the local chat model is its context window.

    Equality rather than "at least", because both directions are defects and they fail differently.
    **Too low** refuses traffic the model would serve, in words that blame the model — the case
    this was written for. **Too high** promises output the model cannot produce, so a caller sizing
    a request from the catalog gets a truncated answer with a 200, which is the silent half.
    """
    reported = _runtime_context_length(CHAT_MODEL)
    if reported is None:
        pytest.skip(f"the model runtime at {OLLAMA_URL} could not be asked about {CHAT_MODEL}")
    declared = await _declared_cap(engine, CHAT_MODEL)

    assert declared == reported, (
        f"the catalog declares {declared} output tokens for {CHAT_MODEL} and the runtime reports a "
        f"context window of {reported}. Too low refuses requests the model would have served, in a "
        f"message that reads as the model's own limit; too high promises output it cannot produce."
    )


async def test_a_request_at_the_declared_cap_is_accepted(engine: AsyncEngine, governed) -> None:
    """The declaration read back through the gateway, which is where it is enforced.

    Asserting the *catalog* alone would leave the enforcement untested — and the enforcement is the
    half a caller meets. A cap the catalog declares and the gateway refuses is the same defect one
    layer along.
    """
    declared = await _declared_cap(engine, CHAT_MODEL)
    if declared is None:
        pytest.skip(f"{CHAT_MODEL} declares no output cap in this deployment")

    response = await governed.generate(
        {
            "contents": [{"role": "user", "parts": [{"text": "Say OK."}]}],
            # At the cap, not near it: the refusal this exists for is a strict `>` comparison, so
            # anything below the boundary would pass against the broken version too.
            "generationConfig": {"maxOutputTokens": declared},
        }
    )

    assert response.status_code == 200, response.text[:300]


async def test_one_token_past_the_declared_cap_is_refused_naming_the_bound(
    engine: AsyncEngine, governed
) -> None:
    """The control itself, which is not in question and must stay. A catalog that declared a
    ceiling nobody enforced would be a number in a table."""
    declared = await _declared_cap(engine, CHAT_MODEL)
    if declared is None:
        pytest.skip(f"{CHAT_MODEL} declares no output cap in this deployment")

    response = await governed.generate(
        {
            "contents": [{"role": "user", "parts": [{"text": "Say OK."}]}],
            "generationConfig": {"maxOutputTokens": declared + 1},
        }
    )

    assert response.status_code == 400, response.text[:300]
    message = response.json()["error"]["message"]
    assert str(declared) in message, message
    assert "maxOutputTokens" in message, message


async def test_an_agentic_clients_ordinary_output_budget_is_served(governed) -> None:
    """The request that started this, as the client actually sends it.

    OpenCode asks for 32 000 output tokens on an ordinary turn — not because it expects to use
    them, but because that is how an agentic client sizes a budget it cannot predict. Named as its
    own case rather than folded into the cap test above, because what broke was not "the boundary
    is off by one": it was that the demo's own coding assistant could not make its first request.
    """
    response = await governed.generate(
        {
            "contents": [{"role": "user", "parts": [{"text": "Say OK."}]}],
            "generationConfig": {"maxOutputTokens": 32_000},
        }
    )

    assert response.status_code == 200, response.text[:300]
