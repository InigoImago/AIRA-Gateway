"""The individual pre-dispatch controls.

Each is a single rule. The order they run in is itself a guarantee, and it is owned by
`prepare.prepare_for_dispatch` — a surface never calls these directly (`test_surface_layering.py`).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator

from fastapi import Request

from aira_common.models import Capability
from aira_common.money import cost_nanos
from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.api.serving.context import attribution_of, catalog_of, use_case_record
from aira_gateway.attachments import Limits, check_bounds
from aira_gateway.budgets.ledger import Amounts
from aira_gateway.budgets.service import Reservation
from aira_gateway.catalog import MAX_ACCOUNTABLE_TOKENS, ModelDeclaration
from aira_gateway.core.canonical import CanonicalRequest, TextPart, ToolCallPart, ToolResultPart
from aira_gateway.pricing import Price
from aira_gateway.ratelimit.buckets import per_minute
from aira_gateway.ratelimit.errors import RateLimited
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

#: Characters per token where a reservation counts text it has not tokenised — about what prose
#: averages. Close rather than exact: `settle` replaces the estimate with the provider's figure.
CHARACTERS_PER_TOKEN = 4

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


class UseCaseRetired(GeminiHTTPError):
    """A request to a retired use case (`FRD-607`).

    Its own type so the audit outcome names what happened, rather than inferring it from a status
    another refusal could share.
    """


class UseCaseUnknown(GeminiHTTPError):
    """A request to a use case this gateway holds no record of (`FRD-204`). Its own type for the
    same reason as `UseCaseRetired`."""


async def refuse_unless_live(request: Request, slug: str | None) -> None:
    """Serve a use case only while this gateway holds its record and the record is not retired.

    **Absence refuses.** Every control a use case carries — the models released to it, whether its
    content is stored, its retirement — lives on its row, and each reads its permissive value when
    there is none: every model, every prompt stored, never retired. So a request to a use case
    without a row is refused, not served with those defaults. One created a moment ago is refused
    until its event has arrived; a Keycloak group `/use-cases/<slug>` naming a use case Management
    does not have is refused for good.

    **A tombstone refuses too** (`FRD-607`): OIDC membership resolves from the token alone, so the
    members of a retired use case could otherwise keep calling it after its budget, rate limit and
    pipeline were gone. A request naming no use case is not this rule's to refuse.
    """
    if not slug:
        return
    record = await use_case_record(request, slug)
    if record is None:
        # 403 like retirement: the credential and the membership are real, and nothing the caller
        # sends differently would be served.
        raise UseCaseUnknown(
            403,
            f"Use case '{slug}' is not known to this gateway: its configuration has not arrived "
            "from Management. A use case created moments ago serves once it has; otherwise ask "
            "an administrator of the use case.",
            "PERMISSION_DENIED",
        )
    if record.deleted_at is not None:
        # 403, not 404: the credential and the membership are real; "wrong slug" would mislead.
        raise UseCaseRetired(
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


def check_attachment_bounds(canonical: CanonicalRequest) -> None:
    """Refuse a request carrying more attachments, or more bytes of them, than one request may.

    Across the whole request — one document split over five messages is still one large request —
    and here, for every surface, rather than in one surface's parser: there the other surface had
    none, and a KIRA request carried hundreds of attachments, each decoded, hashed, forwarded and
    reserved for.
    """
    check_bounds([part.size for part in canonical.attachments], Limits())


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
        why = (
            "reasoning is a use-case setting, and this request names no use case"
            if use_case is None
            else f"an administrator of use case '{use_case}' has turned reasoning off"
        )
        raise GeminiHTTPError(
            400,
            f"'includeThoughts' asks for the model's reasoning, and {why} (FRD-135). Send it as "
            "false, or omit it.",
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
    try:
        await rate_limits.check(
            use_case,
            caller,
            units,
            extra=[per_minute(t.key, t.limit_rpm, label=t.label) for t in throttles],
        )
    except RateLimited as exc:
        throttle = next((t for t in throttles if t.key == exc.bucket), None)
        if throttle is None:
            raise
        # Stopped on purpose, not going too fast: a throttle's refusal is a suspension's.
        raise throttle.refusal(exc.retry_after) from exc
    await budgets.refuse_if_exhausted(use_case, caller)
    request.state.early_gate_taken = True


# == rules the catalogue decides ==================================================================


async def check_declaration(
    request: Request, *, model: str, method: str, requested: int | None, speech: bool = False
) -> ModelDeclaration:
    """Every rule the catalogue decides, before anything expensive happens (FRD-114).

    Returns the declaration so the caller can act on ``deprecated`` without a second lookup.
    Whether a model may *embed* is decided by `embedding.validate` alone — one rule, one place.
    """
    declaration = await catalog_of(request).declaration(model)

    # A spoken answer is the speech capability's question, asked per hop (`SpeechSupported`).
    if method not in EMBEDDING_METHODS and not speech and not declaration.can(Capability.GENERATE):
        hint = (
            " It answers only with speech: send 'responseModalities': ['AUDIO'] and a "
            "'speechConfig' (FRD-624)."
            if declaration.can(Capability.SPEECH)
            else ""
        )
        raise GeminiHTTPError(
            400, f"Model '{model}' does not support generation.{hint}", "INVALID_ARGUMENT"
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
    chosen = getattr(record, "prompt_cache_ttl", None)
    return "1h" if chosen == "1h" else DEFAULT_CACHE_TTL


# == the reservation ==============================================================================


def prompt_texts(canonical: CanonicalRequest) -> Iterator[str]:
    """Every text a request sends a model, which it is billed as input for: the conversation with
    the tool calls and results in it, the functions it declares and the schema it asks for.
    Attachments are not text; the model's declaration prices them (`estimate`)."""
    for message in canonical.messages:
        for part in message.parts:
            if isinstance(part, TextPart):
                yield part.text
            elif isinstance(part, ToolCallPart):
                yield part.name
                yield json.dumps(part.arguments, ensure_ascii=False, default=str)
            elif isinstance(part, ToolResultPart):
                yield part.name
                yield part.content
    for tool in canonical.tools:
        yield tool.name
        yield tool.description
        if tool.parameters is not None:
            yield tool.parameters.model_dump_json(exclude_none=True)
        if tool.parameters_json_schema is not None:
            yield tool.parameters_json_schema.to_json()
    if canonical.response_schema is not None:
        yield canonical.response_schema.model_dump_json(exclude_none=True)
    if canonical.response_json_schema is not None:
        yield canonical.response_json_schema.to_json()


def text_tokens(texts: Iterable[str]) -> int:
    """What text is expected to cost as input, counted rather than tokenised: its characters at
    `CHARACTERS_PER_TOKEN`, rounded up, so any text costs a token and none costs less than nothing.
    """
    return -(-sum(len(text) for text in texts) // CHARACTERS_PER_TOKEN)


async def estimate(
    request: Request,
    *,
    model: str,
    max_output_tokens: int | None,
    attachments: list[str] | None = None,
    units: int = 1,
    input_texts: Iterable[str] = (),
    extra_tokens: int = 0,
) -> Amounts:
    """What this request is expected to consume, for the reservation (FRD-405).

    Deliberately on the high side, because over-reserving briefly is the safe direction for a
    spend limit, and `settle` replaces the estimate with the real figure:

    - **output**: the cap that will be sent — the caller's, else the model's default, else the
      installation's estimate — and ``extra_tokens``, a thinking budget, which bills as output;
    - **input**: the request's own text (``input_texts``, `text_tokens`) and each attachment at
      what the model declares it costs. Without the prompt, an agent's 100 000-token context
      reserved what its answer might cost, and twelve such requests in flight all passed a limit
      with room for one.

    Each side is priced at its own rate: one rate for every token reserved nothing against a cost
    limit for a model priced on input alone, an embedding model. An unpriced model estimates zero
    cost: unknown cannot constrain a cost limit. Never negative, whatever a declaration or a
    resolved budget says — a negative reservation lowers the shared counter every other request
    is checked against.
    """
    settings = settings_of(request)
    declaration = await catalog_of(request).declaration(model)
    tokens = declaration.output_cap(max_output_tokens) or settings.budget_estimate_output_tokens
    # A thinking budget is billed as output and is often the largest number on the request.
    tokens += extra_tokens
    output = tokens = max(0, tokens)
    tokens += text_tokens(input_texts)
    # Attachments are input a character count cannot predict (FRD-110 §5.3).
    tokens += max(0, declaration.attachment_tokens(attachments or []))
    # Clamped here, where no single caller wrote the sum (caller-named figures are refused by name
    # in `check_declaration`): a figure the counters cannot hold would disable enforcement.
    tokens = min(tokens, MAX_ACCOUNTABLE_TOKENS)
    price = await pricing_of(request).price_for(model)
    cost = 0 if price is None else _reserved_cost(tokens, min(output, tokens), price)
    return Amounts(tokens=tokens, requests=units, cost_nanos=cost)


def _reserved_cost(tokens: int, output: int, price: Price) -> int:
    """``output`` of the reserved ``tokens`` at the output rate, the rest at the input rate."""
    return cost_nanos(output, price.output_per_million_nanos) + cost_nanos(
        tokens - output, price.input_per_million_nanos
    )


async def enforce_pre_dispatch(
    request: Request,
    *,
    model: str,
    max_output_tokens: int | None,
    attachments: list[str] | None = None,
    units: int = 1,
    input_texts: Iterable[str] = (),
    extra_tokens: int = 0,
) -> Reservation:
    """Reserve budget against the model **routing** chose — so it runs after the pipeline.

    The reservation makes requests in flight visible to each other's check. ``units`` is how many
    requests this call is (one per text in an embedding batch, `FRD-113` FR-6); ``input_texts`` is
    what the request sends (`prompt_texts`, or the texts to embed); ``extra_tokens`` is consumption
    the body does not predict, today a thinking budget (`FRD-111` FR-5).
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
        input_texts=input_texts,
        extra_tokens=extra_tokens,
    )
    reservation = await budgets_of(request).guard(use_case, caller, estimated=expected)
    # Kept on the request as well as returned, so that a refusal raised **after** the reservation
    # and **before** the accounting block can give it back (`release_reservation`). `Prepared`
    # carries it to the dispatch; the surface's refusal boundary never sees `Prepared`.
    request.state.reservation = reservation
    return reservation
