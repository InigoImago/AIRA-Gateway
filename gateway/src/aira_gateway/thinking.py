"""Resolving and validating a thinking setting against the model that will serve it (FRD-111).

Two jobs, and the second is the one with money in it.

**Validate.** A mode the model does not declare, a ``limited`` budget below its minimum or above
its maximum — each refused with its own code, because a client that cannot tell "wrong mode" from
"budget too high" cannot correct either.

**Resolve.** Absent a setting the model's *declared default* applies — not the provider's, and not
none. The predecessor applies that default, and a gateway that quietly sent nothing would answer
differently for a reason nobody could see. Resolution also fills in ``tokens`` for the abstract
levels from the catalog's level→budget table, so what goes upstream and what the budget reserves
against are the same number rather than two guesses.

Why the reservation cares: thinking tokens are billed as output tokens, and the predecessor's own
configuration allows budgets up to 32 768 of them — an order of magnitude more than a typical
answer. A gateway that enforces spend limits cannot treat the most expensive knob on the request
as invisible (`FRD-405` closed exactly this window for ordinary output).
"""

from __future__ import annotations

from aira_common.models import Capability, ThinkingMode
from aira_gateway.catalog import ModelDeclaration
from aira_gateway.core.canonical import Thinking

#: The predecessor's codes, plus one for a case it has no code for.
INVALID_THINKING_MODE = "INVALID_THINKING_MODE"
MISSING_THINKING_TOKEN_COUNT = "MISSING_THINKING_TOKEN_COUNT"
THINKING_TOKEN_COUNT_TOO_LOW = "THINKING_TOKEN_COUNT_TOO_LOW"
THINKING_TOKEN_COUNT_TOO_HIGH = "THINKING_TOKEN_COUNT_TOO_HIGH"
#: A token count sent with a mode that takes none. The contract has no code for it because its
#: clients do not do it; ignoring the field would still be the wrong answer (`FRD-111` FR-1).
UNEXPECTED_THINKING_TOKEN_COUNT = "UNEXPECTED_THINKING_TOKEN_COUNT"

#: A mode is a word. Bounded so a caller cannot push a paragraph into an error message or an audit
#: row now that the set is open — the only thing lost with the closed enum that was worth keeping.
MAX_MODE_LENGTH = 32


class ThinkingRejected(Exception):
    """A thinking setting the model will not accept, with the code that says which bound broke."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def mode_from(raw: str) -> str:
    """Normalise a client's mode word. **What it may be is a question for the model, not here.**

    This used to refuse anything outside a closed enum, at parse time, before the model was known.
    That was right while the vocabulary was ours; it is wrong now that a level is a word the vendor
    accepts (`ADR-0021`): a new vendor word would be refused by a gateway that has no opinion about
    it, and the caller would be told it "is not a thinking mode" when the model in question takes
    it happily.

    So the refusal moved to where the answer lives — :func:`_validated`, against the model's own
    declared list, with a message that **names what that model offers**. Which is the better error
    besides: `'turbo' is not a thinking mode` sends a reader to the specification, and `model X
    offers ['low', 'high']` sends them to the answer.

    Only the shape is checked here, because a mode is a word: non-empty, and short enough that it
    cannot be used to push a wall of text into an error message or an audit row.

    **Here rather than in each surface's mapper, where it was written twice.** Both spelled out the
    same four lines — the same normalisation, the same code, the same message — and the risk is not
    that the copies look different but that they *stop* being the same in a way no test compares:
    a surface that forgets ``.strip()`` accepts `" high"` from a client the other one refuses, and
    a surface that stops lowercasing turns `"HIGH"` into an error message naming the vocabulary it
    just rejected. Neither is an error anywhere. That is the shape this project has paid for
    repeatedly — an empty membership list meaning "anything goes" on one surface and nothing on the
    other, a kill switch guarded by a visibility predicate on one plane.

    """
    mode = raw.strip().lower()
    if not mode or len(mode) > MAX_MODE_LENGTH:
        raise ThinkingRejected(
            INVALID_THINKING_MODE,
            f"A thinking mode is a word of at most {MAX_MODE_LENGTH} characters.",
        )
    return mode


def resolve(requested: Thinking | None, declaration: ModelDeclaration) -> Thinking | None:
    """The setting to send upstream, or ``None`` when there is nothing to send.

    ``None`` and ``Thinking(disabled)`` are different answers on purpose. The first means the model
    was never going to think and no parameter is needed; the second means the model *would* have
    thought by default and this request is switching it off, which has to be said explicitly or the
    default silently wins.
    """
    if requested is None:
        return _default_for(declaration)
    return _validated(requested, declaration)


def for_a_classifier(declaration: ModelDeclaration) -> Thinking | None:
    """What a pipeline's LLM step should send, so it gets one word rather than a page of reasoning.

    Off **where the model can be told to be off**, and nothing at all where it cannot — the two
    cases the classifier used to collapse into one by sending `disabled` unconditionally,
    bypassing the catalog entirely. Measured: that is a 400 from Google for any model whose
    thinking cannot be switched off, which the classifier then swallowed as "no verdict".

    `FRD-125`'s original finding is the other side of the same coin and is preserved: a reasoning
    model sent no directive **thinks anyway** and spends a four-token allowance on it, so where the
    catalog says the model can be quietened, it is told so explicitly.

    This asks a different question from `resolve` — *what may we ask for* rather than *what did the
    caller ask for* — so it answers rather than raising: a filter whose model declares thinking
    without an off is a filter that still has to run.
    """
    if ThinkingMode.DISABLED not in declaration.thinking_modes:
        return None
    return resolve(Thinking(mode=ThinkingMode.DISABLED), declaration)


def _default_for(declaration: ModelDeclaration) -> Thinking | None:
    default = declaration.thinking_default
    if default is None:
        return None
    mode = default.get("mode")
    if not isinstance(mode, str) or not mode.strip():
        # A declaration Management validated cannot reach this. Treating a malformed one as "no
        # thinking" rather than raising is deliberate: a catalog typo must not take every request
        # for that model with it, and the setting is the one part of a request that is safe to
        # omit — omitting it is what happened before this feature existed.
        return None
    tokens = default.get("tokens")
    return Thinking(
        mode=mode.strip().lower(),
        tokens=tokens if isinstance(tokens, int) and not isinstance(tokens, bool) else None,
    )


def _validated(requested: Thinking, declaration: ModelDeclaration) -> Thinking | None:
    mode = requested.mode
    declared = declaration.thinking_modes

    if mode == ThinkingMode.DISABLED and not declaration.offers_thinking:
        # "Do not think" asked of a model that cannot is already true. Refusing it would fail
        # requests that are asking for exactly what they are going to get, and every caller who
        # sets `disabled` defensively across a fleet of models would have to special-case ours.
        if requested.tokens is not None:
            raise ThinkingRejected(
                UNEXPECTED_THINKING_TOKEN_COUNT,
                "'tokens' applies only to the 'limited' thinking mode.",
            )
        # **Nothing to send**, not an explicit off — corrected 2026-08-11 against a measurement.
        # This used to return `Thinking(DISABLED, tokens=0)`, which the Gemini mapper turns into
        # `thinkingConfig: {thinkingBudget: 0}`, and Google answers **400 for every model that
        # cannot have thinking switched off**: `gemini-flash-latest` refuses it alone, with a
        # token cap, with a large cap — in every combination. Drop the parameter and the same
        # model answers in one output token.
        #
        # It also contradicted this module's own docstring, which says `None` means "the model was
        # never going to think and no parameter is needed" — which is exactly the case this branch
        # is about. Asserting an off for a model that declares no thinking is a claim about the
        # provider's API, and `FRD-124`'s "off has to be said out loud" is about a model that
        # **can** think: there, silence means the default wins. Here there is no default to beat.
        return None

    # **Two lists, one question.** A control mode is one of ours and a level is the vendor's own
    # word; a caller does not know or care which kind theirs is, so the refusal names both rather
    # than telling somebody who asked for `low` that the model declares `['auto', 'disabled']`.
    offered = [str(m) for m in declared] + list(declaration.thinking_levels)
    if not declaration.can(Capability.THINKING) or mode not in offered:
        raise ThinkingRejected(
            INVALID_THINKING_MODE,
            f"Model '{declaration.name}' does not offer the thinking mode '{mode}'. "
            + (
                f"It offers {sorted(offered)}."
                if offered
                else "No thinking modes are declared for it in the model catalog."
            ),
        )

    if mode != ThinkingMode.LIMITED and requested.tokens is not None:
        raise ThinkingRejected(
            UNEXPECTED_THINKING_TOKEN_COUNT,
            f"'tokens' applies only to the 'limited' thinking mode, not to '{mode}'.",
        )

    if mode == ThinkingMode.LIMITED:
        return Thinking(mode=mode, tokens=_limited_budget(requested.tokens, declaration))
    # **No number is invented here.** A level word goes upstream as the word, and `auto` as
    # whatever that dialect spells "you decide" — see `reserved_tokens` for the figure the budget
    # holds back, which is a different question and was the same field until `ADR-0021`.
    return Thinking(mode=mode, tokens=None)


def _limited_budget(tokens: int | None, declaration: ModelDeclaration) -> int:
    if tokens is None:
        raise ThinkingRejected(
            MISSING_THINKING_TOKEN_COUNT,
            "The 'limited' thinking mode requires a token count.",
        )
    minimum, maximum = declaration.thinking_bounds
    if minimum is not None and tokens < minimum:
        raise ThinkingRejected(
            THINKING_TOKEN_COUNT_TOO_LOW,
            f"A thinking budget of {tokens} is below the {minimum} this model accepts.",
        )
    if maximum is not None and tokens > maximum:
        raise ThinkingRejected(
            THINKING_TOKEN_COUNT_TOO_HIGH,
            f"A thinking budget of {tokens} is above the {maximum} this model accepts.",
        )
    return tokens


def reserved_tokens(setting: Thinking | None, declaration: ModelDeclaration) -> int:
    """What the pre-dispatch reservation must add for this setting (`FRD-111` FR-5).

    **Asks the model, not the request.** This used to read ``setting.tokens``, which meant every
    setting had to carry a number — and a level has none to carry, so the catalog grew a
    ``level → token count`` table whose only reader was this line. The table asked whoever
    catalogued a model for a figure no vendor publishes, and the number it produced was then sent
    *upstream* as well, where it silently capped the model's reasoning. One field doing two jobs,
    and the dangerous job was the one nobody asked for.

    Two jobs now. ``setting.tokens`` is what goes on the wire and only ``limited`` has one, named
    by the caller. The reservation is a **spend estimate** and reads the model's own ceiling, which
    is a real, vendor-stated number: Google names it in its own refusal (*"supported values are
    integers from 1 to 24576"*).

    Over-reserving is the safe direction and briefly so: ``settle`` corrects the figure the moment
    the response arrives, and thinking tokens bill as output. A model with no declared ceiling
    reserves nothing extra rather than guessing — the output cap already bounds the request, and a
    guess here is the very thing this change removed.
    """
    if setting is None or setting.mode == ThinkingMode.DISABLED:
        return 0
    if setting.mode == ThinkingMode.LIMITED:
        return setting.tokens or 0
    _, maximum = declaration.thinking_bounds
    return maximum or 0


def permitted_by(setting: Thinking | None, declaration: ModelDeclaration) -> str | None:
    """Why this candidate may not serve a request carrying ``setting``, or ``None`` if it may.

    Used per hop of the dispatch chain. A fallback candidate that cannot honour the mode is
    **skipped**, never served with a different amount of thinking than was resolved: an answer
    computed with less reasoning than asked for is not an error, it is a worse answer returned
    with a 200 — the same failure shape as a dropped attachment.
    """
    if setting is None or setting.mode == ThinkingMode.DISABLED:
        return None
    offered = {str(m) for m in declaration.thinking_modes} | set(declaration.thinking_levels)
    if not declaration.can(Capability.THINKING) or setting.mode not in offered:
        missing = "declares no thinking support" if declaration.declared else "is undeclared"
        return f"{missing}, so it cannot honour the '{setting.mode}' thinking this request asks for"
    if setting.mode == ThinkingMode.LIMITED and setting.tokens is not None:
        minimum, maximum = declaration.thinking_bounds
        if (minimum is not None and setting.tokens < minimum) or (
            maximum is not None and setting.tokens > maximum
        ):
            return (
                f"accepts a thinking budget between {minimum} and {maximum}, and this request "
                f"resolved to {setting.tokens}"
            )
    return None
