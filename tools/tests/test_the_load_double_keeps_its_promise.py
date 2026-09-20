"""A load run's figures rest on the double telling the truth about itself (`FRD-136`).

`overhead` — the number the whole feature exists to produce — is *measured time less promised
time*. So a double that takes longer than it promised does not look slow; it makes the **gateway**
look slow, by exactly the difference, and nothing in the report says so.

That is not hypothetical. Two ways of getting it wrong were found here by measuring rather than by
reasoning, and both are checked below:

1. **The promise counted tokens where the double waits per event.** `declared_seconds` was short
   by one gap per stream; against the clock a promised 6.928 s answer took 7.020 s, and the 92 ms
   would have been charged to the gateway on every streamed request.
2. **Chained sleeps drift.** Each `asyncio.sleep` returns a little late, and a 600-token answer is
   600 of them — measured at about 1.2 ms of overshoot each, which is 0.7 s per request. The
   double schedules against a deadline instead, and `_Clock` is what this file's last test is
   about.

These are hermetic: they compare the promise with what the double **emits**, never with a wall
clock, because a test that asserts on real timing fails on a busy machine and teaches everyone to
re-run it.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from tools.loadtest import driver, seed
from tools.loadtest.models import BY_NAME, CHAT_MODELS, TOOL_CALL_TOKENS, TOOL_MARKER
from tools.loadtest.modelsim import app


async def _collect(body: dict[str, Any]) -> tuple[int, list[dict[str, Any]], float]:
    """Drive the ASGI app directly and return the status, the SSE payloads, and the promised wait.

    The `_Clock` is replaced by one that records what it was asked to wait for instead of waiting,
    so the assertions are about the schedule the double *intends* and run in milliseconds.
    """
    waited = 0.0
    sent: list[dict[str, Any]] = []
    status = 0

    class _Instant:
        def __init__(self, first: float) -> None:
            nonlocal waited
            waited += first

        async def wait(self) -> None:
            return None

        async def advance(self, gap: float) -> None:
            nonlocal waited
            waited += gap

    from tools.loadtest import modelsim

    original_clock, original_sleep = modelsim._Clock, modelsim._sleep

    async def _record(seconds: float) -> None:
        nonlocal waited
        waited += seconds

    modelsim._Clock = _Instant  # type: ignore[assignment]
    modelsim._sleep = _record  # type: ignore[assignment]

    raw = json.dumps(body).encode()

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": raw, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]
        elif message["type"] == "http.response.body":
            sent.append({"body": message.get("body", b"")})

    try:
        await app({"type": "http", "path": "/v1/chat/completions"}, receive, send)
    finally:
        modelsim._Clock, modelsim._sleep = original_clock, original_sleep

    payloads = []
    for entry in sent:
        for line in entry["body"].decode().splitlines():
            if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]"):
                payloads.append(json.loads(line[5:]))
    return status, payloads, waited


@pytest.mark.parametrize("model", [model.name for model in CHAT_MODELS if model.ttft_ms > 0])
@pytest.mark.parametrize("max_tokens", [60, 220, 601])
async def test_a_streamed_answer_takes_exactly_as_long_as_it_promised(
    model: str, max_tokens: int
) -> None:
    """The promise and the schedule are the same arithmetic, for every model and every length.

    Parametrised over lengths that are **not** multiples of any `tokens_per_chunk`, because the
    defect this replaces was a rounding one: with 600 tokens at eight to an event the two
    expressions agreed to within a gap, and the disagreement only showed at 601.
    """
    declaration = BY_NAME[model]
    answer, reasoning = declaration.plan(max_tokens)
    _, _, waited = await _collect(
        {
            "model": model,
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": max_tokens,
            "stream": True,
        }
    )

    assert waited == pytest.approx(declaration.declared_seconds(answer, reasoning), abs=1e-9)


@pytest.mark.parametrize("model", [model.name for model in CHAT_MODELS if model.ttft_ms > 0])
async def test_an_unstreamed_answer_promises_the_same_as_a_streamed_one(model: str) -> None:
    """A caller who does not stream waits the same. Otherwise `overhead` would mean one thing on
    one verb and another on the other, and the report puts them in one column."""
    declaration = BY_NAME[model]
    answer, reasoning = declaration.plan(200)
    _, _, waited = await _collect(
        {
            "model": model,
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 200,
        }
    )

    assert waited == pytest.approx(declaration.declared_seconds(answer, reasoning), abs=1e-9)


async def test_a_turn_that_calls_a_tool_is_short_and_the_driver_knows_it() -> None:
    """A model that calls a function stops generating, so the turn is short.

    The driver has to subtract *that* promise rather than a full answer's, or the sixty per cent of
    agentic turns that end in a tool call each report several seconds of **negative** overhead —
    flattering the gateway by exactly the share of turns that used the feature under test.
    """
    declaration = BY_NAME["sim-coder"]
    body = {
        "model": "sim-coder",
        "messages": [{"role": "user", "content": f"{TOOL_MARKER} read it"}],
        "max_tokens": 600,
        "stream": True,
        "tools": [{"type": "function", "function": {"name": "read_file"}}],
    }
    status, payloads, waited = await _collect(body)
    _, reasoning = declaration.plan(600)

    assert status == 200
    assert any(
        (payload.get("choices") or [{}])[0].get("delta", {}).get("tool_calls")
        for payload in payloads
    )
    assert waited == pytest.approx(
        declaration.declared_seconds(TOOL_CALL_TOKENS, reasoning), abs=1e-9
    )
    profile = driver.PROFILES["agentic"]
    assert driver._declared(profile, tool_call=True) == pytest.approx(waited, abs=1e-9)
    assert driver._declared(profile, tool_call=False) > waited


async def test_thinking_switched_off_is_thinking_not_done() -> None:
    """`reasoning_effort: "none"` is the caller switching thinking off (`FRD-111`). A double that
    thought anyway would make that control untestable under load — it would cost the same either
    way, and the test that noticed would be nobody's."""
    declaration = BY_NAME["sim-coder"]
    _, payloads, waited = await _collect(
        {
            "model": "sim-coder",
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 200,
            "reasoning_effort": "none",
            "stream": True,
        }
    )

    assert waited == pytest.approx(declaration.declared_seconds(200, 0), abs=1e-9)
    assert not any(
        (payload.get("choices") or [{}])[0].get("delta", {}).get("reasoning")
        for payload in payloads
    )


async def test_the_clock_does_not_drift_over_a_long_answer() -> None:
    """The real `_Clock`, with a real event loop: a schedule measured from one start.

    Six hundred gaps of a millisecond each. Chained `asyncio.sleep` calls would land well past
    600 ms — that overshoot is the 0.7 s this class was written to remove — while a deadline-based
    clock lands near it however late any single wait returns. The bound is generous on purpose: it
    fails on *drift*, which is proportional to the number of waits, not on a busy machine.
    """
    from tools.loadtest.modelsim import _Clock

    loop_start = asyncio.get_running_loop().time()
    clock = _Clock(0.0)
    for _ in range(600):
        await clock.advance(0.001)
    elapsed = asyncio.get_running_loop().time() - loop_start

    assert elapsed < 0.6 + 0.2


def test_the_seed_removes_every_table_it_writes() -> None:
    """A load run's rows are active credentials and false audit history. Whatever `create` writes,
    `clean` has to name — and a table added to one and not the other leaves rows behind under a
    prefix nobody looks for again."""
    created = set(seed.create.__code__.co_consts)
    written = {
        statement.split("INSERT INTO ")[1].split(" ")[0].split("(")[0]
        for statement in created
        if isinstance(statement, str) and "INSERT INTO " in statement
    }
    removed = {
        statement.split("DELETE FROM ")[1].split(" ")[0]
        for statement in seed.clean.__code__.co_consts
        if isinstance(statement, str) and "DELETE FROM " in statement
    }

    assert written, "this test no longer finds the seed's INSERT statements"
    assert written <= removed, f"seeded but never cleaned: {sorted(written - removed)}"


def test_every_profile_names_a_use_case_the_seed_creates() -> None:
    """A profile pointed at a use case nobody seeded fails with 401 on its first request, which
    reads as an authentication defect in the gateway rather than as a typo here."""
    unknown = {
        name: profile.use_case
        for name, profile in driver.PROFILES.items()
        if profile.use_case not in seed.BY_SLUG
    }

    assert not unknown, f"profiles naming a use case the seed does not create: {unknown}"


def test_every_profile_names_a_model_its_use_case_released() -> None:
    """`FRD-308`'s release is named in the seed, so a profile addressing a model outside it is
    refused — correctly, and after a run has already been started."""
    wrong = {
        name: (profile.model, seed.BY_SLUG[profile.use_case].models)
        for name, profile in driver.PROFILES.items()
        if profile.model not in seed.BY_SLUG[profile.use_case].models
    }

    assert not wrong, f"profiles addressing a model their use case has not released: {wrong}"


def test_a_rate_limit_admits_the_heaviest_request_the_run_sends() -> None:
    """A batch weighs its own size (`FRD-405` §4.2), so a burst below the largest batch refuses
    that request outright, whatever the per-minute allowance says. Measured: an embedding profile
    seeded at `burst 80` refused every 128-text batch with `RESOURCE_EXHAUSTED` while its rpm was
    4 800 and irrelevant."""
    heaviest = max(profile.embedding_batch for profile in driver.PROFILES.values())

    assert heaviest <= seed.MAX_BATCH_WEIGHT, (
        f"a profile sends batches of {heaviest}, above the {seed.MAX_BATCH_WEIGHT} the seed "
        "guarantees the rate limit's burst will admit"
    )
