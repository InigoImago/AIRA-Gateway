"""What a request asks for is what a model is told (FRD-124), against a real model.

The hermetic suite proves that a control reaches the wire body. That is a different claim from the
one that matters, and this file exists because the difference cost a defect: `disabled` thinking
*did* reach the wire body — as an absent parameter, which the model read as its own default and
answered by spending the whole output allowance on hidden reasoning, under a 200.

So the assertions here are behavioural. A seed makes an answer reproducible or it does not. A stop
sequence truncates the output or it does not. Neither can be established by inspecting a dict.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from tests.integration.conftest import GATEWAY_URL

pytestmark = pytest.mark.integration

MODEL = "qwen3:0.6b"


async def _ask(fixture, prompt: str, **config: object) -> tuple[int, str]:
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": 60,
            # Off, explicitly. Left to the model, a reasoning model spends the allowance thinking
            # and every assertion below becomes an assertion about an empty string.
            "thinkingConfig": {"mode": "disabled"},
            **config,
        },
    }
    async with httpx.AsyncClient(timeout=300.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1beta/models/{MODEL}:generateContent",
            headers=fixture.headers(),
            json=body,
        )
    if response.status_code != 200:
        return response.status_code, response.json()["error"]["message"]
    return 200, response.json()["candidates"][0]["content"]["parts"][0]["text"]


# == the controls take effect ====================================================================


async def test_a_seed_makes_the_same_request_reproducible(fixture) -> None:
    """The property a seed exists for, asserted as the property and not as a field in a body.

    Before `FRD-124` this request was accepted, the seed discarded, and three identical calls
    returned three different answers with a 200 on each — the exact failure a caller sets a seed to
    rule out, presented as the model being creative.
    """
    prompt = "Invent a three-word band name."
    answers = [(await _ask(fixture, prompt, temperature=1.0, seed=777))[1] for _ in range(3)]

    assert len(set(answers)) == 1, f"the same seed produced different answers: {answers}"


async def test_a_different_seed_gives_a_different_answer(fixture) -> None:
    """The other half. Without it, a provider that ignored sampling entirely — or a model with
    nothing to say — would pass the test above."""
    prompt = "Invent a three-word band name."
    _, one = await _ask(fixture, prompt, temperature=1.0, seed=777)
    _, other = await _ask(fixture, prompt, temperature=1.0, seed=31337)

    assert one != other


async def test_a_stop_sequence_actually_truncates_the_output(fixture) -> None:
    """Asserted against the unconstrained answer rather than against a fixed string: a small model
    may not produce the expected text at all, and a test that assumed it would would fail for a
    reason that has nothing to do with the gateway."""
    prompt = "Output exactly: A B C D E"
    _, unconstrained = await _ask(fixture, prompt, temperature=0.0)
    if "C" not in unconstrained:
        pytest.skip(f"the model did not produce the token to stop at: {unconstrained!r}")

    _, truncated = await _ask(fixture, prompt, temperature=0.0, stopSequences=["C"])

    assert "C" not in truncated
    assert len(truncated) < len(unconstrained)


async def test_thinking_switched_off_is_switched_off(fixture) -> None:
    """The defect that started `FRD-124`.

    Sent no `reasoning_effort`, this model thinks anyway and spends the entire allowance doing it:
    600 output tokens, an empty answer, `MAX_TOKENS`, and a 200. The caller who explicitly turned
    thinking off has no way to see any of that, because the reasoning is stripped from the response
    before they receive it — they see a model that failed to answer.

    A question the model can only get right by reasoning, with an allowance too small to reason
    inside: with thinking off it answers, with thinking high it does not.
    """
    prompt = "Is 391 prime? Answer with one word."
    _, direct = await _ask(fixture, prompt, thinkingConfig={"mode": "disabled"})
    _, thinking = await _ask(fixture, prompt, thinkingConfig={"mode": "high"})

    assert direct.strip(), "thinking was switched off and the model still returned nothing"
    assert len(direct) > len(thinking)


# == and what cannot be honoured is refused ======================================================


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tools", [{"functionDeclarations": [{"name": "f", "parameters": {}}]}]),
        ("safetySettings", [{"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"}]),
        ("cachedContent", "cachedContents/abc"),
    ],
)
async def test_an_out_of_scope_field_is_refused_by_the_running_gateway(
    fixture, field: str, value: object
) -> None:
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1beta/models/{MODEL}:generateContent",
            headers=fixture.headers(),
            json={"contents": [{"role": "user", "parts": [{"text": "hi"}]}], field: value},
        )

    assert response.status_code == 400
    assert field in response.json()["error"]["message"]


async def test_a_control_the_serving_dialect_cannot_express_names_it(fixture) -> None:
    """`top_k` has no equivalent in the OpenAI chat API, and this model is served over it. The
    request fails with the control named — and `topP`, which the same dialect *does* have, must not
    appear in the reason or the operator fixes the wrong thing.
    """
    status, message = await _ask(fixture, "hi", topP=0.5, topK=5)

    assert status == 400
    assert "top_k" in message
    assert "top_p" not in message


async def test_a_refused_request_still_leaves_an_audit_row(fixture) -> None:
    """`FRD-122`'s rule, applied to the newest refusal: the log records what was *asked*, not only
    what was served. A control that made a request unservable is exactly the kind of thing somebody
    asks about a week later."""
    await _ask(fixture, "hi", topK=5)

    rows = await fixture.rows()
    assert rows, "a refused request left no trace at all"
    assert any(row["status"] == 400 for row in rows)


async def test_a_body_over_the_ceiling_is_refused_and_recorded(fixture, engine) -> None:
    """The gap `FRD-122` left open, found by counting rows rather than by reading code.

    The size check runs in pure ASGI **before** any route, so the exception boundary that records
    every other refusal never ran: a 20 MB body was answered 413 and left no trace at all. The row
    is deliberately **unattributed** — the credential in the header has not been verified at that
    point, and recording it would let anybody write another system's name into the audit trail by
    sending one oversized request.
    """
    from sqlalchemy import text

    async def count() -> int:
        async with engine.connect() as connection:
            return int(
                (
                    await connection.execute(
                        text("SELECT count(*) FROM request_logs WHERE outcome='request_too_large'")
                    )
                ).scalar()
                or 0
            )

    before = await count()
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1beta/models/{MODEL}:generateContent",
            headers=fixture.headers(),
            json={"contents": [{"role": "user", "parts": [{"text": "x" * (9 * 1024 * 1024)}]}]},
        )

    assert response.status_code == 413
    await asyncio.sleep(2.0)
    assert await count() == before + 1

    async with engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    text(
                        "SELECT subject, credential, source_ip, model, operation,"
                        " request_payload IS NULL AS no_body FROM request_logs"
                        " WHERE outcome='request_too_large' ORDER BY created_at DESC LIMIT 1"
                    )
                )
            )
            .mappings()
            .one()
        )

    assert row["model"] == MODEL, "the row names what was asked for"
    assert row["operation"] == "generateContent"
    assert row["source_ip"]
    assert not row["subject"] and not row["credential"]
    # The body is not kept: it is over the ceiling, and storing what we refused to read would undo
    # the reason for refusing it.
    assert row["no_body"]
