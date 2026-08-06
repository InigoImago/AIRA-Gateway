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

#: The predecessor's codes (`kira_api.md` §6.2), plus one for a case it has no code for.
INVALID_THINKING_MODE = "INVALID_THINKING_MODE"
MISSING_THINKING_TOKEN_COUNT = "MISSING_THINKING_TOKEN_COUNT"
THINKING_TOKEN_COUNT_TOO_LOW = "THINKING_TOKEN_COUNT_TOO_LOW"
THINKING_TOKEN_COUNT_TOO_HIGH = "THINKING_TOKEN_COUNT_TOO_HIGH"
#: A token count sent with a mode that takes none. The predecessor has no code for it because its
#: clients do not do it; ignoring the field would still be the wrong answer (`FRD-111` FR-1).
UNEXPECTED_THINKING_TOKEN_COUNT = "UNEXPECTED_THINKING_TOKEN_COUNT"


class ThinkingRejected(Exception):
    """A thinking setting the model will not accept, with the code that says which bound broke."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


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


def _default_for(declaration: ModelDeclaration) -> Thinking | None:
    default = declaration.thinking_default
    if default is None:
        return None
    mode = default.get("mode")
    if mode not in {member.value for member in ThinkingMode}:
        # A declaration Management validated cannot reach this. Treating a malformed one as "no
        # thinking" rather than raising is deliberate: a catalog typo must not take every request
        # for that model with it, and the setting is the one part of a request that is safe to
        # omit — omitting it is what happened before this feature existed.
        return None
    return _with_budget(
        Thinking(mode=ThinkingMode(mode), tokens=default.get("tokens")), declaration
    )


def _validated(requested: Thinking, declaration: ModelDeclaration) -> Thinking:
    mode = requested.mode
    declared = declaration.thinking_modes

    if mode is ThinkingMode.DISABLED and not declared:
        # "Do not think" asked of a model that cannot is already true. Refusing it would fail
        # requests that are asking for exactly what they are going to get, and every caller who
        # sets `disabled` defensively across a fleet of models would have to special-case ours.
        if requested.tokens is not None:
            raise ThinkingRejected(
                UNEXPECTED_THINKING_TOKEN_COUNT,
                "'tokens' applies only to the 'limited' thinking mode.",
            )
        return Thinking(mode=ThinkingMode.DISABLED, tokens=0)

    if not declaration.can(Capability.THINKING) or mode not in declared:
        raise ThinkingRejected(
            INVALID_THINKING_MODE,
            f"Model '{declaration.name}' does not offer the thinking mode '{mode}'. "
            + (
                f"It declares {sorted(str(m) for m in declared)}."
                if declared
                else "No thinking modes are declared for it in the model catalog."
            ),
        )

    if mode is not ThinkingMode.LIMITED and requested.tokens is not None:
        raise ThinkingRejected(
            UNEXPECTED_THINKING_TOKEN_COUNT,
            f"'tokens' applies only to the 'limited' thinking mode, not to '{mode}'.",
        )

    if mode is ThinkingMode.LIMITED:
        return Thinking(mode=mode, tokens=_limited_budget(requested.tokens, declaration))
    return _with_budget(Thinking(mode=mode, tokens=None), declaration)


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


def _with_budget(setting: Thinking, declaration: ModelDeclaration) -> Thinking:
    """Fill in what an abstract level or ``auto`` costs, from the model's own table.

    Deliberately resolves to the declared *maximum* when a level has no entry: over-reserving
    briefly is the safe direction for a spend limit, and the figure is corrected by ``settle`` the
    moment the response arrives. A silent zero is the failure this exists to prevent — a
    reservation that ignores the expensive half of a request is not a limit.
    """
    if setting.mode is ThinkingMode.DISABLED:
        return Thinking(mode=setting.mode, tokens=0)
    if setting.tokens is not None:
        return setting
    _, maximum = declaration.thinking_bounds
    return Thinking(
        mode=setting.mode, tokens=declaration.thinking_level_tokens(setting.mode) or maximum
    )


def reserved_tokens(setting: Thinking | None) -> int:
    """What the pre-dispatch reservation must add for this setting (`FRD-111` FR-5)."""
    if setting is None or setting.mode is ThinkingMode.DISABLED:
        return 0
    return setting.tokens or 0


def permitted_by(setting: Thinking | None, declaration: ModelDeclaration) -> str | None:
    """Why this candidate may not serve a request carrying ``setting``, or ``None`` if it may.

    Used per hop of the dispatch chain. A fallback candidate that cannot honour the mode is
    **skipped**, never served with a different amount of thinking than was resolved: an answer
    computed with less reasoning than asked for is not an error, it is a worse answer returned
    with a 200 — the same failure shape as a dropped attachment.
    """
    if setting is None or setting.mode is ThinkingMode.DISABLED:
        return None
    if not declaration.can(Capability.THINKING) or setting.mode not in declaration.thinking_modes:
        missing = "declares no thinking support" if declaration.declared else "is undeclared"
        return f"{missing}, so it cannot honour the '{setting.mode}' thinking this request asks for"
    if setting.mode is ThinkingMode.LIMITED and setting.tokens is not None:
        minimum, maximum = declaration.thinking_bounds
        if (minimum is not None and setting.tokens < minimum) or (
            maximum is not None and setting.tokens > maximum
        ):
            return (
                f"accepts a thinking budget between {minimum} and {maximum}, and this request "
                f"resolved to {setting.tokens}"
            )
    return None
