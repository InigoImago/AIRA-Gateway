"""Cached input is counted and priced apart from the rest (`FRD-133` stage A).

Measured before it was built: 99.1 % of an assistant turn is content it sent last time — tool
declarations (69 %) and the system prompt (31 %) — and 93.3 % of that use case's tokens are input.
So this is where its bill is, and until now every one of those tokens was charged at the ordinary
rate whether the provider served it from a cache or not.

The three rates are the reason the counts are apart. A read is **0.1x** base input on Anthropic; a
five-minute write is **1.25x** and an hour-long one **2x**. Folding them into one number — which is
what the Anthropic mapping did, literally summing `input + cached + created` — over-bills a read by
ten times and under-bills a write by a quarter. A cost control that is wrong in the *expensive*
direction is worse than one that is absent, which is why the write case below is asserted as
carefully as the read.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from aira_common.money import to_nanos
from aira_gateway.core.canonical import CanonicalUsage
from aira_gateway.db.base import Base
from aira_gateway.db.models import ModelRead
from aira_gateway.pricing import PricingService


@pytest.fixture
async def sessions():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _priced(sessions, **prices: str | None) -> None:
    async with sessions() as session:
        session.add(
            ModelRead(
                model="m1",
                input_price_per_million_nanos=to_nanos("10.00"),
                output_price_per_million_nanos=to_nanos("30.00"),
                cached_input_price_per_million_nanos=(
                    to_nanos(prices["cached"]) if prices.get("cached") else None
                ),
                cache_write_price_per_million_nanos=(
                    to_nanos(prices["write"]) if prices.get("write") else None
                ),
            )
        )
        await session.commit()


# ---- the arithmetic ------------------------------------------------------------------------


def test_the_parts_are_subsets_of_the_input_and_never_added_to_it() -> None:
    """`prompt_tokens` keeps meaning **all** input. Every budget, report and index in the system is
    built on it, and a feature that redefined it would silently move all of them."""
    usage = CanonicalUsage(
        prompt_tokens=1000, completion_tokens=50, cached_input_tokens=800, cache_write_tokens=100
    )

    assert usage.uncached_input_tokens == 100
    assert usage.total_tokens == 1050


def test_a_provider_contradicting_itself_cannot_produce_a_negative_charge() -> None:
    """More cache tokens than input tokens is not a state any vendor documents, which is exactly
    why it is worth clamping: one malformed response must not bill a negative amount."""
    usage = CanonicalUsage(
        prompt_tokens=100, completion_tokens=0, cached_input_tokens=900, cache_write_tokens=0
    )

    assert usage.uncached_input_tokens == 0


async def test_a_cache_read_is_charged_at_its_own_rate(sessions) -> None:
    await _priced(sessions, cached="1.00", write=None)
    usage = CanonicalUsage(
        prompt_tokens=1_000_000, completion_tokens=0, cached_input_tokens=900_000
    )

    cost = await PricingService(sessions).cost_nanos("m1", usage)

    # 100k uncached at 10.00 + 900k cached at 1.00 = 1.00 + 0.90
    assert cost == to_nanos("1.90")


async def test_a_cache_write_costs_more_than_ordinary_input(sessions) -> None:
    """The direction that matters. Anthropic charges 1.25x for a five-minute write and 2x for an
    hour; treating a write as ordinary input under-states the bill, and under-stating is the one
    error a spend control must not make."""
    await _priced(sessions, cached="1.00", write="12.50")
    usage = CanonicalUsage(
        prompt_tokens=1_000_000, completion_tokens=0, cache_write_tokens=1_000_000
    )

    cost = await PricingService(sessions).cost_nanos("m1", usage)

    assert cost == to_nanos("12.50")
    assert cost > to_nanos("10.00"), "a cache write priced as ordinary input under-bills"


async def test_an_undeclared_cache_price_falls_back_to_the_input_rate(sessions) -> None:
    """**Not to zero.** `FRD-403`'s rule one field in: a rate nobody has declared is not a free
    one. Falling back to the ordinary input rate is what the system did before these columns
    existed, so an installation that declares nothing keeps exactly its previous figures."""
    await _priced(sessions, cached=None, write=None)
    usage = CanonicalUsage(
        prompt_tokens=1_000_000,
        completion_tokens=0,
        cached_input_tokens=600_000,
        cache_write_tokens=400_000,
    )

    cost = await PricingService(sessions).cost_nanos("m1", usage)

    assert cost == to_nanos("10.00")


async def test_a_request_with_no_cache_costs_exactly_what_it_did_before(sessions) -> None:
    """The compatibility property, asserted rather than assumed: three rates must reduce to one
    when no cache was involved, or this feature silently repriced every existing model."""
    await _priced(sessions, cached="1.00", write="12.50")
    usage = CanonicalUsage(prompt_tokens=1_000_000, completion_tokens=1_000_000)

    cost = await PricingService(sessions).cost_nanos("m1", usage)

    assert cost == to_nanos("40.00")


async def test_a_model_with_no_price_at_all_is_still_unpriced(sessions) -> None:
    """Cache columns must not accidentally make an unpriced model priceable — "we do not know what
    this cost" and "this was cheap" are different statements (`FRD-403`)."""
    async with sessions() as session:
        session.add(ModelRead(model="m1", cached_input_price_per_million_nanos=to_nanos("1.00")))
        await session.commit()

    usage = CanonicalUsage(prompt_tokens=100, completion_tokens=1, cached_input_tokens=100)

    assert await PricingService(sessions).cost_nanos("m1", usage) is None


# ---- what each dialect reports (`FRD-133` §4a) ----------------------------------------------
#
# Three of the four providers report cached tokens and only one takes a marker, which is the
# opposite of the intuition that the marker is the feature. These assert the *reading* half, per
# dialect, because a field read from the wrong key is indistinguishable from a cache that never
# hit — and both look like "caching does not work here".


def test_anthropic_reports_reads_and_writes_apart_and_keeps_the_total_whole() -> None:
    from aira_gateway.upstreams.vertex.anthropic_mapping import usage_of

    usage = usage_of(
        {"input_tokens": 100, "cache_read_input_tokens": 800, "cache_creation_input_tokens": 100}
    )

    assert usage.prompt_tokens == 1000, "the total input must stay whole"
    assert usage.cached_input_tokens == 800
    assert usage.cache_write_tokens == 100
    assert usage.uncached_input_tokens == 100


def test_the_openai_dialect_reads_the_nested_cached_tokens() -> None:
    """Azure reports it under `prompt_tokens_details`, and there is **no write figure** on this
    dialect: a first request pays the ordinary rate and populates the cache as a side effect."""
    from aira_gateway.upstreams.openai.mapping import _usage_of

    usage = _usage_of(
        {
            "prompt_tokens": 2048,
            "completion_tokens": 10,
            "prompt_tokens_details": {"cached_tokens": 1024},
        }
    )

    assert usage.cached_input_tokens == 1024
    assert usage.cache_write_tokens == 0
    assert usage.uncached_input_tokens == 1024


def test_a_runtime_that_reports_no_details_is_read_as_no_cache() -> None:
    """Verified against the running Ollama: it answers with `prompt_tokens`/`completion_tokens` and
    nothing else. Zero is the honest reading — and on a self-hosted model there is no bill for it
    to be wrong about."""
    from aira_gateway.upstreams.openai.mapping import _usage_of

    usage = _usage_of({"prompt_tokens": 30, "completion_tokens": 1})

    assert usage.cached_input_tokens == 0
    assert usage.uncached_input_tokens == 30


def test_gemini_reads_its_implicit_cache_count() -> None:
    """Implicit caching is on by default from 2.5 and needs nothing sent; this count is the only
    evidence it happened."""
    from aira_gateway.upstreams.gemini_mapping import _usage_of

    usage = _usage_of(
        {
            "usageMetadata": {
                "promptTokenCount": 3000,
                "candidatesTokenCount": 20,
                "cachedContentTokenCount": 2500,
            }
        }
    )

    assert usage.prompt_tokens == 3000
    assert usage.cached_input_tokens == 2500
    assert usage.uncached_input_tokens == 500


# ---- the marker (`FRD-133` stage B) ---------------------------------------------------------


def _request(**over):
    from aira_gateway.core.canonical import (
        CanonicalMessage,
        CanonicalRequest,
        Role,
        ToolDeclaration,
    )

    base = {
        "model": "claude-sonnet-4-5@20250929",
        "messages": [
            CanonicalMessage(role=Role.SYSTEM, text="You are a coding assistant."),
            CanonicalMessage(role=Role.USER, text="hi"),
        ],
        "tools": (
            ToolDeclaration(name="read", description="read a file"),
            ToolDeclaration(name="write", description="write a file"),
        ),
    }
    base.update(over)
    return CanonicalRequest(**base)


def test_the_marker_lands_on_the_system_block_and_the_last_tool() -> None:
    """**Two breakpoints, not one per tool.** Anthropic allows four per request and a breakpoint
    means "everything up to here", so marking every tool would exhaust the budget on the fourth
    function and cache almost nothing. Two is what the measurement says covers 99.1 % of a turn:
    the tool declarations (69 %) and the system instruction (31 %)."""
    from aira_gateway.upstreams.vertex.anthropic_mapping import canonical_to_anthropic

    body = canonical_to_anthropic(_request(cache_prefix=True), max_tokens=256)

    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in body["tools"][0], "one breakpoint per tool exhausts the budget"


def test_the_conversation_so_far_is_marked_on_its_last_block() -> None:
    """**The third breakpoint: the conversation.** §5.1 measured short conversations — 47 bytes to
    1.6 KB — beside 31 KB of tools and system prompt. A coding assistant on a large repository
    turns that round: the files it read and its tools' results are hundreds of thousands of tokens,
    resent every turn and appended to at the end. Unmarked, each turn paid all of it at full price
    and the provider read it anew before the first word; marked on its last block, the next turn
    reads everything up to here from the cache (`ADR-0046`'s context)."""
    from aira_gateway.core.canonical import CanonicalMessage, Role, ToolCallPart, ToolResultPart
    from aira_gateway.upstreams.vertex.anthropic_mapping import canonical_to_anthropic

    turns = [
        CanonicalMessage(role=Role.SYSTEM, text="You are a coding assistant."),
        CanonicalMessage(role=Role.USER, text="read hello.py"),
        CanonicalMessage(
            role=Role.MODEL,
            parts=[ToolCallPart(id="c1", name="read", arguments={"path": "hello.py"})],
        ),
        CanonicalMessage(
            role=Role.USER,
            parts=[ToolResultPart(call_id="c1", name="read", content='print("hello")')],
        ),
    ]
    body = canonical_to_anthropic(_request(messages=turns, cache_prefix=True), max_tokens=256)

    last = body["messages"][-1]["content"][-1]
    assert last["type"] == "tool_result"
    assert last["cache_control"] == {"type": "ephemeral"}
    earlier = [block for message in body["messages"][:-1] for block in message["content"]]
    assert all("cache_control" not in block for block in earlier)
    markers = (
        sum("cache_control" in block for block in body["system"])
        + sum("cache_control" in tool for tool in body["tools"])
        + sum("cache_control" in block for m in body["messages"] for block in m["content"])
    )
    assert markers == 3, "Anthropic refuses a fifth breakpoint; three leave one to spare"


def test_the_conversation_is_kept_as_long_as_the_rest() -> None:
    """One lifetime for every marker: a longer one after a shorter one is refused by the provider,
    and the use case chose one."""
    from aira_gateway.upstreams.vertex.anthropic_mapping import canonical_to_anthropic

    body = canonical_to_anthropic(_request(cache_prefix=True, cache_ttl="1h"), max_tokens=256)

    assert body["messages"][-1]["content"][-1]["cache_control"] == {
        "type": "ephemeral",
        "ttl": "1h",
    }


def test_without_the_marker_the_body_is_what_it_always_was() -> None:
    """The compatibility property. A use case that has not opted in must produce a byte-identical
    request — including `system` staying a plain string, because `cache_control` only exists on the
    block form and changing shape for everybody to serve the few who opted in is a wire change
    nobody asked for."""
    from aira_gateway.upstreams.vertex.anthropic_mapping import canonical_to_anthropic

    body = canonical_to_anthropic(_request(), max_tokens=256)

    assert body["system"] == "You are a coding assistant."
    assert all("cache_control" not in tool for tool in body["tools"])
    assert all("cache_control" not in b for m in body["messages"] for b in m["content"])


def test_the_five_minute_lifetime_is_chosen_rather_than_the_hour() -> None:
    """A one-hour write costs 2x base input against 1.25x for five minutes. The measured gap
    between assistant turns is 41 seconds, with 13 of 14 inside five minutes — the longer window
    would pay double to buy almost nothing."""
    from aira_gateway.upstreams.vertex.anthropic_mapping import canonical_to_anthropic

    body = canonical_to_anthropic(_request(cache_prefix=True), max_tokens=256)

    assert "ttl" not in body["system"][0]["cache_control"]


async def test_a_model_that_cannot_cache_is_served_uncached_and_not_skipped() -> None:
    """**The one capability gap that is not a skip** (`FRD-133` FR-2).

    Every other flag in the vocabulary guards the *answer*: a model that cannot read the attachment
    would answer confidently about a document it never saw, so the chain moves on and, if nothing
    qualifies, refuses. A model that cannot cache answers exactly the right thing — it just costs
    more. Skipping it would refuse a request over a **price**, which is the opposite of what a
    fallback chain is for.

    Asserted as behaviour and not as a comment, because the inconsistency is the kind somebody
    tidies up: the request must still be **served**, and the marker must be off — through the
    route, for a use case that asked for caching, beside a model that can cache and gets it.
    """
    from fastapi.testclient import TestClient

    from aira_gateway.app import create_app
    from aira_gateway.config import GatewaySettings
    from aira_gateway.db.models import UseCaseRead
    from aira_gateway.upstreams.base import ProviderRegistry
    from aira_gateway.upstreams.mock import MockProvider

    app = create_app(GatewaySettings(auth_required=False, enforce_budgets=False, log_queue_size=0))
    with TestClient(app) as client:
        app.state.providers = ProviderRegistry([MockProvider("no-cache-1", "cache-1")])
        async with app.state.db_sessionmaker() as session:
            session.add(UseCaseRead(slug="caching-uc", name="c", prompt_caching_enabled=True))
            session.add(ModelRead(model="no-cache-1", capabilities=["generate"]))
            session.add(ModelRead(model="cache-1", capabilities=["generate", "prompt_caching"]))
            await session.commit()
        body = {
            "systemInstruction": {"parts": [{"text": "You are a coding assistant."}]},
            "contents": [{"role": "user", "parts": [{"text": "hello"}]}],
        }
        headers = {"x-aira-use-case": "caching-uc"}
        uncached = client.post(
            "/v1beta/models/no-cache-1:generateContent", json=body, headers=headers
        )
        cached = client.post("/v1beta/models/cache-1:generateContent", json=body, headers=headers)

    assert uncached.status_code == 200, uncached.text
    assert "[cache:" not in uncached.text, "a model that cannot cache was sent the marker"
    assert cached.status_code == 200, cached.text
    assert "[cache:5m]" in cached.text, "the use case's choice never reached the request"


def test_the_long_lifetime_is_sent_only_when_it_was_chosen() -> None:
    """An hour costs **2x** base input to write against 1.25x for five minutes, so it has to be
    asked for. An absent `ttl` is Anthropic's own default, which means the cheap case sends exactly
    what it sent before this parameter existed — the expensive option appears on the wire, never
    the cheap one."""
    from aira_gateway.upstreams.vertex.anthropic_mapping import canonical_to_anthropic

    short = canonical_to_anthropic(_request(cache_prefix=True), max_tokens=256)
    long = canonical_to_anthropic(_request(cache_prefix=True, cache_ttl="1h"), max_tokens=256)

    assert "ttl" not in short["system"][0]["cache_control"]
    assert long["system"][0]["cache_control"]["ttl"] == "1h"
    assert long["tools"][-1]["cache_control"]["ttl"] == "1h"


def test_an_unrecognised_lifetime_reads_as_the_cheap_one() -> None:
    """A typo must not be able to double a bill. Asserted on the wire rather than on the setting,
    because the setting is only wrong if it reaches the provider."""
    from aira_gateway.upstreams.vertex.anthropic_mapping import canonical_to_anthropic

    body = canonical_to_anthropic(_request(cache_prefix=True, cache_ttl="1hour"), max_tokens=256)

    assert "ttl" not in body["system"][0]["cache_control"]


# == from the console to the provider ============================================================
#
# The tests above prove the mapping and the pricing. What they cannot see is the **journey**: a
# checkbox in the console becomes an event, a read-model row, and a lookup that happens *after*
# routing — four hops, each one somewhere the setting can be dropped without anything failing.
# That is `FRD-124`'s lesson, which cost us a defect once already: a requirement tested only where
# it is implemented leaves the wiring to it undefended, and a request served uncached looks exactly
# like a request that was never asked to cache.


def _wired_app():  # noqa: ANN202
    from aira_gateway.app import create_app
    from aira_gateway.config import GatewaySettings

    return create_app(GatewaySettings(auth_required=False, enforce_budgets=False, log_queue_size=0))


async def _configure(app, *, enabled: bool, ttl: str = "5m", can_cache: bool = True) -> None:  # noqa: ANN001
    from aira_gateway.db.models import UseCaseRead

    capabilities = ["generate"] + (["prompt_caching"] if can_cache else [])
    async with app.state.db_sessionmaker() as session:
        session.add(
            UseCaseRead(slug="uc", name="uc", prompt_caching_enabled=enabled, prompt_cache_ttl=ttl)
        )
        session.add(ModelRead(model="mock-1", capabilities=capabilities))
        await session.commit()


def _ask(client):  # noqa: ANN001, ANN202
    return client.post(
        "/v1beta/models/mock-1:generateContent",
        json={"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
        headers={"x-aira-use-case": "uc"},
    )


async def test_the_lifetime_a_use_case_chose_reaches_the_model() -> None:
    """The parameter the console exists to tune. Asserted at the far end of the journey, because
    every hop in between is one where "1h" can quietly become the default."""
    from fastapi.testclient import TestClient

    app = _wired_app()
    with TestClient(app) as client:
        await _configure(app, enabled=True, ttl="1h")
        response = _ask(client)

    assert response.status_code == 200, response.text
    assert "[cache:1h]" in response.text


async def test_a_use_case_that_did_not_opt_in_is_never_marked() -> None:
    """Off by default is a *privacy* property on Vertex, where the cache scope is the whole
    organisation — so this is not merely a saving nobody claimed."""
    from fastapi.testclient import TestClient

    app = _wired_app()
    with TestClient(app) as client:
        await _configure(app, enabled=False, ttl="1h")
        response = _ask(client)

    assert response.status_code == 200, response.text
    assert "[cache:" not in response.text


async def test_a_model_that_cannot_cache_is_served_anyway_without_the_marker() -> None:
    """`FRD-133` FR-2 as behaviour rather than as a source-code assertion: the request is served,
    with a 200 and an answer, and simply carries no marker."""
    from fastapi.testclient import TestClient

    app = _wired_app()
    with TestClient(app) as client:
        await _configure(app, enabled=True, ttl="1h", can_cache=False)
        response = _ask(client)

    assert response.status_code == 200, response.text
    assert "[cache:" not in response.text
