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


def test_the_served_response_omits_the_thinking_count_rather_than_sending_null() -> None:
    """The same rule, asserted **on the wire** instead of on the schema.

    The test below already dumped a `UsageMetadata` with `exclude_none` and checked the key was
    gone. That proves the schema can do it, and the route did not: `canonical_to_gemini(...)
    .model_dump()` sent `"thoughtsTokenCount": null` on every buffered answer, which is the same
    invented field the docstring below refuses, wearing a different value.

    Its own comment records the earlier version of exactly this mistake — *"the first version of
    this asserted that the field exists and is omitted when empty — both true with the mapping
    handing over `None`, so the mutation that stopped filling it survived"*. One level further
    out, one more time: what a caller receives is decided by the route, so the route is what is
    asked.
    """
    from fastapi.testclient import TestClient

    from aira_gateway.app import create_app
    from aira_gateway.config import GatewaySettings

    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    with TestClient(app) as client:
        response = client.post(
            "/v1beta/models/mock-1:generateContent",
            json={"contents": [{"role": "user", "parts": [{"text": "hallo"}]}]},
        )

    assert response.status_code == 200, response.text
    usage = response.json()["usageMetadata"]
    # The mock does not think, so Google would send no such key at all.
    assert "thoughtsTokenCount" not in usage, usage
    # And the three that are always reported are still there — `exclude_none` must not be read as
    # "send less", only as "do not invent".
    assert {"promptTokenCount", "candidatesTokenCount", "totalTokenCount"} <= set(usage)


def test_the_caller_is_told_what_thinking_cost() -> None:
    """Google reports `thoughtsTokenCount`; this surface recorded it and did not hand it back.

    The figure existed all along — the mapping reads it from the upstream and the audit row keeps
    it as `reasoning_tokens` — and the caller saw only `candidatesTokenCount`. Measured against a
    real model on 2026-08-19: 796 completion tokens of which **764 were thinking**, and at the
    `low` level 1104 of which 443. Most of the bill, invisible, on the one number a caller checks.

    Deliberately **not** gated by `include_reasoning` (`FRD-135`): that decides whether the
    reasoning *text* is returned and stored, which is a question about content. A token count is a
    question about money, and the provider bills those tokens either way.

    Omitted rather than sent as `0` when nothing was thought, because Google omits it and a
    compatibility surface should not invent a field the original leaves out.
    """
    from aira_gateway.api.gemini import schemas
    from aira_gateway.api.gemini.mapping import canonical_to_gemini
    from aira_gateway.core.canonical import CanonicalResponse, CanonicalUsage

    # **Through the mapping, not only the schema.** The first version of this asserted that the
    # field exists and is omitted when empty — both true with the mapping handing over `None`, so
    # the mutation that stopped filling it survived. A schema test proves the shape; only this
    # proves the number arrives.
    answered = canonical_to_gemini(
        CanonicalResponse(
            model="gemini-2.5-flash",
            text="7",
            finish_reason="stop",
            usage=CanonicalUsage(
                prompt_tokens=17,
                completion_tokens=1104,
                total_tokens=1121,
                reasoning_tokens=443,
            ),
        )
    )
    assert answered.usageMetadata.thoughtsTokenCount == 443

    silently = canonical_to_gemini(
        CanonicalResponse(
            model="gemini-2.5-flash",
            text="7",
            finish_reason="stop",
            usage=CanonicalUsage(prompt_tokens=17, completion_tokens=553, total_tokens=570),
        )
    )
    assert silently.usageMetadata.thoughtsTokenCount is None

    spent = schemas.UsageMetadata(
        promptTokenCount=17,
        candidatesTokenCount=1104,
        totalTokenCount=1121,
        thoughtsTokenCount=443,
    )
    silent = schemas.UsageMetadata(
        promptTokenCount=17, candidatesTokenCount=553, totalTokenCount=570
    )

    assert spent.model_dump(exclude_none=True)["thoughtsTokenCount"] == 443
    assert "thoughtsTokenCount" not in silent.model_dump(exclude_none=True)
