"""Which spellings the compatibility surface accepts, checked in **both** directions.

`FRD-107` FR-2 names the predecessor's camelCase fields exactly: `maxTokens` and `responseSchema`.
Everything else it spells in snake_case. The module said something broader — that every field took
either spelling — while five fields carried an ``alias=`` that merely restated their own name,
which reads like a second spelling and is not one. Nothing behaved wrongly; a reader asking
whether `conversationHistory` was accepted would have been told yes by the module and no by the
server.

So this file asserts the claim rather than the implementation:

- every camelCase field FR-2 names is accepted, **and** its snake_case form is too
  (``populate_by_name``), because a compatibility surface that required the nicer spelling would
  not be one;
- no *other* camelCase spelling is quietly accepted — a surface whose strictness is one field
  wider than its documentation is one a migrating client discovers in production.

The second direction is the one that has value here. This repository has now four times found a
hand-written list that agreed with the constants in one direction only (the Kafka topics twice,
the group grant, the SPA's capability list), and the answer each time was the same: compare both
ways.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aira_gateway.api.kira import schemas

#: `FRD-107` FR-2. The wire spelling first, the Python attribute second.
CAMEL_CASE_FIELDS = [("maxTokens", "max_tokens"), ("responseSchema", "response_schema")]

BASE = {"request": {"parts": [{"text": "hi"}]}, "model_id": 1}
VALUES: dict[str, object] = {
    "maxTokens": 16,
    "responseSchema": {"type": "object"},
}


@pytest.mark.parametrize(("wire", "attribute"), CAMEL_CASE_FIELDS)
def test_the_predecessors_spelling_is_accepted(wire: str, attribute: str) -> None:
    parsed = schemas.ChatRequest.model_validate({**BASE, wire: VALUES[wire]})

    assert getattr(parsed, attribute) == VALUES[wire]


@pytest.mark.parametrize(("wire", "attribute"), CAMEL_CASE_FIELDS)
def test_the_snake_case_form_is_accepted_too(wire: str, attribute: str) -> None:
    """`populate_by_name`. A client that has already moved on should not have to move back."""
    parsed = schemas.ChatRequest.model_validate({**BASE, attribute: VALUES[wire]})

    assert getattr(parsed, attribute) == VALUES[wire]


@pytest.mark.parametrize(
    "camel",
    ["conversationHistory", "systemInstruction", "modelId", "taskType"],
)
def test_no_other_camel_case_spelling_is_accepted(camel: str) -> None:
    """The other direction, and the reason this file exists.

    These are the four fields whose no-op alias made the module look as though it took both forms.
    They are refused by name — which is `FRD-124`'s rule and Stage A's own ("an unsupported field
    is refused, never ignored"), so a migrating client learns at migration time rather than by
    wondering why its history has no effect. **That is the failure this guards against**: an
    ignored `conversationHistory` would not error, it would answer without the conversation.
    """
    with pytest.raises(ValidationError) as raised:
        schemas.ChatRequest.model_validate({**BASE, camel: None})

    assert camel in str(raised.value), "the refusal must name the field"


def test_every_alias_that_remains_says_something_new() -> None:
    """The guard against the state this file was written out of.

    An ``alias=`` equal to its own field name adds nothing and suggests a second spelling. Rather
    than trusting that nobody writes one again, the models are asked. Five were removed; a sixth
    would fail here instead of in a reader's head.
    """
    restated: list[str] = []
    for model in (
        schemas.ChatRequest,
        schemas.EmbeddingRequest,
        schemas.RequestContent,
        schemas.ConversationContent,
        schemas.ThinkingSetting,
        schemas.TextPart,
    ):
        for name, field in model.model_fields.items():
            if field.alias == name:
                restated.append(f"{model.__name__}.{name}")

    assert restated == [], (
        f"these aliases restate their own field name and mean nothing: {restated}. "
        "Either the field has a second spelling, in which case say what it is, or it has one "
        "spelling, in which case the alias only makes a reader believe otherwise."
    )
