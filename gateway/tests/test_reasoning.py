"""A model's reasoning: counted always, returned where a use case says so (`FRD-135`).

Two properties, and they are deliberately not the same kind of thing. **Counting** is not a
setting: providers bill thinking at the output rate, and an installation does not get to decide
whether it was charged. **Returning** is a decision per use case, because reasoning can restate the
prompt verbatim (`ADR-0016`).

The measurement that produced this file, against `gemini-2.5-flash` on 2026-08-17:
`prompt=25 candidates=1 thoughts=143 total=169`, of which AIRA recorded 26.
"""

from __future__ import annotations

from typing import Any

from aira_gateway.core.canonical import CanonicalMessage, CanonicalRequest, Role
from aira_gateway.upstreams.gemini_mapping import (
    canonical_to_gemini_request,
    gemini_response_to_canonical,
)


def _response(thoughts: int, *, parts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "candidates": [
            {
                "content": {"parts": parts or [{"text": "the answer"}]},
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 25,
            "candidatesTokenCount": 1,
            "thoughtsTokenCount": thoughts,
            "totalTokenCount": 26 + thoughts,
        },
    }


def test_thinking_is_billed_as_output_and_reported_apart() -> None:
    """The subset invariant `FRD-133` set for cache tokens, applied to the other end of the row:
    `completion_tokens` is what the response cost, `reasoning_tokens` says how much of it was
    thinking. A sibling field instead would have needed every summation in the codebase found."""
    usage = gemini_response_to_canonical(_response(143), "gemini-2.5-flash").usage

    assert usage.completion_tokens == 144
    assert usage.reasoning_tokens == 143
    assert usage.total_tokens == 169


def test_a_model_that_does_not_think_reports_nothing_rather_than_zero_of_something() -> None:
    usage = gemini_response_to_canonical(_response(0), "gemini-2.5-flash").usage

    assert usage.completion_tokens == 1
    assert usage.reasoning_tokens == 0


def test_thoughts_are_not_glued_to_the_answer() -> None:
    """Google marks thought parts inside the same array. A mapper that joined them all would hand a
    caller its reasoning as though it were the answer — worse than dropping it, and the reason
    `includeThoughts` was refused outright before this existed."""
    parsed = gemini_response_to_canonical(
        _response(20, parts=[{"text": "let me think", "thought": True}, {"text": "the answer"}]),
        "gemini-2.5-flash",
    )

    assert parsed.text == "the answer"
    assert parsed.reasoning == "let me think"


def test_the_provider_is_asked_for_thoughts_only_where_the_use_case_allows_it() -> None:
    """Google returns nothing extra unless asked, so a use case that turned reasoning on and never
    saw any would be looking at a switch that changed nothing (`FRD-125`)."""
    base = CanonicalRequest(
        model="gemini-2.5-flash",
        messages=[CanonicalMessage(role=Role.USER, text="hallo")],
    )

    off = canonical_to_gemini_request(base)
    on = canonical_to_gemini_request(base.model_copy(update={"include_reasoning": True}))

    assert "thinkingConfig" not in off.get("generationConfig", {})
    assert on["generationConfig"]["thinkingConfig"]["includeThoughts"] is True
