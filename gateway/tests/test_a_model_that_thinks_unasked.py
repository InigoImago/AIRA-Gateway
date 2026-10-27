"""A model that thinks when nothing is asked returns that reasoning once its default says so
(`FRD-151`).

Most requests name no thinking setting — OpenCode sends none until somebody picks a variant. The
gateway asks for the reasoning only where it knows the model is thinking (`FRD-135` FR-5): Google
refuses `includeThoughts` from a model that is not, and Claude's newest models return their
thinking empty unless asked for a summary. What tells the gateway is the model's declared thinking
default — the setting that reproduces what the vendor does anyway — so with it the reasoning comes
back at no extra cost, and without it, nothing changes.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from aira_common.models import Capability
from aira_gateway.api.serving.prepare import fitted
from aira_gateway.app import create_app
from aira_gateway.auth.attribution import Attribution
from aira_gateway.catalog import ModelDeclaration
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import CanonicalMessage, CanonicalRequest, Role, Thinking
from aira_gateway.db.models import ModelRead, UseCaseRead
from aira_gateway.thinking import resolve
from aira_gateway.upstreams.gemini_mapping import canonical_to_gemini_request
from aira_gateway.upstreams.vertex.anthropic_mapping import canonical_to_anthropic

#: What Claude Opus 5.5 is drafted as: it thinks adaptively whenever a request says nothing.
THINKS_UNASKED = {
    "modes": ["auto"],
    "levels": ["low", "medium", "high", "xhigh", "max"],
    "default": {"mode": "auto"},
}
#: The same words, and nothing said about what the model does unasked.
UNDECLARED_DEFAULT = {"modes": [], "levels": ["low", "medium", "high", "xhigh", "max"]}


def _unset(model: str = "m") -> CanonicalRequest:
    """A request that names no thinking setting, from a use case that returns reasoning."""
    return CanonicalRequest(
        model=model,
        messages=[CanonicalMessage(role=Role.USER, text="hello")],
        include_reasoning=True,
    )


def _fitted(thinking: dict[str, Any]) -> CanonicalRequest:
    declaration = ModelDeclaration(name="m", declared=True, thinking=thinking)
    asked = _unset()
    return asked.model_copy(update={"thinking": resolve(asked.thinking, declaration)})


def test_claude_is_asked_for_the_summary_of_the_thinking_it_does_anyway() -> None:
    body = canonical_to_anthropic(_fitted(THINKS_UNASKED), max_tokens=8192)

    assert body["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert "output_config" not in body, "the vendor's own effort, not one the gateway picked"


def test_claude_with_no_default_is_sent_what_it_was_sent_before() -> None:
    assert "thinking" not in canonical_to_anthropic(_fitted(UNDECLARED_DEFAULT), max_tokens=8192)


def test_gemini_is_asked_for_the_thoughts_at_its_own_level() -> None:
    declared = {"modes": [], "levels": ["minimal", "medium", "high"], "default": {"mode": "medium"}}

    body = canonical_to_gemini_request(_fitted(declared))

    assert body["generationConfig"]["thinkingConfig"] == {
        "thinkingLevel": "medium",
        "includeThoughts": True,
    }


def test_gemini_with_no_default_is_not_asked_for_thoughts_google_would_refuse() -> None:
    declared = {"modes": ["disabled", "auto", "limited"], "max_tokens": 24576}

    body = canonical_to_gemini_request(_fitted(declared))

    assert "thinkingConfig" not in body.get("generationConfig", {})


# == the whole path, as OpenCode meets it ========================================================


async def _seed(app: Any, thinking: dict[str, Any]) -> None:
    async with app.state.db_sessionmaker() as session:
        session.add(
            ModelRead(model="mock-1", capabilities=["generate", "thinking"], thinking=thinking)
        )
        session.add(UseCaseRead(slug="uc", name="uc"))
        await session.commit()


def _thoughts(client: TestClient) -> list[str]:
    """The thought parts of a streamed answer to a request that names no thinking setting."""
    response = client.post(
        "/uc/uc/v1beta/models/mock-1:streamGenerateContent?alt=sse",
        json={"contents": [{"role": "user", "parts": [{"text": "hello"}]}]},
    )
    assert response.status_code == 200, response.text
    elements = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    return [
        part["text"]
        for element in elements
        for candidate in element.get("candidates") or []
        for part in (candidate.get("content") or {}).get("parts") or []
        if part.get("thought")
    ]


@pytest.mark.parametrize(
    ("thinking", "shown"),
    [(THINKS_UNASKED, ["[mock thought] "]), (UNDECLARED_DEFAULT, [])],
    ids=["default-declared", "no-default"],
)
async def test_a_stream_that_names_no_setting_carries_the_thinking_the_default_declares(
    thinking: dict[str, Any], shown: list[str]
) -> None:
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    with TestClient(app) as client:
        await _seed(app, thinking)
        thoughts = _thoughts(client)

    assert thoughts == shown


# == whose setting it is (`FRD-152`) =============================================================


def _request_of_a_use_case() -> Request:
    request = Request({"type": "http", "headers": [], "app": None})
    request.state.attribution = Attribution(subject="s", method="api_key", use_case="uc")
    # The per-request cache `use_case_record` reads first, so no database is needed.
    request.state.use_cases = {"uc": SimpleNamespace(prompt_caching_enabled=False)}
    return request


@pytest.mark.parametrize(
    ("asked", "from_default"),
    [(None, True), (Thinking(mode="high"), False)],
    ids=["the-models-default", "the-callers-own"],
)
async def test_a_default_is_marked_as_the_installations_and_a_setting_as_the_callers(
    asked: Thinking | None, from_default: bool
) -> None:
    """A dialect that cannot carry thinking into a turn leaves a default out and refuses what the
    caller asked for, so it has to know which of the two it was given."""
    declaration = ModelDeclaration(
        name="m",
        declared=True,
        capabilities=frozenset({Capability.GENERATE, Capability.THINKING}),
        thinking=THINKS_UNASKED,
    )

    sent = await fitted(
        _request_of_a_use_case(), _unset().model_copy(update={"thinking": asked}), declaration
    )

    assert sent.thinking is not None
    assert sent.thinking_from_default is from_default
