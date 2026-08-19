"""The catalog must not be able to hold a declaration that cannot work (FRD-114 FR-3, FR-4).

The catalog is a **runtime authority**: what it says decides whether a request is accepted. So a
self-contradictory declaration has to be refused where it is written. Discovering it where it is
enforced means a vendor error message for every request against that model, and nobody looking at
the catalog would see anything wrong.
"""

from __future__ import annotations

from typing import Any

import pytest
from aira_management.apps.catalog.models import Model
from aira_management.apps.catalog.validation import validate_declaration, validate_thinking

from .test_catalog import BASE, _client, _user

pytestmark = pytest.mark.django_db


def _declaration(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "capabilities": ["generate"],
        "hosting": "managed",
        "thinking": None,
        "embedding": None,
        "attachments": None,
        "max_output_tokens": None,
        "default_max_output_tokens": None,
    }
    base.update(over)
    return base


# -- the rule with teeth ------------------------------------------------------------------


def test_a_thinking_budget_at_or_above_the_output_cap_is_refused() -> None:
    """Anthropic draws thinking tokens from ``max_tokens`` (`FRD-119` §5.4), so this declaration
    describes a model that could never answer — and the catalog is where that has to be caught."""
    errors = validate_declaration(
        _declaration(
            max_output_tokens=8192,
            thinking={"modes": ["limited"], "max_tokens": 8192},
        )
    )
    assert any("below max_output_tokens" in error for error in errors)


def test_a_thinking_budget_below_the_output_cap_is_accepted() -> None:
    assert (
        validate_declaration(
            _declaration(
                max_output_tokens=8192,
                thinking={"modes": ["limited"], "min_tokens": 128, "max_tokens": 4096},
            )
        )
        == []
    )


# -- internal consistency ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("block", "fragment"),
    [
        # A block that offers nothing at all — neither a mode nor a level word.
        ({"modes": []}, "neither a mode nor a level"),
        ({"modes": ["telepathy"]}, "Unknown thinking modes"),
        ({"modes": ["limited"]}, "required when 'limited' is offered"),
        ({"modes": ["auto"], "min_tokens": 4096, "max_tokens": 128}, "must not exceed"),
        ({"modes": ["auto"], "default": {"mode": "high"}}, "one of the declared modes"),
        # A level list of the old shape, and one naming a mode as though it were a vendor word.
        ({"modes": ["auto"], "levels": {"telepathy": 10}}, "list of the level words"),
        ({"modes": ["auto"], "levels": ["auto"]}, "thinking modes rather than"),
    ],
)
def test_an_inconsistent_thinking_block_is_refused(block: dict[str, Any], fragment: str) -> None:
    errors = validate_declaration(_declaration(thinking=block))
    assert any(fragment in error for error in errors), errors


@pytest.mark.parametrize(
    ("block", "fragment"),
    [
        ({"task_types": [""]}, "non-empty strings"),
        ({"dimensions": []}, "non-empty list"),
        ({"dimensions": [768, -1]}, "positive integer"),
        ({"dimensions": [768], "default": 3072}, "one of the declared dimensions"),
    ],
)
def test_an_inconsistent_embedding_block_is_refused(block: dict[str, Any], fragment: str) -> None:
    errors = validate_declaration(_declaration(embedding=block))
    assert any(fragment in error for error in errors), errors


def test_an_attachment_declaration_needs_media_types_and_positive_estimates() -> None:
    assert any(
        "not a media type" in error
        for error in validate_declaration(_declaration(attachments={"media_types": {"pdf": {}}}))
    )
    assert any(
        "positive integer" in error
        for error in validate_declaration(
            _declaration(attachments={"media_types": {"application/pdf": {"tokens": 0}}})
        )
    )


def test_a_valid_attachment_declaration_is_accepted() -> None:
    assert (
        validate_declaration(
            _declaration(
                capabilities=["generate", "attachments"],
                attachments={"media_types": {"application/pdf": {"tokens": 2000}}},
            )
        )
        == []
    )


def test_an_unknown_capability_is_refused() -> None:
    errors = validate_declaration(_declaration(capabilities=["generate", "telepathy"]))
    assert any("Unknown capabilities" in error for error in errors)


def test_an_unknown_hosting_value_is_refused() -> None:
    assert validate_declaration(_declaration(hosting="somebody-elses-computer"))


def test_the_default_output_cap_may_not_exceed_the_maximum() -> None:
    errors = validate_declaration(
        _declaration(max_output_tokens=1024, default_max_output_tokens=4096)
    )
    assert any("must not exceed max_output_tokens" in error for error in errors)


# -- through the API ------------------------------------------------------------------------


def test_a_global_admin_declares_a_model_and_it_is_published(monkeypatch: Any) -> None:
    published: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        "aira_management.apps.catalog.views.emit",
        lambda event, payload: published.append((event, payload)),
    )

    response = _client(_user("admin", "global-admin")).post(
        BASE,
        {
            "name": "claude-sonnet-4-5@20250929",
            "capabilities": ["generate", "thinking", "structured_output", "attachments"],
            "publisher": "anthropic",
            "platform": "vertex",
            "addressing": {"publisher_path": "publishers/anthropic"},
            "underlying_model": "claude-sonnet-4-5",
            "max_output_tokens": 64000,
            "default_max_output_tokens": 4096,
            "thinking": {"modes": ["auto", "limited"], "min_tokens": 1024, "max_tokens": 32000},
            "hosting": "managed",
            "deprecated": False,
        },
        format="json",
    )
    assert response.status_code == 201, response.json()

    model = Model.objects.get(name="claude-sonnet-4-5@20250929")
    assert model.is_declared is True
    assert model.publisher == "anthropic"

    # Everything validation needs has to travel with the event: the gateway reads its own
    # read-model on the request path and never calls Management (FR-8).
    event, payload = published[-1]
    assert event == "model.upserted"
    assert payload["capabilities"] == [
        "generate",
        "thinking",
        "structured_output",
        "attachments",
    ]
    assert payload["max_output_tokens"] == 64000
    assert payload["thinking"]["max_tokens"] == 32000
    assert payload["publisher"] == "anthropic"


def test_the_api_refuses_a_contradictory_declaration() -> None:
    response = _client(_user("admin", "global-admin")).post(
        BASE,
        {
            "name": "impossible-1",
            "capabilities": ["generate", "thinking"],
            "max_output_tokens": 4096,
            "thinking": {"modes": ["limited"], "max_tokens": 8192},
        },
        format="json",
    )
    assert response.status_code == 400
    assert Model.objects.filter(name="impossible-1").count() == 0


def test_a_patch_is_validated_against_what_the_row_already_holds() -> None:
    """A PATCH that touches only ``max_output_tokens`` must be checked against the thinking block
    it cannot see — otherwise the two halves are each valid and the row is not."""
    client = _client(_user("admin", "global-admin"))
    client.post(
        BASE,
        {
            "name": "patched-1",
            "capabilities": ["generate", "thinking"],
            "max_output_tokens": 64000,
            "thinking": {"modes": ["limited"], "max_tokens": 32000},
        },
        format="json",
    )

    response = client.patch(f"{BASE}patched-1/", {"max_output_tokens": 1024}, format="json")

    assert response.status_code == 400
    assert Model.objects.get(name="patched-1").max_output_tokens == 64000


def test_a_model_with_only_a_price_is_undeclared_rather_than_invalid() -> None:
    """Every existing installation looks like this. Undeclared is a valid state — the gateway
    reads it as the baseline capabilities and nothing more (FR-7)."""
    response = _client(_user("admin", "global-admin")).post(
        BASE,
        {"name": "legacy-1", "input_price_per_million": "1", "output_price_per_million": "2"},
        format="json",
    )
    assert response.status_code == 201
    assert Model.objects.get(name="legacy-1").is_declared is False


def test_somebody_without_a_global_role_may_not_declare_a_model() -> None:
    """A thinking maximum is a cost ceiling: whoever can raise it can make one request cost as
    much as a month. Same restriction as prices, with more direct leverage (FRD-114 §5.4).

    Named for the caller it now describes: `use-case-admin` was an organisation-wide role and is
    one no longer (`ADR-0017`), so the person this guards against is anybody whose authority is a
    use case rather than the installation.
    """
    response = _client(_user("ucadmin")).post(
        BASE, {"name": "sneaky-1", "capabilities": ["generate"]}, format="json"
    )
    assert response.status_code == 403
    assert Model.objects.filter(name="sneaky-1").count() == 0


# ---- the shapes a declaration can be wrong in (2026-08-09) --------------------------------
#
# Thirteen branches of `validation.py` had no test. Every one of them is a refusal, so an untested
# branch is a malformed declaration the catalog would have accepted — and the catalog is a
# *runtime authority* (`FRD-114`): what it holds decides what a request may ask for. A block of the
# wrong type slipping through does not fail here, it fails per request against a vendor, with an
# error nobody can trace back to a form somebody filled in once.
#
# Written as one case per wrong shape rather than one case with everything wrong, because a
# validator that stops at the first problem would pass the second kind of test and leave an
# operator fixing one field per attempt.


@pytest.mark.parametrize(
    ("declaration", "expected"),
    [
        ({"capabilities": "generate"}, "capabilities must be a list"),
        ({"hosting": "somewhere"}, "hosting must be one of"),
        ({"thinking": ["disabled"]}, "thinking must be an object"),
        ({"thinking": {"modes": []}}, "thinking declares neither a mode nor a level"),
        ({"thinking": {"modes": "limited"}}, "thinking.modes must be a list"),
        ({"embedding": []}, "embedding must be an object"),
        ({"embedding": {"task_types": "SEMANTIC"}}, "embedding.task_types must be a list"),
        ({"embedding": {"task_types": [""]}}, "embedding.task_types must be a list"),
        ({"embedding": {"dimensions": []}}, "embedding.dimensions must be a non-empty list"),
        ({"embedding": {"dimensions": "768"}}, "embedding.dimensions must be a non-empty list"),
        ({"embedding": {"dimensions": [768, 0]}}, "embedding.dimensions must be a positive"),
        ({"embedding": {"dimensions": [768], "default": 512}}, "embedding.default must be one of"),
        ({"embedding": {"default": -1}}, "embedding.default must be a positive"),
        ({"attachments": []}, "attachments must be an object"),
        ({"attachments": {"media_types": []}}, "media_types must be a non-empty object"),
        # The shapes swapped with `ADR-0021`: a list of words is right, the old table is not.
        (
            {"thinking": {"modes": ["limited"], "max_tokens": 4096, "levels": {"low": 512}}},
            "thinking.levels must be a list of the level words",
        ),
        ({"attachments": {"media_types": {"pdf": {}}}}, "is not a media type"),
        (
            {"attachments": {"media_types": {"application/pdf": "yes"}}},
            "must be an object",
        ),
    ],
)
def test_a_declaration_of_the_wrong_shape_is_refused_by_name(
    declaration: dict[str, Any], expected: str
) -> None:
    errors = validate_declaration(declaration)

    assert errors, f"{declaration} was accepted"
    assert any(expected in error for error in errors), (
        f"{declaration} was refused, but for a reason that does not name the field: {errors}"
    )


def test_attachments_without_media_types_declares_nothing_rather_than_failing() -> None:
    """An `attachments` block that names no media types is a model that declares no attachment
    support — not a malformed declaration. Refusing it would make the block impossible to write
    incrementally, and `FRD-114` FR-7 already says undeclared means unsupported."""
    assert validate_declaration({"attachments": {}}) == []


def test_a_media_type_may_be_declared_with_no_specification() -> None:
    """`{"application/pdf": null}` is "this model reads PDFs, with no per-type estimate" — the
    ordinary case for a vendor that publishes no figure."""
    assert validate_declaration({"attachments": {"media_types": {"application/pdf": None}}}) == []


def test_a_boolean_is_not_a_positive_integer() -> None:
    """`True == 1` in Python, so a naive `isinstance(value, int) and value > 0` accepts it. A
    declaration carrying `dimensions: [true]` would then reach the gateway as a dimension of 1."""
    assert validate_declaration({"embedding": {"dimensions": [True]}})


def test_too_many_media_types_are_refused_rather_than_stored() -> None:
    """A bound on a caller-shaped structure, for the same reason the response schema has one: the
    catalog is read on the request path, and an unbounded map there is an unbounded cost."""
    media_types = {f"application/x-{n}": {} for n in range(64)}

    errors = validate_declaration({"attachments": {"media_types": media_types}})

    assert any("at most" in error for error in errors)


def test_too_many_dimensions_are_refused() -> None:
    errors = validate_declaration({"embedding": {"dimensions": list(range(1, 20))}})

    assert any("at most" in error for error in errors)


def test_a_thinking_default_outside_the_declared_bounds_is_refused() -> None:
    """`FRD-114`'s own rule, one level in: a default the model could never honour is a model that
    answers differently for a reason nobody can see."""
    errors = validate_declaration(
        {
            "thinking": {
                "modes": ["limited"],
                "min_tokens": 128,
                "max_tokens": 512,
                "default": {"mode": "limited", "tokens": 4096},
            }
        }
    )

    assert any("within the declared bounds" in error for error in errors)


def test_a_default_naming_an_undeclared_mode_is_refused() -> None:
    errors = validate_declaration(
        {"thinking": {"modes": ["disabled"], "default": {"mode": "limited", "tokens": 100}}}
    )

    assert any("must be one of the declared modes" in error for error in errors)


def test_a_valid_declaration_is_accepted() -> None:
    """The other half of a refusal test: a validator that refused everything would pass every
    case above."""
    assert (
        validate_declaration(
            {
                "capabilities": ["generate", "thinking", "attachments"],
                "hosting": "managed",
                # No `min_tokens` beside `disabled`, and the fixture used to have both — it
                # described a model that can stop thinking *and* refuses to think less than 128
                # tokens, which is not a model. Measured on 2026-08-19: `gemini-2.5-flash` takes a
                # budget of 0; `gemini-2.5-pro` refuses 0 and anything under 128, and offers no
                # `disabled` at all. A fixture is a claim about the world like any other.
                "thinking": {
                    "modes": ["disabled", "limited"],
                    "max_tokens": 512,
                    "default": {"mode": "limited", "tokens": 256},
                },
                "embedding": {"task_types": ["SEMANTIC_SIMILARITY"], "dimensions": [768]},
                "attachments": {"media_types": {"application/pdf": {"tokens": 250}}},
            }
        )
        == []
    )


#: What Vertex was **measured** to accept on 2026-08-19, one call per value. Two models of one
#: family, on one platform, on one afternoon — which is why the envelope belongs to the model and
#: not to the vocabulary.
#:
#:   gemini-2.5-flash   budget 0 stops thinking · 1…24576 accepted · 32768 refused
#:   gemini-2.5-pro     budget 0 **refused** ("does not support setting thinking_budget to 0")
#:                      1…127 refused · 128…32768 accepted · 40000 refused
#:   both               `thinkingLevel` refused — *"not supported by this model"* — which is why
#:                      neither declares a level word and both are asked with the modes instead.
MEASURED = {
    "gemini-2.5-flash": {
        "modes": ["disabled", "auto", "limited"],
        "max_tokens": 24576,
    },
    "gemini-2.5-pro": {
        "modes": ["auto", "limited"],
        "min_tokens": 128,
        "max_tokens": 32768,
    },
}


@pytest.mark.parametrize("model", sorted(MEASURED))
def test_a_measured_declaration_is_accepted(model: str) -> None:
    """The other half: rules that refuse a real model's real envelope are not rules, they are a
    validator nobody can satisfy."""
    assert validate_thinking(MEASURED[model], max_output_tokens=65536) == []


def test_disabled_and_a_floor_above_zero_cannot_both_be_true() -> None:
    """The contradiction that produced the question this was written for.

    A model with a minimum budget above zero cannot be told to stop. Declaring both is a
    configuration the console accepted and the provider refuses — at the caller, at request time,
    with a vendor message and nothing pointing at the catalogue. `gemini-2.5-pro` is exactly that
    model: it answers *"The model does not support setting thinking_budget to 0"*.
    """
    errors = validate_thinking(
        {"modes": ["disabled", "limited"], "min_tokens": 128},
        max_output_tokens=65536,
    )

    assert any("cannot stop thinking" in error for error in errors)


# ---- levels are the vendor's words (`ADR-0021`) -------------------------------------------------
#
# Four rules used to live here: a level below the floor, above the ceiling, for a mode the model
# does not offer, and levels-with-a-ceiling-and-no-table. Every one of them was correct about the
# `{level: token count}` table, and the table was the mistake. The owner, cataloguing a real model:
#
#   *"If I now pick medium or low, you ask me how many tokens that should be. You do not even find
#   these parameters on the vendors' own pages. How am I supposed to know?"*
#
# Nobody publishes it, so nobody could fill it, and the guess went upstream as a ceiling on the
# model's reasoning. The tests below guard what is left, which is deliberately thin: shape and
# duplication here, and the *model* answers whether a word works.


def test_a_list_of_words_is_the_shape_and_a_table_is_not() -> None:
    """The migration's own before-and-after, asserted. A catalog still holding the old table is a
    declaration nothing can act on, so it must be refused rather than quietly read as empty."""
    assert validate_thinking({"levels": ["low", "high"]}, max_output_tokens=65536) == []

    errors = validate_thinking({"levels": {"low": 512}}, max_output_tokens=65536)
    assert any("list of the level words" in error for error in errors), errors


def test_a_word_nobody_here_has_heard_of_is_accepted() -> None:
    """**The property free text exists for.** A vendor ships a word next month and a catalogue can
    hold it the same day. A rule refusing an unrecognised one would be this plane inventing a
    vocabulary again, one release behind whatever the vendors do."""
    assert validate_thinking({"levels": ["turbo", "glacial"]}, max_output_tokens=65536) == []


@pytest.mark.parametrize(
    ("levels", "expected"),
    [
        (["low", "low"], "the same level twice"),
        (["low", "LOW"], "the same level twice"),
        (["low", "  "], "empty word"),
        (["low", "auto"], "thinking modes rather than vendor level words"),
    ],
)
def test_a_level_list_that_says_nothing_useful_is_refused(levels: list, expected: str) -> None:
    """What is left to be wrong about a list of words. The last is the one with teeth: `auto`,
    `disabled` and `limited` are settings the gateway translates per dialect, so a model listing
    one as a *level* would send that string to the vendor and mean something else by it."""
    errors = validate_thinking({"levels": levels}, max_output_tokens=65536)
    assert any(expected in error for error in errors), errors


def test_a_block_that_offers_nothing_is_refused() -> None:
    """It used to be "modes must be non-empty", which stopped being right the moment a model could
    offer level words and no control mode — an OpenAI-compatible server that takes
    `reasoning_effort` and cannot say "you decide" is exactly that model."""
    assert validate_thinking({"levels": ["low"]}, max_output_tokens=65536) == []
    assert validate_thinking({"modes": ["auto"]}, max_output_tokens=65536) == []

    errors = validate_thinking({"min_tokens": 128}, max_output_tokens=65536)
    assert any("neither a mode nor a level" in error for error in errors), errors
