"""What a caller says about the model's reasoning is what comes back (`FRD-135` FR-4/FR-5).

Three gaps a live round against Gemini found: an explicit `includeThoughts: false` was ignored where
the use case allows reasoning; thoughts were asked for with thinking switched off, which Google
refuses; and a stream asking for thoughts answered 200 without them.
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import Request
from fastapi.testclient import TestClient

from aira_common.models import ThinkingMode
from aira_gateway.api.serving.controls import resolve_reasoning
from aira_gateway.app import create_app
from aira_gateway.auth.attribution import Attribution
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import CanonicalMessage, CanonicalRequest, Role, Thinking
from aira_gateway.upstreams.gemini_mapping import canonical_to_gemini_request

_BASE = CanonicalRequest(model="m", messages=[CanonicalMessage(role=Role.USER, text="hallo")])


def _request_for_a_use_case_that_returns_reasoning() -> Request:
    request = Request({"type": "http", "headers": [], "app": None})
    request.state.attribution = Attribution(subject="s", method="api_key", use_case="uc")
    # The per-request cache `use_case_record` reads first, so no database is needed.
    request.state.use_cases = {"uc": SimpleNamespace(include_reasoning=True)}
    return request


async def test_an_explicit_no_withholds_the_reasoning_the_use_case_allows() -> None:
    request = _request_for_a_use_case_that_returns_reasoning()

    unsaid = await resolve_reasoning(request, _BASE, asked_for=None)
    asked = await resolve_reasoning(request, _BASE, asked_for=True)
    declined = await resolve_reasoning(request, _BASE, asked_for=False)

    assert unsaid.include_reasoning is True, "on means returned (FR-5)"
    assert asked.include_reasoning is True
    assert declined.include_reasoning is False, "a caller who declined must not receive it"


def test_thoughts_are_not_asked_for_while_thinking_is_off() -> None:
    """Google refuses `includeThoughts` without thinking, and there is nothing to return."""
    reasoning_on = _BASE.model_copy(update={"include_reasoning": True})

    off = canonical_to_gemini_request(
        reasoning_on.model_copy(update={"thinking": Thinking(mode=ThinkingMode.DISABLED)})
    )
    auto = canonical_to_gemini_request(
        reasoning_on.model_copy(update={"thinking": Thinking(mode=ThinkingMode.AUTO)})
    )

    assert off["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}
    assert auto["generationConfig"]["thinkingConfig"]["includeThoughts"] is True


def test_a_stream_asking_for_thoughts_is_refused_rather_than_answered_without_them() -> None:
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    url = "/v1beta/models/mock-1:streamGenerateContent"

    def body(include: bool) -> dict:
        return {
            "contents": [{"role": "user", "parts": [{"text": "hallo"}]}],
            "generationConfig": {"thinkingConfig": {"includeThoughts": include}},
        }

    with TestClient(app) as client:
        asked = client.post(url, json=body(True))
        declined = client.post(url, json=body(False))

    assert asked.status_code == 400
    assert asked.json()["error"]["status"] == "INVALID_ARGUMENT"
    assert "includeThoughts" in asked.json()["error"]["message"]
    assert "stream" in asked.json()["error"]["message"]
    assert declined.status_code == 200, declined.text
