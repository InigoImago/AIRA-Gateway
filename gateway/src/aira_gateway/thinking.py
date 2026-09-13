"""Resolving and validating a thinking setting against the model that will serve it (FRD-111).

**Validate.** A mode the model does not offer, a ``limited`` budget outside its bounds — each
refused with its own code, so a client can tell which to correct.

**Resolve.** Absent a setting, the model's *declared default* applies — not the provider's, and not
none, or the gateway would answer differently from the predecessor for a reason nobody could see.

The reservation cares too: thinking tokens bill as output and can be an order of magnitude more
than a typical answer, so the budget cannot treat them as invisible (`FRD-405`).
"""

from __future__ import annotations

from aira_common.models import Capability, ThinkingMode
from aira_gateway.catalog import MAX_ACCOUNTABLE_TOKENS, ModelDeclaration
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
#: row now that the set is open.
MAX_MODE_LENGTH = 32


class ThinkingRejected(Exception):
    """A thinking setting the model will not accept, with the code that says which bound broke."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def mode_from(raw: str) -> str:
    """Normalise a client's mode word; whether the model offers it is decided later.

    Only the shape is checked here — non-empty and short. The vocabulary is the vendor's
    (`ADR-0021`), so the refusal lives in :func:`_validated`, against the model's declared list,
    naming what that model offers. One function for both surfaces, so they cannot drift apart on
    stripping or case.
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

    Off **where the model can be told to be off** — a reasoning model sent no directive thinks
    anyway (`FRD-125`) — and nothing where it cannot, because an unconditional off is a 400 from
    Google for such a model. Answers rather than raising: the filter still has to run.
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
        # Management validation prevents this. A malformed default reads as "no thinking" rather
        # than failing every request for the model; omitting the setting is always safe.
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
        # "Do not think" asked of a model that cannot is already true, so it is not refused.
        if requested.tokens is not None:
            raise ThinkingRejected(
                UNEXPECTED_THINKING_TOKEN_COUNT,
                "'tokens' applies only to the 'limited' thinking mode.",
            )
        # **Nothing to send**, not an explicit off: Google answers 400 to a zero budget for every
        # model whose thinking cannot be switched off. `FRD-124`'s "off has to be said out loud"
        # is about a model that can think; here there is no default to beat.
        return None

    # A control mode is ours and a level is the vendor's word; the caller does not care which,
    # so the refusal names both lists.
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
    # **No number is invented here**: a level goes upstream as the word, and the budget's figure is
    # `reserved_tokens`' separate question (`ADR-0021`).
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
    if tokens > MAX_ACCOUNTABLE_TOKENS:
        # Unconditional, because the model's bounds are nullable (`catalog.MAX_ACCOUNTABLE_TOKENS`).
        # `TOO_HIGH` rather than a new code: a migrating client already switches on it, and the
        # message says whose ceiling it is.
        raise ThinkingRejected(
            THINKING_TOKEN_COUNT_TOO_HIGH,
            f"A thinking budget of {tokens} is above the {MAX_ACCOUNTABLE_TOKENS} this gateway "
            "can account for.",
        )
    return tokens


def reserved_tokens(setting: Thinking | None, declaration: ModelDeclaration) -> int:
    """What the pre-dispatch reservation must add for this setting (`FRD-111` FR-5).

    **Asks the model, not the request.** ``setting.tokens`` is what goes on the wire, and only
    ``limited`` has one. The reservation is a spend estimate and reads the model's own declared
    ceiling — a real, vendor-stated number; a model without one reserves nothing extra rather than
    guessing. Over-reserving is the safe direction, and ``settle`` corrects it.
    """
    if setting is None or setting.mode == ThinkingMode.DISABLED:
        return 0
    if setting.mode == ThinkingMode.LIMITED:
        return setting.tokens or 0
    _, maximum = declaration.thinking_bounds
    return maximum or 0


def permitted_by(setting: Thinking | None, declaration: ModelDeclaration) -> str | None:
    """Why this candidate may not serve a request carrying ``setting``, or ``None`` if it may.

    Used per hop of the dispatch chain. A candidate that cannot honour the mode is **skipped**,
    never served with less thinking than was resolved — a worse answer returned with a 200.
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
