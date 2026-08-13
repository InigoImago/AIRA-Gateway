"""Thinking resolution and validation (FRD-111).

Two things are being pinned here and they are different in kind.

The **validation matrix** is ordinary: each way a setting can be wrong has its own code, because a
client that cannot tell "wrong mode" from "budget too high" can correct neither.

The **resolution** is the one with money in it. What goes upstream and what the budget reserves
against have to be the same number; a level resolved from the catalog and a reservation computed
from something else would be a limit bounding a request nobody made.
"""

from __future__ import annotations

import pytest

from aira_common.models import Capability, ThinkingMode
from aira_gateway.catalog import ModelDeclaration
from aira_gateway.core.canonical import Thinking
from aira_gateway.thinking import (
    INVALID_THINKING_MODE,
    MISSING_THINKING_TOKEN_COUNT,
    THINKING_TOKEN_COUNT_TOO_HIGH,
    THINKING_TOKEN_COUNT_TOO_LOW,
    UNEXPECTED_THINKING_TOKEN_COUNT,
    ThinkingRejected,
    permitted_by,
    reserved_tokens,
    resolve,
)

_THINKS = frozenset({Capability.GENERATE, Capability.THINKING})


def _model(**thinking: object) -> ModelDeclaration:
    return ModelDeclaration(
        name="m", declared=True, capabilities=_THINKS, thinking=dict(thinking) or None
    )


# == validation ==================================================================================


def test_a_mode_the_model_does_not_declare_is_refused() -> None:
    with pytest.raises(ThinkingRejected) as caught:
        resolve(Thinking(mode=ThinkingMode.HIGH), _model(modes=["auto"]))
    assert caught.value.code == INVALID_THINKING_MODE
    # The message names what *is* available: the fix is a one-line client change, and saying so
    # is the difference between that and a support conversation.
    assert "auto" in caught.value.message


def test_a_model_that_declares_no_thinking_at_all_says_so() -> None:
    undeclared = ModelDeclaration(name="m")
    with pytest.raises(ThinkingRejected) as caught:
        resolve(Thinking(mode=ThinkingMode.AUTO), undeclared)
    assert caught.value.code == INVALID_THINKING_MODE
    assert "catalog" in caught.value.message


def test_limited_without_a_count_is_its_own_failure() -> None:
    with pytest.raises(ThinkingRejected) as caught:
        resolve(Thinking(mode=ThinkingMode.LIMITED), _model(modes=["limited"], max_tokens=1000))
    assert caught.value.code == MISSING_THINKING_TOKEN_COUNT


@pytest.mark.parametrize(
    ("tokens", "code"),
    [(127, THINKING_TOKEN_COUNT_TOO_LOW), (24_577, THINKING_TOKEN_COUNT_TOO_HIGH)],
)
def test_each_bound_is_refused_at_its_own_boundary(tokens: int, code: str) -> None:
    """One below the minimum and one above the maximum — the boundary is where an off-by-one
    lives, and a test at 0 and 999999 would pass against an implementation that had one."""
    model = _model(modes=["limited"], min_tokens=128, max_tokens=24_576)
    with pytest.raises(ThinkingRejected) as caught:
        resolve(Thinking(mode=ThinkingMode.LIMITED, tokens=tokens), model)
    assert caught.value.code == code


@pytest.mark.parametrize("tokens", [128, 24_576])
def test_the_boundaries_themselves_are_accepted(tokens: int) -> None:
    model = _model(modes=["limited"], min_tokens=128, max_tokens=24_576)
    assert resolve(Thinking(mode=ThinkingMode.LIMITED, tokens=tokens), model).tokens == tokens


def test_a_token_count_on_a_mode_that_takes_none_is_refused() -> None:
    """FR-1: required for `limited`, **rejected** otherwise. Accepting and ignoring it would let a
    caller believe they had bounded a budget they had not."""
    with pytest.raises(ThinkingRejected) as caught:
        resolve(Thinking(mode=ThinkingMode.AUTO, tokens=500), _model(modes=["auto"]))
    assert caught.value.code == UNEXPECTED_THINKING_TOKEN_COUNT


def test_disabled_asked_of_a_model_that_cannot_think_sends_nothing() -> None:
    """ "Do not think" asked of a model that cannot is already true, so the request is accepted —
    and **nothing is sent**, which is the correction of 2026-08-11.

    It used to resolve to an explicit `Thinking(disabled, tokens=0)`, which the Gemini mapper turns
    into `thinkingConfig: {thinkingBudget: 0}` — and Google answers **400** for every model that
    cannot have thinking switched off. Measured against `gemini-flash-latest`: refused with a token
    cap, refused with a large cap, refused alone; drop the parameter and the same model answers in
    one output token.

    `FRD-124`'s "off has to be said out loud" is about a model that **can** think, where silence
    lets the default win. Here there is no default to beat, and asserting an off is a claim about
    the provider's API rather than about the request."""
    assert resolve(Thinking(mode=ThinkingMode.DISABLED), ModelDeclaration(name="m")) is None


def test_disabled_with_a_count_is_still_refused() -> None:
    with pytest.raises(ThinkingRejected) as caught:
        resolve(Thinking(mode=ThinkingMode.DISABLED, tokens=5), ModelDeclaration(name="m"))
    assert caught.value.code == UNEXPECTED_THINKING_TOKEN_COUNT


# == resolution ==================================================================================


def test_no_setting_resolves_to_the_models_declared_default() -> None:
    """FR-4. Not the provider's default and not none: the predecessor applies this, so a gateway
    that sent nothing would answer differently for a reason nobody could see."""
    model = _model(modes=["medium", "auto"], default={"mode": "medium"}, levels={"medium": 2048})
    resolved = resolve(None, model)
    assert resolved is not None
    assert (resolved.mode, resolved.tokens) == (ThinkingMode.MEDIUM, 2048)


def test_no_setting_and_no_declaration_sends_nothing() -> None:
    assert resolve(None, ModelDeclaration(name="m")) is None


def test_a_malformed_default_does_not_take_every_request_with_it() -> None:
    """Management validates the declaration, so this is unreachable through the API. A catalog
    typo must not fail every request for that model — omitting the setting is what happened
    before the feature existed, so it is the safe reading."""
    assert resolve(None, _model(modes=["auto"], default={"mode": "aggressive"})) is None


def test_an_abstract_level_is_translated_by_the_models_own_table() -> None:
    """The level→budget mapping is per model and lives in the catalog, which is what keeps a new
    model from being a code change (§5.2)."""
    model = _model(modes=["high", "low"], levels={"high": 8192, "low": 512})
    assert resolve(Thinking(mode=ThinkingMode.HIGH), model).tokens == 8192
    assert resolve(Thinking(mode=ThinkingMode.LOW), model).tokens == 512


def test_a_level_with_no_entry_falls_back_to_the_declared_maximum() -> None:
    """Conservative in the safe direction, and settled against the real figure the moment the
    response arrives. A silent zero would be a reservation ignoring the expensive half."""
    model = _model(modes=["high"], max_tokens=16_000)
    assert resolve(Thinking(mode=ThinkingMode.HIGH), model).tokens == 16_000


def test_disabled_resolves_to_a_budget_of_zero_not_to_nothing() -> None:
    """The distinction that matters: a model whose default is `auto` must be told *explicitly* to
    stop, or its default silently wins over the caller's instruction."""
    model = _model(modes=["disabled", "auto"], default={"mode": "auto"})
    resolved = resolve(Thinking(mode=ThinkingMode.DISABLED), model)
    assert resolved is not None
    assert resolved.tokens == 0


# == what the reservation sees ===================================================================


@pytest.mark.parametrize(
    ("setting", "expected"),
    [
        (None, 0),
        (Thinking(mode=ThinkingMode.DISABLED, tokens=0), 0),
        (Thinking(mode=ThinkingMode.LIMITED, tokens=20_000), 20_000),
        (Thinking(mode=ThinkingMode.AUTO, tokens=None), 0),
    ],
)
def test_the_reservation_adds_the_resolved_budget(setting: Thinking | None, expected: int) -> None:
    assert reserved_tokens(setting) == expected


# == the per-hop check ===========================================================================


def test_a_candidate_that_cannot_think_is_refused_by_name() -> None:
    """The dispatch chain skips it rather than serving a quieter answer: less reasoning than was
    asked for is not an error, it is a worse answer with a 200 on it."""
    setting = Thinking(mode=ThinkingMode.HIGH, tokens=8192)
    reason = permitted_by(setting, ModelDeclaration(name="other"))
    assert reason is not None
    assert "undeclared" in reason


def test_a_candidate_whose_bounds_are_narrower_is_refused() -> None:
    setting = Thinking(mode=ThinkingMode.LIMITED, tokens=20_000)
    narrow = ModelDeclaration(
        name="narrow",
        declared=True,
        capabilities=_THINKS,
        thinking={"modes": ["limited"], "min_tokens": 128, "max_tokens": 4096},
    )
    assert permitted_by(setting, narrow) is not None


def test_a_capable_candidate_is_permitted() -> None:
    setting = Thinking(mode=ThinkingMode.LIMITED, tokens=2048)
    model = _model(modes=["limited"], min_tokens=128, max_tokens=24_576)
    assert permitted_by(setting, model) is None


@pytest.mark.parametrize("setting", [None, Thinking(mode=ThinkingMode.DISABLED, tokens=0)])
def test_a_request_that_asks_for_no_thinking_constrains_no_candidate(
    setting: Thinking | None,
) -> None:
    """Otherwise every fallback chain would start refusing candidates over a feature the request
    is not using — a limit that bites when nothing asked for it."""
    assert permitted_by(setting, ModelDeclaration(name="plain")) is None


# ---- one reading of a mode string, for every surface -------------------------------------------


@pytest.mark.parametrize("raw", ["HIGH", " high ", "High", "high"])
def test_both_surfaces_read_a_mode_string_the_same_way(raw: str) -> None:
    """The normalisation is the whole property, and it used to be written twice.

    Each surface's mapper spelled out its own `ThinkingMode(raw.strip().lower())` with its own
    copy of the refusal. Identical on the day it was written, and nothing compared them — so a
    surface that lost the `.strip()` would accept `" high"` from a client the other refused, with
    no error anywhere and no test that could tell. This project has paid for that shape more than
    once: an empty membership list read as "anything goes" on one surface and as nothing on the
    other.

    Asserted through **both mappers** rather than through `mode_from` alone. The shared function
    being right says nothing about whether a surface calls it — which is `FRD-124`'s lesson, and
    the reason the same argument is made twice in this repository.
    """
    from aira_gateway.api.gemini import schemas as gemini_schemas
    from aira_gateway.api.gemini.mapping import thinking_of as gemini_thinking
    from aira_gateway.api.kira import schemas as kira_schemas
    from aira_gateway.api.kira.mapping import thinking_of as kira_thinking

    gemini = gemini_thinking(gemini_schemas.ThinkingConfig(mode=raw))
    kira = kira_thinking(kira_schemas.ThinkingSetting(mode=raw))

    assert gemini is not None and kira is not None
    assert gemini.mode is kira.mode is ThinkingMode.HIGH


def test_an_unknown_mode_is_refused_the_same_way_on_both() -> None:
    """Including the code, because a migrating client's error handling switches on that string."""
    from aira_gateway.api.gemini import schemas as gemini_schemas
    from aira_gateway.api.gemini.mapping import thinking_of as gemini_thinking
    from aira_gateway.api.kira import schemas as kira_schemas
    from aira_gateway.api.kira.mapping import thinking_of as kira_thinking

    with pytest.raises(ThinkingRejected) as gemini:
        gemini_thinking(gemini_schemas.ThinkingConfig(mode="ponder"))
    with pytest.raises(ThinkingRejected) as kira:
        kira_thinking(kira_schemas.ThinkingSetting(mode="ponder"))

    assert gemini.value.code == kira.value.code == INVALID_THINKING_MODE
    assert gemini.value.message == kira.value.message
    assert "ponder" in gemini.value.message
