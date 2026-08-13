"""Limits and budgets against a running gateway: how fast, how much, and who is stopped.

Rate limits and budgets answer different questions and are made of different parts — a token bucket
in Redis that must hold across gateway instances, and a reservation that makes requests in flight
visible to each other's check. What only shows up against the real stack is the part in the middle:
that a refusal reaches the caller as a status it can act on, that it is *recorded*, and that it
costs the caller nothing beyond being told.

Two properties this file is built around:

- **A refused request must not have spent anything.** Not the allowance it was refused by (a
  request that fails one bucket must not debit the other), and not a model call on the way to being
  told (`guard_before_work` runs before the pipeline for exactly this reason: measured at seven
  refusals costing more than the one answer).
- **A refusal is recorded.** `FRD-122`: the log records what was **asked**, not what was served, so
  "somebody keeps hitting this limit" is a question with an answer.

Where the gateway caches a policy on purpose — the rate limiter for a few seconds, the suspension
gate for five — the test waits it out rather than pretending the cache is not there. A test that
did not would be testing the clock.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from .conftest import GATEWAY_URL
from .governed import CHAT_MODEL, EMBED_MODEL, GEMINI, MOCK_MODEL, Governed

pytestmark = pytest.mark.integration

SHORT = {"maxOutputTokens": 8}

#: One per minute, so the bucket refills a token every sixty seconds rather than every one.
#:
#: **The refill is what makes a limit test flaky.** Written as `limit_rpm=60`, a bucket regains a
#: token per second — and a request that calls a *real* model takes longer than that, so the second
#: request finds the allowance restored and is served. It passed in isolation, where the model was
#: warm, and failed inside the full suite, where it was not: the test was measuring how fast the
#: model answered. Nothing below waits a minute, so at this rate no refill can rescue a refusal.
SLOW_REFILL = 1


def _body(text: str = "Say OK.", **config: object) -> dict:
    return {
        "contents": [{"role": "user", "parts": [{"text": text}]}],
        "generationConfig": {**SHORT, **config},
    }


def _message(response: httpx.Response) -> str:
    return str(response.json()["error"]["message"])


async def _statuses(governed: Governed, count: int, *, model: str = MOCK_MODEL) -> list[int]:
    """``count`` requests, in order, as the statuses they came back with.

    The double on purpose: these cases are about the *counter*, and a two-second model answer
    per request would make a ten-request case a twenty-second one without changing what is tested.
    """
    out: list[int] = []
    for _ in range(count):
        out.append((await governed.generate(_body(), model=model)).status_code)
    return out


# ═══ 1. the rate limit ═════════════════════════════════════════════════════════════════════════


async def test_a_use_case_with_no_limit_is_unlimited(governed: Governed) -> None:
    """This feature must never start rejecting existing traffic on upgrade."""
    assert await _statuses(governed, 8) == [200] * 8


async def test_a_use_case_limit_binds_and_says_which_allowance_ran_out(
    governed: Governed,
) -> None:
    await governed.rate_limit(limit_rpm=SLOW_REFILL, burst=2)
    await governed.settle()

    statuses = await _statuses(governed, 4)

    assert statuses[:2] == [200, 200], statuses
    assert 429 in statuses, statuses
    refused = await governed.generate(_body(), model=MOCK_MODEL)
    assert "use case" in _message(refused).lower()


async def test_a_refusal_carries_a_retry_after_a_client_would_obey(governed: Governed) -> None:
    """A 429 without one tells a client to guess, and every client guesses differently."""
    await governed.rate_limit(limit_rpm=SLOW_REFILL, burst=1)
    await governed.settle()

    await governed.generate(_body(), model=MOCK_MODEL)
    refused = await governed.generate(_body(), model=MOCK_MODEL)

    assert refused.status_code == 429, refused.text[:200]
    assert refused.headers.get("Retry-After"), dict(refused.headers)
    assert int(refused.headers["Retry-After"]) >= 0


async def test_a_rate_refusal_is_recorded_and_produced_no_output(governed: Governed) -> None:
    """`FRD-122` again: a thousand rate-limited requests *is* the anomaly, and a control that
    refuses without recording makes it invisible."""
    await governed.rate_limit(limit_rpm=SLOW_REFILL, burst=1)
    await governed.settle()

    await _statuses(governed, 3)
    rows = await governed.wait_for_rows(2)

    refusals = [row for row in rows if row["outcome"] == "rate_limited"]
    assert refusals, [row["outcome"] for row in rows]
    assert refusals[0]["status"] == 429
    assert not refusals[0]["completion_tokens"], (
        "a refused request was recorded as producing output"
    )


async def test_a_disabled_limit_does_not_bind(governed: Governed) -> None:
    await governed.rate_limit(limit_rpm=SLOW_REFILL, burst=1)
    async with governed.engine.begin() as connection:
        from sqlalchemy import text

        await connection.execute(
            text("UPDATE rate_limits SET enabled = false WHERE use_case = :slug"),
            {"slug": governed.slug},
        )
    await governed.settle()

    assert await _statuses(governed, 5) == [200] * 5


async def test_an_unset_burst_means_the_per_minute_figure_not_zero(governed: Governed) -> None:
    """ "60 per minute" with no burst must let requests through, not none. A bucket sized zero
    refuses everything, which reads as an outage rather than as a limit."""
    await governed.rate_limit(limit_rpm=60, burst=0)
    await governed.settle()

    assert (await governed.generate(_body(), model=MOCK_MODEL)).status_code == 200


async def test_a_member_limit_binds_the_member_it_names(governed: Governed) -> None:
    """The key's subject is `dev-round`; a rule about somebody else must not bind this caller."""
    await governed.rate_limit(
        limit_rpm=SLOW_REFILL, burst=1, scope="member", subject="somebody-else"
    )
    await governed.settle()

    assert await _statuses(governed, 4) == [200] * 4


async def test_a_member_limit_naming_this_caller_binds_it(governed: Governed) -> None:
    await governed.rate_limit(limit_rpm=SLOW_REFILL, burst=1, scope="member", subject="dev-round")
    await governed.settle()

    statuses = await _statuses(governed, 3)

    assert statuses[0] == 200, statuses
    assert 429 in statuses, statuses
    refused = await governed.generate(_body(), model=MOCK_MODEL)
    assert "member" in _message(refused).lower()


async def test_the_per_person_scope_needs_no_row_naming_anybody(governed: Governed) -> None:
    """`each_member`: one configured row, one counter per caller — a fair share per head without
    listing the heads, which is what an administrator wants far more often than either of the
    other two."""
    await governed.rate_limit(limit_rpm=SLOW_REFILL, burst=1, scope="each_member")
    await governed.settle()

    statuses = await _statuses(governed, 3)

    assert statuses[0] == 200, statuses
    assert 429 in statuses, statuses


async def test_the_stricter_of_two_scopes_wins(governed: Governed) -> None:
    """Both are checked where both exist, so one member cannot consume a whole use case's
    allowance — and the *decision is all-or-nothing*: a refused member must not have drained the
    use case's bucket on the way out (`FRD-405` FR-4)."""
    await governed.rate_limit(limit_rpm=600, burst=50)
    await governed.rate_limit(limit_rpm=SLOW_REFILL, burst=1, scope="member", subject="dev-round")
    await governed.settle()

    statuses = await _statuses(governed, 3)

    assert statuses[0] == 200, statuses
    assert 429 in statuses, statuses


async def test_an_embedding_batch_weighs_what_it_is(governed: Governed) -> None:
    """A batch of *n* is *n* requests (`FRD-113` FR-6). Admitting it as one would leave a limit of
    ten allowing five thousand texts a minute — intact on paper and gone in practice.

    At `SLOW_REFILL` for the reason that constant exists, which this one test did not use. It said
    `limit_rpm=600` — ten tokens a second — so the five it needs are back **half a second** after
    the first batch took them, and the second request is refused only if the first answers faster
    than that. Measured against the running stack: 82 ms and 234 ms for this call unloaded, so the
    margin was a quarter of a second wide, and it duly went green in isolation and red inside the
    full suite. A test whose verdict turns on how quickly a real model answered is measuring the
    machine.
    """
    await governed.rate_limit(limit_rpm=SLOW_REFILL, burst=5)
    await governed.settle()

    body = {
        "requests": [
            {"model": f"models/{EMBED_MODEL}", "content": {"parts": [{"text": f"t{i}"}]}}
            for i in range(5)
        ]
    }
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=60.0) as client:
        first = await client.post(
            f"{GEMINI}/models/{EMBED_MODEL}:batchEmbedContents",
            json=body,
            headers=governed.headers(),
        )
        second = await client.post(
            f"{GEMINI}/models/{EMBED_MODEL}:batchEmbedContents",
            json=body,
            headers=governed.headers(),
        )

    assert first.status_code == 200, first.text[:200]
    assert second.status_code == 429, "a batch of five weighed one against a bucket of five"


async def test_a_batch_larger_than_the_bucket_says_so_instead_of_stalling(
    governed: Governed,
) -> None:
    """A batch bigger than the bucket can never be admitted however long the caller waits, so the
    refusal names the bound rather than offering a `Retry-After` that would still be wrong an hour
    later."""
    await governed.rate_limit(limit_rpm=SLOW_REFILL, burst=2)
    await governed.settle()

    body = {
        "requests": [
            {"model": f"models/{EMBED_MODEL}", "content": {"parts": [{"text": f"t{i}"}]}}
            for i in range(10)
        ]
    }
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=60.0) as client:
        response = await client.post(
            f"{GEMINI}/models/{EMBED_MODEL}:batchEmbedContents",
            json=body,
            headers=governed.headers(),
        )

    assert response.status_code == 429, response.text[:300]
    assert "cannot fit" in _message(response) or "smaller batches" in _message(response)


@pytest.mark.parametrize("surface", ["gemini", "kira"])
async def test_the_limit_holds_on_both_surfaces(governed: Governed, surface: str) -> None:
    """A limit one surface enforces is a limit a caller escapes by changing a URL — the reason the
    early gate sits on the path every verb takes rather than inside one of them."""
    await governed.rate_limit(limit_rpm=SLOW_REFILL, burst=1)
    await governed.settle()

    if surface == "gemini":
        first = await governed.generate(_body(), model=MOCK_MODEL)
        second = await governed.generate(_body(), model=MOCK_MODEL)
    else:
        payload = {"request": {"parts": [{"text": "hi"}]}, "model_id": 9001, "maxTokens": 8}
        first = await governed.kira("/chat", payload)
        second = await governed.kira("/chat", payload)

    assert first.status_code == 200, first.text[:200]
    assert second.status_code == 429, second.text[:200]


async def test_a_limit_on_one_use_case_leaves_another_alone(
    governed: Governed, second_governed: Governed
) -> None:
    await governed.rate_limit(limit_rpm=SLOW_REFILL, burst=1)
    await governed.settle()

    await _statuses(governed, 2)

    assert (await second_governed.generate(_body(), model=MOCK_MODEL)).status_code == 200


async def test_a_rate_limited_caller_pays_for_no_classifier(governed: Governed) -> None:
    """`guard_before_work` runs **before** the pipeline. It did not once, and the measurement was
    one served request, seven refused and 72 400 tokens spent — the refusals cost more than the
    answer. A refused caller must not pay to be told no."""
    await governed.pipeline(
        {
            "type": "injection_filter",
            "config": {"mode": "llm", "action": "flag", "model": CHAT_MODEL},
        }
    )
    await governed.rate_limit(limit_rpm=SLOW_REFILL, burst=1)
    await governed.settle()

    assert (await governed.generate(_body(), model=MOCK_MODEL)).status_code == 200
    await governed.clear_rows()
    assert (await governed.generate(_body(), model=MOCK_MODEL)).status_code == 429

    rows = await governed.wait_for_rows(1)
    assert not [row for row in rows if str(row["operation"]).startswith("pipeline:")], (
        "a refused request paid for a classifier on the way to being refused"
    )


# ═══ 2. budgets ════════════════════════════════════════════════════════════════════════════════


async def test_a_use_case_with_no_budget_is_unlimited(governed: Governed) -> None:
    assert await _statuses(governed, 5) == [200] * 5


async def test_a_request_budget_refuses_once_it_is_spent(governed: Governed) -> None:
    await governed.budget(requests=2)

    statuses = await _statuses(governed, 4)

    assert statuses[:2] == [200, 200], statuses
    assert 429 in statuses, statuses


async def test_a_token_budget_refuses_once_it_is_spent(governed: Governed) -> None:
    await governed.budget(tokens=1)

    statuses = await _statuses(governed, 3, model=CHAT_MODEL)

    assert 429 in statuses, statuses


async def test_a_cost_budget_refuses_once_it_is_spent(governed: Governed) -> None:
    """Money, not tokens. A token differs in price by more than tenfold between models and output
    is billed several times higher than input, so a token cap was never a cost control
    (`FRD-403`)."""
    await governed.budget(cost_nanos=1)

    statuses = await _statuses(governed, 3, model=CHAT_MODEL)

    assert 429 in statuses, statuses


async def test_a_budget_refusal_is_recorded_with_its_own_outcome(governed: Governed) -> None:
    """Not folded into `rate_limited`: "this use case is out of money" and "this caller is going
    too fast" want different answers from whoever reads the report."""
    await governed.budget(requests=1)

    await _statuses(governed, 3)
    rows = await governed.wait_for_rows(2)

    assert any(row["outcome"] == "budget_exceeded" for row in rows), [r["outcome"] for r in rows]


async def test_a_zero_budget_refuses_the_very_first_request(governed: Governed) -> None:
    await governed.budget(requests=0)

    assert (await governed.generate(_body(), model=MOCK_MODEL)).status_code == 429


async def test_a_daily_and_a_monthly_budget_both_bind(governed: Governed) -> None:
    """Two periods, two counters. The narrower one is what stops the traffic, and it must not be
    made irrelevant by the wider one being generous."""
    await governed.budget(requests=100, period="month")
    await governed.budget(requests=1, period="day")

    statuses = await _statuses(governed, 3)

    assert statuses[0] == 200, statuses
    assert 429 in statuses, statuses


async def test_a_member_budget_binds_only_that_member(governed: Governed) -> None:
    await governed.budget(requests=1, scope="member", subject="somebody-else")

    assert await _statuses(governed, 3) == [200] * 3


async def test_a_per_person_budget_binds_whoever_turns_up(governed: Governed) -> None:
    """`each_member` again, on the money side: one configured row, one counter per caller."""
    await governed.budget(requests=1, scope="each_member")

    statuses = await _statuses(governed, 3)

    assert statuses[0] == 200, statuses
    assert 429 in statuses, statuses


@pytest.mark.parametrize("surface", ["gemini", "kira"])
async def test_the_budget_holds_on_both_surfaces(governed: Governed, surface: str) -> None:
    await governed.budget(requests=1)

    if surface == "gemini":
        first = await governed.generate(_body(), model=MOCK_MODEL)
        second = await governed.generate(_body(), model=MOCK_MODEL)
    else:
        payload = {"request": {"parts": [{"text": "hi"}]}, "model_id": 9001, "maxTokens": 8}
        first = await governed.kira("/chat", payload)
        second = await governed.kira("/chat", payload)

    assert first.status_code == 200, first.text[:200]
    assert second.status_code == 429, second.text[:200]


async def test_an_embedding_batch_weighs_its_size_against_a_request_budget(
    governed: Governed,
) -> None:
    """A batch of *n* books *n* requests, not one.

    **Asserted through the next request, because a budget and a rate bucket differ here on
    purpose.** The bucket has a capacity, so a batch bigger than it can never be admitted however
    long the caller waits and is refused by name. A budget is a running total for a period, and the
    reservation script checks `requests >= limit` *before* adding — "already at it" is what refuses,
    so one request may carry the counter past the line. Refusing instead would mean a batch of five
    hundred could never run under a 499-request monthly budget, even on the first of the month.

    So the weighting cannot be read from the batch's own status. It is read from what the batch
    left behind: against a limit of three, a batch of four is served and the **next** request is
    refused. Had the batch weighed one, the counter would stand at one and that request would have
    been served — which is the whole difference this test exists to see.
    """
    await governed.budget(requests=3)

    body = {
        "requests": [
            {"model": f"models/{EMBED_MODEL}", "content": {"parts": [{"text": f"t{i}"}]}}
            for i in range(4)
        ]
    }
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=60.0) as client:
        batch = await client.post(
            f"{GEMINI}/models/{EMBED_MODEL}:batchEmbedContents",
            json=body,
            headers=governed.headers(),
        )

    assert batch.status_code == 200, batch.text[:300]
    after = await governed.generate(_body(), model=MOCK_MODEL)
    assert after.status_code == 429, (
        "the batch of four weighed one against a budget of three, so a request limit means "
        f"something different for embeddings: {after.status_code}"
    )


async def test_a_budget_on_one_use_case_leaves_another_alone(
    governed: Governed, second_governed: Governed
) -> None:
    await governed.budget(requests=1)
    await _statuses(governed, 2)

    assert (await second_governed.generate(_body(), model=MOCK_MODEL)).status_code == 200


async def test_a_refused_request_does_not_consume_the_allowance_it_was_refused_by(
    governed: Governed,
) -> None:
    """A reservation is **released** when nothing chargeable was produced. Booking a request
    against somebody who received nothing would spend a request limit on an upstream outage — and
    the leak was real: they used to leak on every failure that was not an `UpstreamError`."""
    await governed.budget(requests=3)

    # Two refusals that never reach a model, then three requests that should all fit.
    for _ in range(2):
        assert (await governed.generate({"contents": []})).status_code in (400, 422)
    statuses = await _statuses(governed, 3)

    assert statuses == [200, 200, 200], f"a refusal consumed the allowance: {statuses}"


async def test_concurrent_requests_see_each_others_reservations(governed: Governed) -> None:
    """The race `FRD-405` closed: without a reservation, *n* concurrent requests all pass a check
    with room for one. Twenty at once against an allowance of one — at most a handful may be served,
    and the old path served all twenty."""
    await governed.budget(requests=1)

    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=120.0) as client:

        async def one() -> int:
            response = await client.post(
                f"{GEMINI}/models/{MOCK_MODEL}:generateContent",
                json=_body(),
                headers=governed.headers(),
            )
            return response.status_code

        statuses = await asyncio.gather(*(one() for _ in range(20)))

    served = [status for status in statuses if status == 200]
    assert len(served) <= 5, f"{len(served)} of 20 got through an allowance of one: {statuses}"
    assert 429 in statuses, statuses


# ═══ 3. the kill switch ════════════════════════════════════════════════════════════════════════


async def test_a_suspended_use_case_is_stopped_with_429_and_its_own_outcome(
    governed: Governed,
) -> None:
    """**429, not 403.** The credential is valid; "come back later" is what 429 means, and a 403
    would send the caller to whoever issues keys. `suspended` is its own outcome rather than being
    folded into `rate_limited`, because "we stopped this caller on purpose" and "this caller is
    going too fast" are different facts."""
    from .conftest import Fixture  # noqa: F401 - imported for the suspension helper's shape

    await governed._exec(  # noqa: SLF001 - the fixture writes the read-model, as every suite does
        "INSERT INTO access_suspensions (id, use_case, target, target_value, action, throttle_rpm,"
        " expires_at, author, reason)"
        " VALUES (gen_random_uuid(), :slug, 'use_case', :slug, 'block', NULL,"
        " now() + interval '1 hour', 'user:dev-round', 'developer round')",
        slug=governed.slug,
    )
    await governed.settle()

    response = await governed.generate(_body(), model=MOCK_MODEL)

    assert response.status_code == 429, response.text[:300]
    row = await governed.last_row()
    assert row["outcome"] == "suspended", row["outcome"]


async def test_a_stopped_caller_pays_for_no_classifier_either(governed: Governed) -> None:
    """Read at the one pre-dispatch gate, so a stopped caller does not pay for a classifier on the
    way to being told (`FRD-503` FR-3) — the same argument that moved rate limiting there."""
    await governed.pipeline(
        {
            "type": "injection_filter",
            "config": {"mode": "llm", "action": "flag", "model": CHAT_MODEL},
        }
    )
    await governed._exec(  # noqa: SLF001
        "INSERT INTO access_suspensions (id, use_case, target, target_value, action, throttle_rpm,"
        " expires_at, author, reason)"
        " VALUES (gen_random_uuid(), :slug, 'use_case', :slug, 'block', NULL,"
        " now() + interval '1 hour', 'user:dev-round', 'developer round')",
        slug=governed.slug,
    )
    await governed.settle()

    assert (await governed.generate(_body(), model=MOCK_MODEL)).status_code == 429
    rows = await governed.wait_for_rows(1)

    assert not [row for row in rows if str(row["operation"]).startswith("pipeline:")], rows


async def test_a_suspension_on_one_use_case_leaves_another_alone(
    governed: Governed, second_governed: Governed
) -> None:
    await governed._exec(  # noqa: SLF001
        "INSERT INTO access_suspensions (id, use_case, target, target_value, action, throttle_rpm,"
        " expires_at, author, reason)"
        " VALUES (gen_random_uuid(), :slug, 'use_case', :slug, 'block', NULL,"
        " now() + interval '1 hour', 'user:dev-round', 'developer round')",
        slug=governed.slug,
    )
    await governed.settle()

    assert (await governed.generate(_body(), model=MOCK_MODEL)).status_code == 429
    assert (await second_governed.generate(_body(), model=MOCK_MODEL)).status_code == 200


async def test_an_expired_suspension_does_not_stop_anybody(governed: Governed) -> None:
    """A decision with an expiry is what makes an automatic block something other than an outage
    with a good reason (`ADR-0014`). One that has passed must stop applying by itself."""
    await governed._exec(  # noqa: SLF001
        "INSERT INTO access_suspensions (id, use_case, target, target_value, action, throttle_rpm,"
        " expires_at, author, reason)"
        " VALUES (gen_random_uuid(), :slug, 'use_case', :slug, 'block', NULL,"
        " now() - interval '1 minute', 'user:dev-round', 'expired')",
        slug=governed.slug,
    )
    await governed.settle()

    assert (await governed.generate(_body(), model=MOCK_MODEL)).status_code == 200
