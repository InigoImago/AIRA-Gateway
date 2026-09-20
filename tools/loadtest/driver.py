"""The load driver (`FRD-136` FR-3, FR-4, FR-5).

Three workloads the owner named, one that measures the gateway alone, and the same four driven
**straight at the double** so that a figure about the gateway is not a figure about this script.

Spread across processes on purpose. One Python process holding three hundred SSE streams spends
its time in its own event loop, and every percentile it reports is then a percentile of the driver
— the most common way a load test lies. `--processes` defaults to something the machine can carry
and each process reports how much of its own wall clock it spent runnable, so that a run which
*did* become driver-bound says so rather than being believed.

    uv run python -m tools.loadtest.driver --profile agentic --users 100 --duration 60
    uv run python -m tools.loadtest.driver --profile agentic --users 100 --target direct
    uv run python -m tools.loadtest.driver --profile chat --ramp 25,50,100,200,300 --duration 45
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import json
import math
import multiprocessing as mp
import os
import random
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx
from tools.loadtest import addresses, seed
from tools.loadtest.models import (
    BY_NAME,
    EMBEDDING_BY_NAME,
    TOOL_CALL_TOKENS,
    TOOL_MARKER,
    SimModel,
)

#: The vocabulary a synthetic prompt is built from, and its **size is a measurement decision**.
#:
#: Twelve words cycled reads like filler and stores like nothing: Postgres compresses a large
#: `json` value before writing it out of line, and a repeated dozen words compressed by about
#: fifty to one — so a 24 kB agentic prompt was measured at **2 kB** on disk, and the audit trail's
#: growth rate, which is the figure an installation sizes a volume from, was wrong by more than an
#: order of magnitude in the comfortable direction.
#:
#: Four thousand distinct tokens, deterministic from a fixed seed, put the entropy roughly where
#: prose is. Nothing else changes: the JSON encoding the gateway pays for is a function of length,
#: not of what the letters are.
_VOCABULARY_SIZE = 4_000
_FILLER = [
    "".join(
        "abcdefghijklmnopqrstuvwxyz"[(index * 7 + position * 31 + index // 26) % 26]
        for position in range(3 + index % 7)
    )
    + str(index % 97)
    for index in range(_VOCABULARY_SIZE)
]


@functools.lru_cache(maxsize=512)
def _text(tokens: int, salt: int = 0) -> str:
    """``tokens`` tokens of filler, varying with ``salt`` so a cache cannot flatter a run.

    Memoised because the driver shares the machine with the thing it is measuring: building twelve
    thousand words per request costs 0.9 ms of driver CPU, and at three hundred callers that is
    driver time bought back for nothing. The `salt` keeps the *bodies* different between callers,
    which is what matters — a prompt cache upstream must not see the same text twice.
    """
    return " ".join(
        _FILLER[(index * 131 + salt * 7919) % len(_FILLER)] for index in range(max(1, tokens))
    )


@dataclass(slots=True)
class Sample:
    """One request, as the driver saw it."""

    started: float
    elapsed: float
    #: Seconds to the first byte of answer. ``None`` for anything not streamed.
    ttft: float | None
    status: int
    ok: bool
    #: What the double promised this answer would take, from its own declaration. The figure
    #: `FRD-136` FR-5 subtracts; without it every number here is a statement about the double.
    declared: float
    error: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass(slots=True)
class Profile:
    """A workload: what it sends, to which use case, and how often one person sends it."""

    name: str
    use_case: str
    model: str
    #: Seconds a simulated person waits between finishing one request and starting the next.
    think_time: tuple[float, float]
    #: Tokens of prompt this workload carries, and how many it asks the model to produce.
    context_tokens: int
    output_tokens: int
    streaming: bool = True
    tools: bool = False
    thinking: bool = False
    #: Share of turns in which the caller comes back with a tool result, so the prompt grows.
    tool_turn_share: float = 0.0
    embedding_batch: int = 0
    embedding_text_tokens: int = 0
    #: How much the context grows per turn of one simulated session, in tokens. An agentic session
    #: does not send the same prompt twice, and a run in which it does measures prompt caching.
    context_growth: int = 0
    turns_per_session: int = 1
    #: Ask for the reasoning to be **returned** (`FRD-135` FR-3). Only on an unstreamed request:
    #: the Gemini surface refuses `includeThoughts` on a stream by design, because a stream that
    #: silently carried no thoughts would look complete. A client that wants the thinking back
    #: therefore cannot stream, and that is a different code path with a different cost — which is
    #: why it is a profile of its own rather than a flag on the streamed one.
    include_thoughts: bool = False


PROFILES: dict[str, Profile] = {
    # Agentic coding: a long system prompt and a repository excerpt, thinking, tools, and a
    # session whose context grows with every tool result carried back.
    "agentic": Profile(
        name="agentic",
        use_case=f"{seed.PREFIX}agentic",
        model="sim-coder",
        think_time=(2.0, 8.0),
        context_tokens=6_000,
        output_tokens=600,
        streaming=True,
        tools=True,
        thinking=True,
        tool_turn_share=0.6,
        context_growth=1_200,
        turns_per_session=6,
    ),
    # A chatbot with a RAG in front of it: a large retrieved context, a short answer, no tools.
    "chat": Profile(
        name="chat",
        use_case=f"{seed.PREFIX}chat",
        model="sim-chat",
        think_time=(8.0, 25.0),
        context_tokens=4_000,
        output_tokens=220,
        streaming=True,
        turns_per_session=4,
        context_growth=300,
    ),
    # The same, from a runtime that batches eight tokens per event. The pair answers "how much of
    # the gateway's cost is per request and how much is per streamed event".
    "chat-batched": Profile(
        name="chat-batched",
        use_case=f"{seed.PREFIX}chat",
        model="sim-chat-batched",
        think_time=(8.0, 25.0),
        context_tokens=4_000,
        output_tokens=220,
        streaming=True,
        turns_per_session=4,
        context_growth=300,
    ),
    # The same work, with the reasoning **returned** — which on this surface means not streaming
    # (`FRD-135`). The pair says what an agentic client pays for seeing the model's thoughts.
    "agentic-thoughts": Profile(
        name="agentic-thoughts",
        use_case=f"{seed.PREFIX}agentic",
        model="sim-coder",
        think_time=(2.0, 8.0),
        context_tokens=6_000,
        output_tokens=600,
        streaming=False,
        tools=True,
        thinking=True,
        include_thoughts=True,
        tool_turn_share=0.6,
        context_growth=1_200,
        turns_per_session=6,
    ),
    # The nightly job: few callers, enormous bodies, no streaming and no thinking.
    "embed": Profile(
        name="embed",
        use_case=f"{seed.PREFIX}embed",
        model="sim-embed",
        think_time=(0.0, 0.0),
        context_tokens=0,
        output_tokens=0,
        streaming=False,
        embedding_batch=128,
        embedding_text_tokens=120,
    ),
    # No upstream latency at all, so everything the clock shows is the gateway.
    "bare": Profile(
        name="bare",
        use_case=f"{seed.PREFIX}bare",
        model="sim-instant",
        think_time=(0.0, 0.0),
        context_tokens=400,
        output_tokens=32,
        streaming=False,
    ),
    # The same, streamed, because a stream is a different code path in the gateway and the
    # difference between the two is the cost of streaming itself.
    "bare-stream": Profile(
        name="bare-stream",
        use_case=f"{seed.PREFIX}bare",
        model="sim-instant",
        think_time=(0.0, 0.0),
        context_tokens=400,
        output_tokens=32,
        streaming=True,
    ),
}


# == building one request =========================================================================


def _gemini_body(profile: Profile, turn: int, salt: int, tool_call: bool) -> dict[str, Any]:
    context = profile.context_tokens + profile.context_growth * turn
    marker = TOOL_MARKER if tool_call else ""
    contents: list[dict[str, Any]] = [
        {
            "role": "user",
            "parts": [{"text": f"{marker} {_text(context, salt)}"}],
        }
    ]
    body: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {"maxOutputTokens": profile.output_tokens, "temperature": 0.2},
    }
    if profile.tools:
        body["tools"] = [
            {
                "functionDeclarations": [
                    {
                        "name": "read_file",
                        "description": "Read a file from the repository.",
                        "parameters": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    }
                ]
            }
        ]
    if profile.thinking:
        # Not `includeThoughts`: the Gemini surface refuses that on a stream by design
        # (`FRD-135`), and an agentic client streams. The model thinks and is billed either way,
        # which is what this profile is about.
        thinking: dict[str, Any] = {"mode": "high"}
        if profile.include_thoughts:
            thinking["includeThoughts"] = True
        body["generationConfig"]["thinkingConfig"] = thinking
    return body


def _openai_body(
    profile: Profile, turn: int, salt: int, tool_call: bool, *, stream: bool
) -> dict[str, Any]:
    """The same request as the double would receive it, for the baseline that skips the gateway."""
    context = profile.context_tokens + profile.context_growth * turn
    marker = TOOL_MARKER if tool_call else ""
    body: dict[str, Any] = {
        "model": profile.model,
        "messages": [{"role": "user", "content": f"{marker} {_text(context, salt)}"}],
        "max_tokens": profile.output_tokens,
    }
    if profile.tools:
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a file from the repository.",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
            }
        ]
    if profile.thinking:
        body["reasoning_effort"] = "high"
    if stream:
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
    return body


def _declared(profile: Profile, tool_call: bool = False) -> float:
    """What the double promises **this** request will take, before the gateway touches it.

    Per request, not per profile: a turn that ends in a tool call stops generating, so subtracting
    a full answer's promised time from it would report several seconds of *negative* overhead and
    flatter the gateway by exactly the share of turns that called a tool.
    """
    if profile.embedding_batch:
        embed = EMBEDDING_BY_NAME[profile.model]
        return (embed.base_ms + embed.ms_per_text * profile.embedding_batch) / 1000.0
    model: SimModel = BY_NAME[profile.model]
    answer, reasoning = model.plan(profile.output_tokens, thinking=True)
    if tool_call:
        answer = TOOL_CALL_TOKENS
    return model.declared_seconds(answer, reasoning)


# == one request, through the gateway or straight at the double ===================================


async def _gateway_request(
    client: httpx.AsyncClient, profile: Profile, key: str, turn: int, salt: int
) -> Sample:
    headers = {"x-goog-api-key": key, "content-type": "application/json"}

    if profile.embedding_batch:
        body = {
            "requests": [
                {
                    "model": f"models/{profile.model}",
                    "content": {"parts": [{"text": _text(profile.embedding_text_tokens, index)}]},
                }
                for index in range(profile.embedding_batch)
            ]
        }
        url = f"/v1beta/models/{profile.model}:batchEmbedContents"
        # **The clock starts after the body exists.** Building twelve thousand words of prompt is
        # the driver's work, not the gateway's, and it was inside the measurement until it was
        # timed: 0.4–0.9 ms, inside the noise here but not in a profile with a larger body.
        return await _unstreamed(client, url, body, headers, time.monotonic(), _declared(profile))

    tool_call = profile.tools and random.random() < profile.tool_turn_share
    declared = _declared(profile, tool_call)
    body = _gemini_body(profile, turn, salt, tool_call)
    started = time.monotonic()
    if not profile.streaming:
        url = f"/v1beta/models/{profile.model}:generateContent"
        return await _unstreamed(client, url, body, headers, started, declared)

    url = f"/v1beta/models/{profile.model}:streamGenerateContent?alt=sse"
    return await _streamed(client, url, body, headers, started, declared)


async def _direct_request(
    client: httpx.AsyncClient, profile: Profile, key: str, turn: int, salt: int
) -> Sample:
    """The baseline: the same load at the double, with nothing in between (`FRD-136` FR-4)."""
    headers = {"content-type": "application/json"}

    if profile.embedding_batch:
        body = {
            "model": profile.model,
            "input": [
                _text(profile.embedding_text_tokens, index)
                for index in range(profile.embedding_batch)
            ],
        }
        return await _unstreamed(
            client, "/v1/embeddings", body, headers, time.monotonic(), _declared(profile)
        )

    tool_call = profile.tools and random.random() < profile.tool_turn_share
    declared = _declared(profile, tool_call)
    body = _openai_body(profile, turn, salt, tool_call, stream=profile.streaming)
    started = time.monotonic()
    if not profile.streaming:
        return await _unstreamed(client, "/v1/chat/completions", body, headers, started, declared)
    return await _streamed(client, "/v1/chat/completions", body, headers, started, declared)


async def _unstreamed(
    client: httpx.AsyncClient,
    url: str,
    body: dict[str, Any],
    headers: dict[str, str],
    started: float,
    declared: float,
) -> Sample:
    try:
        response = await client.post(url, json=body, headers=headers)
    except Exception as exc:  # noqa: BLE001 — every failure kind is a result, not an abort
        return Sample(started, time.monotonic() - started, None, 0, False, declared, _kind(exc))
    elapsed = time.monotonic() - started
    prompt, completion = _counts(response)
    return Sample(
        started,
        elapsed,
        None,
        response.status_code,
        response.status_code == 200,
        declared,
        "" if response.status_code == 200 else _detail(response),
        prompt,
        completion,
    )


async def _streamed(
    client: httpx.AsyncClient,
    url: str,
    body: dict[str, Any],
    headers: dict[str, str],
    started: float,
    declared: float,
) -> Sample:
    ttft: float | None = None
    prompt = completion = 0
    try:
        async with client.stream("POST", url, json=body, headers=headers) as response:
            if response.status_code != 200:
                await response.aread()
                return Sample(
                    started,
                    time.monotonic() - started,
                    None,
                    response.status_code,
                    False,
                    declared,
                    _detail(response),
                )
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                if ttft is None:
                    ttft = time.monotonic() - started
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    chunk = json.loads(payload)
                except ValueError:
                    continue
                usage = chunk.get("usageMetadata") or chunk.get("usage") or {}
                prompt = max(
                    prompt, int(usage.get("promptTokenCount", usage.get("prompt_tokens", 0)) or 0)
                )
                completion = max(
                    completion,
                    int(usage.get("candidatesTokenCount", usage.get("completion_tokens", 0)) or 0),
                )
    except Exception as exc:  # noqa: BLE001
        return Sample(started, time.monotonic() - started, ttft, 0, False, declared, _kind(exc))
    return Sample(
        started, time.monotonic() - started, ttft, 200, True, declared, "", prompt, completion
    )


def _counts(response: httpx.Response) -> tuple[int, int]:
    try:
        data = response.json()
    except ValueError:
        return 0, 0
    usage = data.get("usageMetadata") or data.get("usage") or {}
    prompt = int(usage.get("promptTokenCount", usage.get("prompt_tokens", 0)) or 0)
    completion = int(usage.get("candidatesTokenCount", usage.get("completion_tokens", 0)) or 0)
    return prompt, completion


def _kind(exc: BaseException) -> str:
    """A failure's *kind*, not its message: the message carries an address and a port, and a
    tally keyed by it would have one entry per connection."""
    return type(exc).__name__


def _detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    error = payload.get("error") or {}
    status = error.get("status") or error.get("code") or response.status_code
    message = str(error.get("message") or "")[:120]
    return f"{status}: {message}" if message else f"HTTP {response.status_code}"


# == one simulated person, and a process full of them =============================================


async def _person(
    client: httpx.AsyncClient,
    profile: Profile,
    key: str,
    index: int,
    deadline: float,
    target: str,
    samples: list[Sample],
    stop: asyncio.Event,
) -> None:
    turn = 0
    request = _gateway_request if target == "gateway" else _direct_request
    # A stagger, so a run does not begin with every person sending at the same millisecond. That
    # opening spike is real in nothing, and it is what makes a first percentile unreadable.
    await asyncio.sleep(random.random() * min(2.0, max(profile.think_time)) or random.random())
    while time.monotonic() < deadline and not stop.is_set():
        sample = await request(client, profile, key, turn, index)
        samples.append(sample)
        turn = (turn + 1) % max(1, profile.turns_per_session)
        low, high = profile.think_time
        if high > 0:
            await asyncio.sleep(random.uniform(low, high))
        elif time.monotonic() < deadline:
            # A job with no think time still yields, or one person's loop starves the others.
            await asyncio.sleep(0)


def _worker(arguments: dict[str, Any], connection: Any) -> None:
    connection.send(asyncio.run(_run_worker(arguments)))
    connection.close()


async def _run_worker(arguments: dict[str, Any]) -> dict[str, Any]:
    profile = PROFILES[arguments["profile"]]
    target = arguments["target"]
    people = arguments["people"]
    base = addresses.gateway() if target == "gateway" else addresses.modelsim()

    random.seed(arguments["offset"])
    samples: list[Sample] = []
    stop = asyncio.Event()
    limits = httpx.Limits(
        max_connections=len(people) + 8, max_keepalive_connections=len(people) + 8
    )
    timeout = httpx.Timeout(arguments["timeout"], connect=10.0)
    started_at = time.monotonic()
    deadline = started_at + arguments["duration"]

    async with httpx.AsyncClient(base_url=base, limits=limits, timeout=timeout) as client:
        await asyncio.gather(
            *(
                _person(client, profile, key, index, deadline, target, samples, stop)
                for index, key in enumerate(people)
            )
        )
    return {
        "samples": [asdict(sample) for sample in samples],
        "wall": time.monotonic() - started_at,
        # How much CPU this driver process burned. Divided by its wall clock it is the share of a
        # core it needed; a figure near 1.0 means the run measured the driver (`FRD-136` FR-4).
        "cpu": sum(os.times()[:2]),
    }


# == what a run reports ===========================================================================


@dataclass(slots=True)
class Result:
    profile: str
    target: str
    users: int
    duration: float
    completed: int = 0
    failed: int = 0
    failures: dict[str, int] = field(default_factory=dict)
    rps: float = 0.0
    latency: dict[str, float] = field(default_factory=dict)
    ttft: dict[str, float] = field(default_factory=dict)
    overhead: dict[str, float] = field(default_factory=dict)
    declared: float = 0.0
    tokens_per_second: float = 0.0
    driver_cores: float = 0.0


def _percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values)

    def at(fraction: float) -> float:
        index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
        return round(ordered[index] * 1000, 1)

    return {
        "p50_ms": at(0.50),
        "p90_ms": at(0.90),
        "p99_ms": at(0.99),
        "max_ms": round(ordered[-1] * 1000, 1),
        "mean_ms": round(statistics.fmean(ordered) * 1000, 1),
    }


def summarise(
    profile: Profile, target: str, users: int, duration: float, payloads: list[dict[str, Any]]
) -> Result:
    samples = [Sample(**raw) for payload in payloads for raw in payload["samples"]]
    ok = [sample for sample in samples if sample.ok]
    bad = [sample for sample in samples if not sample.ok]
    wall = max((payload["wall"] for payload in payloads), default=duration)

    failures: dict[str, int] = {}
    for sample in bad:
        failures[sample.error or f"HTTP {sample.status}"] = (
            failures.get(sample.error or f"HTTP {sample.status}", 0) + 1
        )

    result = Result(profile.name, target, users, round(wall, 1))
    result.completed = len(ok)
    result.failed = len(bad)
    result.failures = dict(sorted(failures.items(), key=lambda item: -item[1])[:8])
    result.rps = round(len(ok) / wall, 2) if wall else 0.0
    result.latency = _percentiles([sample.elapsed for sample in ok])
    result.ttft = _percentiles([sample.ttft for sample in ok if sample.ttft is not None])
    # The figure the owner's question turns on: what the request cost beyond what the model
    # promised. Negative would mean the double under-slept, so it is reported unclamped.
    result.overhead = _percentiles([sample.elapsed - sample.declared for sample in ok])
    result.declared = (
        round(statistics.fmean([sample.declared for sample in ok]) * 1000, 1) if ok else 0.0
    )
    result.tokens_per_second = (
        round(sum(sample.completion_tokens for sample in ok) / wall, 1) if wall else 0.0
    )
    result.driver_cores = (
        round(sum(payload["cpu"] for payload in payloads) / wall, 2) if wall else 0.0
    )
    return result


def run(
    profile_name: str,
    *,
    users: int,
    duration: float,
    target: str = "gateway",
    processes: int = 4,
    timeout: float = 120.0,
) -> Result:
    profile = PROFILES[profile_name]
    workload = seed.BY_SLUG[profile.use_case]
    keys = [seed.key_for(profile.use_case, index % workload.people) for index in range(users)]

    processes = max(1, min(processes, users))
    context = mp.get_context("spawn")
    children: list[tuple[Any, Any]] = []
    for number in range(processes):
        share = keys[number::processes]
        if not share:
            continue
        parent, child = context.Pipe(duplex=False)
        arguments = {
            "profile": profile_name,
            "target": target,
            "people": share,
            "duration": duration,
            "offset": number * 1000 + users,
            "timeout": timeout,
        }
        process = context.Process(target=_worker, args=(arguments, child), daemon=True)
        process.start()
        child.close()
        children.append((process, parent))

    payloads: list[dict[str, Any]] = []
    for process, parent in children:
        payloads.append(parent.recv())
        parent.close()
        process.join()
    return summarise(profile, target, users, duration, payloads)


def _print(result: Result) -> None:
    print(json.dumps(asdict(result), indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, choices=sorted(PROFILES))
    parser.add_argument("--users", type=int, default=50)
    parser.add_argument("--ramp", default="", help="comma-separated user counts, run in order")
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--target", default="gateway", choices=("gateway", "direct"))
    parser.add_argument("--processes", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--out", default="", help="append each result as JSON to this file")
    arguments = parser.parse_args()

    steps = (
        [int(value) for value in arguments.ramp.split(",") if value.strip()]
        if arguments.ramp
        else [arguments.users]
    )
    for users in steps:
        result = run(
            arguments.profile,
            users=users,
            duration=arguments.duration,
            target=arguments.target,
            processes=arguments.processes,
            timeout=arguments.timeout,
        )
        _print(result)
        if arguments.out:
            with open(arguments.out, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(result)) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
