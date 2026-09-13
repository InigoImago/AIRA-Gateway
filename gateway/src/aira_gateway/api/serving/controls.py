"""The individual pre-dispatch controls.

Each is a single rule. The order they run in is itself a guarantee, and it is owned by
`prepare.prepare_for_dispatch` — a surface never calls these directly (`test_surface_layering.py`).
"""

from __future__ import annotations

from fastapi import Request

from aira_common.models import Capability
from aira_common.money import cost_nanos
from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.api.serving.context import attribution_of, catalog_of, use_case_record
from aira_gateway.budgets.ledger import Amounts
from aira_gateway.budgets.service import Reservation
from aira_gateway.catalog import MAX_ACCOUNTABLE_TOKENS, ModelDeclaration
from aira_gateway.core.canonical import CanonicalRequest
from aira_gateway.ratelimit.buckets import per_minute
from aira_gateway.state import (
    budgets_of,
    pricing_of,
    rate_limits_of,
    settings_of,
    suspensions_of,
)

#: Every verb that embeds. A set rather than one name, so a new embedding verb is never asked for
#: the *generation* capability (`FRD-113` added the batch verb).
EMBEDDING_METHODS = frozenset({"embedContent", "batchEmbedContents"})

#: The prompt-cache lifetime a use case gets unless it chose the long one (`FRD-133`).
DEFAULT_CACHE_TTL = "5m"

#: The range every supported dialect accepts for each sampling control, under the name a caller
#: sends (`FRD-124`). Checked before dispatch, so an out-of-range value is the caller's `400` naming
#: the field, never an upstream refusal recorded as the provider's error. A dialect with a narrower
#: range still refuses upstream.
SAMPLING_BOUNDS: dict[str, tuple[str, float, float]] = {
    "temperature": ("temperature", 0.0, 2.0),
    "top_p": ("topP", 0.0, 1.0),
    "top_k": ("topK", 1.0, float("inf")),
    "presence_penalty": ("presencePenalty", -2.0, 2.0),
    "frequency_penalty": ("frequencyPenalty", -2.0, 2.0),
}


# == refusals that need no model ==================================================================


async def refuse_if_retired(request: Request) -> None:
    """Refuse a request to a retired use case (`FRD-607`).

    Only safe because retirement leaves a tombstone row: a tombstone is positive knowledge, while
    absence may just be a use case whose event has not arrived yet. Needed because OIDC membership
    resolves from the token alone, so members of a retired use case could otherwise keep calling
    it after its budget, rate limit and pipeline were gone.
    """
    slug = getattr(attribution_of(request), "use_case", None)
    record = await use_case_record(request, slug)
    if record is not None and record.deleted_at is not None:
        # 403, not 404: the credential and the membership are real; "wrong slug" would mislead.
        raise GeminiHTTPError(
            403,
            f"Use case '{slug}' has been retired and no longer serves requests. Its record is "
            "kept for audit; ask a Global Administrator if you believe this is wrong.",
            "PERMISSION_DENIED",
        )


def check_not_empty(canonical: CanonicalRequest) -> None:
    """Refuse a request that asks nothing: it would be billed for an answer to nothing."""
    if canonical.is_empty:
        raise GeminiHTTPError(
            400,
            "The request carries no text and no attachment. It would be billed for an answer to "
            "nothing.",
            "INVALID_ARGUMENT",
        )


def check_sampling(canonical: CanonicalRequest) -> None:
    """Refuse a sampling value outside the range every dialect accepts, naming the field."""
    for attribute, (name, low, high) in SAMPLING_BOUNDS.items():
        value = getattr(canonical, attribute)
        if value is not None and not low <= value <= high:
            allowed = f"at least {low:g}" if high == float("inf") else f"from {low:g} to {high:g}"
            raise GeminiHTTPError(
                400,
                f"{name} {value:g} is outside the accepted range ({allowed}).",
                "INVALID_ARGUMENT",
            )


async def check_tools_permitted(request: Request, canonical: CanonicalRequest) -> None:
    """A request may declare functions only if its use case enabled tool calling (`FRD-131` FR-3).

    Costs nothing for requests without tools. `FAILED_PRECONDITION`, not `PERMISSION_DENIED`: the
    request is fine; a configuration somebody can change is missing.
    """
    if not canonical.tools:
        return
    use_case = getattr(attribution_of(request), "use_case", None)
    if use_case is None:
        raise GeminiHTTPError(
            400,
            "Tool calling is configured per use case, and this request names none. Send it with a "
            "use case that has tool calling enabled.",
            "FAILED_PRECONDITION",
        )
    record = await use_case_record(request, use_case)
    if record is None or not record.tools_enabled:
        raise GeminiHTTPError(
            400,
            f"Use case '{use_case}' has not enabled tool calling. An administrator of the use "
            "case can turn it on; it is off by default so that only the use cases which need "
            "functions can declare them.",
            "FAILED_PRECONDITION",
        )


async def resolve_reasoning(
    request: Request, canonical: CanonicalRequest, *, asked_for: bool | None
) -> CanonicalRequest:
    """Whether the model's reasoning comes back (`FRD-135` FR-3/FR-4) — the use case decides.

    Enabled: the request carries it and the adapter asks for thoughts. Disabled but asked for:
    refused by name, because a 200 without thoughts would be a silent drop (`FRD-124`).
    """
    use_case = getattr(attribution_of(request), "use_case", None)
    record = await use_case_record(request, use_case) if use_case else None
    allowed = bool(record is not None and record.include_reasoning)
    if asked_for and not allowed:
        raise GeminiHTTPError(
            400,
            "'includeThoughts' asks for the model's reasoning, and this use case does not return "
            "it. An administrator of the use case can turn it on; it is off by default because "
            "reasoning can restate the prompt verbatim and is stored with the answer when it is "
            "on (FRD-135). Send it as false, or omit it.",
            "FAILED_PRECONDITION",
        )
    # An explicit no withholds them even where the use case allows them: Google's own meaning of
    # the field, and a caller who declined must not receive text it did not ask for.
    returned = allowed and asked_for is not False
    return canonical.model_copy(update={"include_reasoning": True}) if returned else canonical


async def guard_before_work(request: Request, *, units: int = 1) -> None:
    """Suspension, rate limit and "already over budget" — the controls that need no model.

    They run before the pipeline, which can call a model itself, so a refused caller never pays
    for a classifier. Counted against the **person** (`aira_gateway.scopes.person`), so a key and a
    sign-in by one human share an allowance; a suspension additionally targets the subject and the
    credential, because "block this leaked key" must not stop the person holding it.
    """
    attribution = attribution_of(request)
    use_case = getattr(attribution, "use_case", None)
    subject = getattr(attribution, "subject", None)
    caller = getattr(attribution, "person", None)
    credential = getattr(attribution, "credential", None)

    suspensions = suspensions_of(request)
    rate_limits = rate_limits_of(request)
    budgets = budgets_of(request)
    throttles = await suspensions.check(use_case, subject, credential, caller)
    await rate_limits.check(
        use_case,
        caller,
        units,
        extra=[per_minute(t.key, t.limit_rpm, label=t.label) for t in throttles],
    )
    await budgets.refuse_if_exhausted(use_case, caller)
    request.state.early_gate_taken = True


# == rules the catalogue decides ==================================================================


async def check_declaration(
    request: Request, *, model: str, method: str, requested: int | None
) -> ModelDeclaration:
    """Every rule the catalogue decides, before anything expensive happens (FRD-114).

    Returns the declaration so the caller can act on ``deprecated`` without a second lookup.
    Whether a model may *embed* is decided by `embedding.validate` alone — one rule, one place.
    """
    declaration = await catalog_of(request).declaration(model)

    if method not in EMBEDDING_METHODS and not declaration.can(Capability.GENERATE):
        raise GeminiHTTPError(
            400, f"Model '{model}' does not support generation.", "INVALID_ARGUMENT"
        )

    if requested is not None and requested <= 0:
        # A negative cap would silently truncate the answer (`words[:limit]`).
        raise GeminiHTTPError(
            400,
            f"maxOutputTokens must be a positive number of tokens, not {requested}.",
            "INVALID_ARGUMENT",
        )

    cap = declaration.max_output_tokens
    if requested is not None and cap is not None and requested > cap:
        raise GeminiHTTPError(
            400,
            f"maxOutputTokens {requested} exceeds the {cap} this model accepts.",
            "INVALID_ARGUMENT",
        )
    if requested is not None and requested > MAX_ACCOUNTABLE_TOKENS:
        # Unconditional, unlike the model cap above (which may be undeclared): a caller's number
        # beyond what the counters can hold would push budget enforcement onto its racy fallback.
        # Refused rather than clamped, because the caller wrote it and can fix it.
        raise GeminiHTTPError(
            400,
            f"maxOutputTokens {requested} exceeds the {MAX_ACCOUNTABLE_TOKENS} this gateway can "
            "account for.",
            "INVALID_ARGUMENT",
        )
    return declaration


async def cache_prefix_wanted(request: Request, declaration: ModelDeclaration) -> bool:
    """Whether to mark the stable prefix cacheable (`FRD-133`): the use case opted in and the
    routed model can honour it.

    Deliberately **not** a dispatch condition: a model that cannot cache still answers correctly,
    just at a higher price, so it is served uncached instead of skipped.
    """
    if Capability.PROMPT_CACHING not in declaration.capabilities:
        return False
    slug = getattr(attribution_of(request), "use_case", None)
    record = await use_case_record(request, slug)
    return bool(record is not None and record.prompt_caching_enabled)


async def cache_ttl_for(request: Request) -> str:
    """The cache lifetime this use case chose; anything unrecognised reads as the cheap default."""
    slug = getattr(attribution_of(request), "use_case", None)
    record = await use_case_record(request, slug)
    chosen = getattr(record, "prompt_cache_ttl", "5m") if record is not None else "5m"
    return "1h" if chosen == "1h" else DEFAULT_CACHE_TTL


# == the reservation ==============================================================================


async def estimate(
    request: Request,
    *,
    model: str,
    max_output_tokens: int | None,
    attachments: list[str] | None = None,
    units: int = 1,
    extra_tokens: int = 0,
) -> Amounts:
    """What this request is expected to consume, for the reservation (FRD-405).

    Deliberately conservative — every token priced at the output rate — because over-reserving
    briefly is the safe direction for a spend limit, and `settle` replaces the estimate with the
    real figure. An unpriced model estimates zero cost: unknown cannot constrain a cost limit.
    """
    settings = settings_of(request)
    declaration = await catalog_of(request).declaration(model)
    tokens = declaration.output_cap(max_output_tokens) or settings.budget_estimate_output_tokens
    # Attachments are input a character count cannot predict (FRD-110 §5.3).
    tokens += declaration.attachment_tokens(attachments or [])
    # A thinking budget is billed as output and is often the largest number on the request.
    tokens += extra_tokens
    # Clamped here, where no single caller wrote the sum (caller-named figures are refused by name
    # in `check_declaration`): a figure the counters cannot hold would disable enforcement.
    tokens = min(tokens, MAX_ACCOUNTABLE_TOKENS)
    price = await pricing_of(request).price_for(model)
    cost = 0 if price is None else cost_nanos(tokens, price.output_per_million_nanos)
    return Amounts(tokens=tokens, requests=units, cost_nanos=cost)


async def enforce_pre_dispatch(
    request: Request,
    *,
    model: str,
    max_output_tokens: int | None,
    attachments: list[str] | None = None,
    units: int = 1,
    extra_tokens: int = 0,
) -> Reservation:
    """Reserve budget against the model **routing** chose — so it runs after the pipeline.

    The reservation makes requests in flight visible to each other's check. ``units`` is how many
    requests this call is (one per text in an embedding batch, `FRD-113` FR-6); ``extra_tokens`` is
    consumption the body does not predict, today a thinking budget (`FRD-111` FR-5).
    """
    if not getattr(request.state, "early_gate_taken", False):
        # Reserving without the early gate would serve traffic nobody is rate-limiting.
        raise RuntimeError(
            "enforce_pre_dispatch reached without guard_before_work; this surface would serve "
            "unmetered traffic. Take the early gate before the pipeline."
        )

    attribution = attribution_of(request)
    use_case = getattr(attribution, "use_case", None)
    # The same pot the early gate checked, or one caller could pass one and fail the other.
    caller = getattr(attribution, "person", None)

    expected = await estimate(
        request,
        model=model,
        max_output_tokens=max_output_tokens,
        attachments=attachments,
        units=units,
        extra_tokens=extra_tokens,
    )
    return await budgets_of(request).guard(use_case, caller, estimated=expected)
