"""A use case that asked for the model's reasoning gets it, whichever dialect answered (`FRD-135`).

`FRD-135` FR-3 makes reasoning a **use case's** decision and its acceptance criteria say *"with it
on, thoughts reach the caller"* — without naming a dialect, because a governance switch that works
on one vendor and not another is not a governance switch.

Only the Gemini mapper acted on it. The canonical request carried `include_reasoning` through the
whole pre-dispatch sequence and the other two mappers never read it, so a use case that had turned
reasoning on and routed to a Claude model or to any OpenAI-dialect server was answered `200` with
no thoughts and nothing said — the silent drop `FRD-124` exists against, on a field somebody had
deliberately switched on. Both halves were invisible: the *counting* worked on every dialect
(`reasoning_tokens` is read from all three), so the reporting screen showed thinking being paid for
while the answer never carried any.

**Both directions in every case.** The default is off, and it has to stay off by construction
rather than by call sites remembering: these two dialects return reasoning whether or not anybody
asked — a reasoning model on the OpenAI dialect thinks with no `reasoning_effort` at all — so a
mapper that read the field unconditionally would hand every caller a chain of thought that
`FRD-135` §8 keeps behind the same gate as the stored prompt.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from aira_common.tokens import StaticTokenSource
from aira_gateway.core.canonical import CanonicalMessage, CanonicalRequest, Role
from aira_gateway.upstreams.openai import OpenAIAdapter
from aira_gateway.upstreams.openai.mapping import openai_to_canonical
from aira_gateway.upstreams.openai.transport import OpenAITransport
from aira_gateway.upstreams.vertex.adapters import VertexAnthropicAdapter, VertexModel
from aira_gateway.upstreams.vertex.anthropic_mapping import anthropic_to_canonical
from aira_gateway.upstreams.vertex.transport import VertexTransport

THOUGHTS = "First I check the customer number, then I answer."
ANSWER = "Your order ships on Tuesday."


def _openai(reasoning: str = THOUGHTS) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {"content": ANSWER, "reasoning": reasoning},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 40,
            "completion_tokens_details": {"reasoning_tokens": 32},
        },
    }


def _anthropic() -> dict[str, Any]:
    return {
        "content": [
            {"type": "thinking", "thinking": THOUGHTS},
            {"type": "text", "text": ANSWER},
        ],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 40},
    }


@pytest.mark.parametrize(
    ("mapper", "payload"),
    [(openai_to_canonical, _openai()), (anthropic_to_canonical, _anthropic())],
    ids=["openai-dialect", "anthropic"],
)
def test_the_use_case_asked_and_the_thoughts_come_back(mapper, payload) -> None:
    answer = mapper(payload, "m", include_reasoning=True)

    assert answer.reasoning == THOUGHTS
    # The answer is still only the answer: reasoning travels beside it, never glued to the front,
    # or a caller cannot tell what the model said from what it thought.
    assert answer.text == ANSWER


@pytest.mark.parametrize(
    ("mapper", "payload"),
    [(openai_to_canonical, _openai()), (anthropic_to_canonical, _anthropic())],
    ids=["openai-dialect", "anthropic"],
)
def test_a_use_case_that_did_not_ask_is_told_nothing(mapper, payload) -> None:
    """The provider sent it anyway. Off is the default and has to survive a forgetful call site."""
    answer = mapper(payload, "m")

    assert answer.reasoning == ""
    assert answer.text == ANSWER


def test_thinking_is_counted_whether_or_not_it_is_shown() -> None:
    """`FRD-135` FR-1: a use case is charged for reasoning it never sees, so the figure is
    unconditional — that is what makes "what did thinking cost us" answerable at all."""
    withheld = openai_to_canonical(_openai(), "m")
    shown = openai_to_canonical(_openai(), "m", include_reasoning=True)

    assert withheld.usage is not None and withheld.usage.reasoning_tokens == 32
    assert shown.usage is not None and shown.usage.reasoning_tokens == 32


def test_a_model_that_returned_no_thoughts_is_served_normally() -> None:
    """`FRD-135`'s own acceptance criterion. The switch is permission, not a requirement."""
    answer = openai_to_canonical(_openai(reasoning=""), "m", include_reasoning=True)

    assert answer.reasoning == ""
    assert answer.text == ANSWER


def _request(*, include_reasoning: bool) -> CanonicalRequest:
    return CanonicalRequest(
        model="local-1",
        messages=[CanonicalMessage(role=Role.USER, text="hi")],
        include_reasoning=include_reasoning,
    )


def _adapter() -> OpenAIAdapter:
    client = httpx.AsyncClient(
        base_url="http://local.test",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=_openai())),
    )
    return OpenAIAdapter(OpenAITransport(client=client), ["local-1"])  # type: ignore[arg-type]


@pytest.mark.parametrize("asked", [True, False], ids=["asked", "did-not-ask"])
async def test_the_adapter_carries_the_switch_rather_than_dropping_it(asked: bool) -> None:
    """**The wire between the request and the mapper**, which the mapper tests above cannot see.

    Written because a mutation said so: dropping `include_reasoning=` from the adapter's one call
    site left the whole suite green, and a default that is safe is exactly the kind that hides a
    lost argument — the answer is simply always withheld, which looks like the feature being off.
    The switch travels from the request the pre-dispatch sequence built (`serving.resolve_reasoning`
    reads the use case's row, never the caller) to the mapper that renders it, and nothing between
    them gets to decide.
    """
    answer = await _adapter().generate(_request(include_reasoning=asked))

    assert answer.reasoning == (THOUGHTS if asked else "")


@pytest.mark.parametrize("asked", [True, False], ids=["asked", "did-not-ask"])
async def test_the_vertex_anthropic_adapter_carries_it_too(asked: bool) -> None:
    """The same wire, on the other dialect. Two adapters passing one switch is two chances to drop
    it, and a dropped one is invisible: the answer is simply always withheld, which looks exactly
    like the feature being off."""
    adapter = VertexAnthropicAdapter(
        VertexTransport(
            project="p",
            tokens=StaticTokenSource("t"),
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(lambda _: httpx.Response(200, json=_anthropic()))
            ),
        ),
        [VertexModel("eu", "anthropic", "claude-1")],
        default_max_tokens=2048,
    )

    answer = await adapter.generate(
        CanonicalRequest(
            model="claude-1",
            messages=[CanonicalMessage(role=Role.USER, text="hi")],
            include_reasoning=asked,
        )
    )

    assert answer.reasoning == (THOUGHTS if asked else "")
    assert answer.text == ANSWER
