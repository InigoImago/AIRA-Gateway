"""Fallback-aware dispatch, and the conditions a candidate has to meet (FRD-302, ADR-0012 §3).

Two forms of one chain. :func:`dispatch_with_fallback` tries ``[model, *fallback_models]`` in order
and returns the first finished response; :func:`stream_with_fallback` tries the same candidates and
returns the first one that has **produced a chunk**, with that chunk already in hand. Both report
the position of the candidate that answered, so the audit trail can say a substitution happened
(`FRD-122` FR-3), and both report who was passed over and why.

**A chain must not degrade a request silently.** A fallback that cannot read the attachment,
enforce the schema, or serve from a permitted region does not fail — it answers fluently from less
than the caller sent, with a 200. So a candidate that fails a condition is **skipped** with its
reason, and when none qualifies the request fails with those reasons (`NoCapableModel`) rather than
as an upstream outage.

**A candidate that was tried and failed is reported too** (`ADR-0032`): the row says why the model
before the one that answered did not, which is the question somebody reading an audit trail
actually has. **And when none answered**, the error the chain raises carries the same report
(`PassedOver`): the row of a refused request is the one that most needs to say what was tried.

**A candidate whose wire format cannot carry the request is skipped like any other** — a level word
to Anthropic, a document to an OpenAI-compatible server, a thinking budget that does not fit the
output cap. The adapter finds out while mapping (`DialectUnsupported`), one step later than a
condition would, and a fallback on another dialect may serve the request as written.

**Each candidate is sent the request as the caller made it, with its own defaults.** What the
caller left to the model — the output cap, the thinking, the cache marker — is the candidate's to
fill (`fit`), never the first candidate's: a fallback held to the primary's default cap answers
with the primary's few tokens and a 200, which is the silent degradation above in another form.

Conditions arrive as an async predicate and the fit as an async function, so dispatch never learns
what a region, a media type or a default is.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from aira_common.logging import get_logger
from aira_gateway.core.canonical import CanonicalChunk, CanonicalRequest, CanonicalResponse
from aira_gateway.residency import RegionNotAllowed
from aira_gateway.telemetry import model_call_span
from aira_gateway.upstreams.base import (
    AmbiguousModel,
    DialectUnsupported,
    ProviderRegistry,
    Upstream,
    UpstreamError,
)

_log = get_logger("aira_gateway.pipeline")

#: Given a model name, why it may not serve this request — or ``None`` if it may.
Permits = Callable[[str], Awaitable[str | None]]


@dataclass(frozen=True, slots=True)
class Routing:
    """Everything the catalogue says about **reaching** one candidate.

    Kept together because they are read together: a hop that took the fallback's provider but kept
    the primary's ``addressing`` sent it to the primary's regions (`ADR-0011` — the caller's model
    name is never the platform's addressing, and a chain that changes one must change both).
    """

    provider: str = ""
    publisher: str = ""
    addressing: dict[str, Any] = field(default_factory=dict)


#: Given a model name, how the catalogue says to reach it.
RoutingOf = Callable[[str], Awaitable[Routing]]

#: Given the caller's request addressed to a candidate, the request as that candidate is sent it —
#: or why it cannot be sent one.
Fit = Callable[[CanonicalRequest], Awaitable[CanonicalRequest | str]]


@dataclass(frozen=True, slots=True)
class Skipped:
    """A candidate that was not tried, and why. Kept so the failure can explain itself."""

    model: str
    reason: str


@dataclass(frozen=True, slots=True)
class Attempted:
    """A candidate that **was** called and did not answer, with the upstream's own reason.

    Apart from `Skipped` because the two are different facts about the configuration: a skip is
    something an operator can fix, an attempt that failed is something a vendor can.
    """

    model: str
    reason: str


@dataclass(frozen=True, slots=True)
class PassedOver:
    """Who a chain moved past before it gave up: what the audit row of the refusal reports
    (`FRD-302` FR-11). Carried **on the error** the chain raises, because the refusal is recorded
    at the surface's exception boundary, which sees the error and nothing else of the chain."""

    skipped: tuple[Skipped, ...]
    failed: tuple[Attempted, ...]


#: The attribute an exhausted chain's error carries its :class:`PassedOver` under. An attribute
#: rather than a field of each error class: the errors are the adapters' (`UpstreamError`,
#: `DialectUnsupported`), and an adapter must not learn what a chain is.
_PASSED_OVER = "aira_passed_over"


def passed_over_by(error: BaseException) -> PassedOver | None:
    """What the chain that raised ``error`` passed over, or ``None`` if no chain raised it."""
    found = getattr(error, _PASSED_OVER, None)
    return found if isinstance(found, PassedOver) else None


def _exhausted[E: Exception](error: E, skipped: list[Skipped], failed: list[Attempted]) -> E:
    """``error``, marked with everything the chain passed over on the way to raising it.

    An outage's message names every model the chain tried and why each did not answer
    (`FRD-628` FR-6): the last provider's bare status — "Vertex upstream returned 503." — names
    neither the model nor the candidates before it, and a caller cannot see the audit row that
    does. The status stays the last upstream's, so a client's retry logic is unchanged.
    """
    setattr(error, _PASSED_OVER, PassedOver(tuple(skipped), tuple(failed)))
    if isinstance(error, UpstreamError) and failed:
        tried = [f"{entry.model}: {entry.reason}" for entry in failed]
        tried += [f"{entry.model}: skipped, {entry.reason}" for entry in skipped]
        error.message = f"No model answered this request ({'; '.join(tried)})."
    return error


class NoCapableModel(Exception):
    """No candidate could serve the request.

    Distinct from an upstream failure: "every model was excluded" is a configuration the operator
    can fix, and reporting it as a 502 sends the reader to the wrong place.
    """

    def __init__(self, skipped: list[Skipped]) -> None:
        self.skipped = skipped
        detail = "; ".join(f"{entry.model}: {entry.reason}" for entry in skipped)
        super().__init__(f"No model could serve this request ({detail}).")


@dataclass(frozen=True, slots=True)
class Dispatched:
    """A response, which candidate produced it (0 is the primary), and who was passed over."""

    response: CanonicalResponse
    candidate_index: int
    skipped: list[Skipped] = field(default_factory=list)
    failed: list[Attempted] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Streaming:
    """A stream that has already produced its first chunk, and what it took to get there.

    ``chunks`` begins with that chunk: the surface iterates it exactly as it iterated the adapter's
    own iterator before the chain existed, and cannot tell that one chunk was pulled early.
    """

    model: str
    candidate_index: int
    chunks: AsyncIterator[CanonicalChunk]
    skipped: list[Skipped] = field(default_factory=list)
    failed: list[Attempted] = field(default_factory=list)
    #: The adapter that answered and the request exactly as it was sent to it — what its vendor is
    #: asked to count when the caller leaves before the vendor reports usage (`FRD-128` FR-6).
    provider: Upstream | None = None
    sent: CanonicalRequest | None = None
    #: Where the first chunk came from: known here even when the caller leaves before reading it.
    region: str = ""


def _why(error: UpstreamError) -> str:
    """The upstream's own reason, with the status where it answered with one."""
    if error.status_code is None:
        return error.message
    return f"{error.message} (HTTP {error.status_code})"


async def _candidates(
    registry: ProviderRegistry,
    request: CanonicalRequest,
    fallback_models: tuple[str, ...],
    permits: Permits | None,
    routing_of: RoutingOf | None,
    skipped: list[Skipped],
    fit: Fit | None = None,
) -> AsyncIterator[tuple[int, str, Upstream, CanonicalRequest]]:
    """Each candidate that may be **called**, in order, with the request addressed — and, given
    ``fit``, fitted — to it.

    One walk for both forms of the chain, because every difference between them would be a
    difference in what a stream is allowed to reach — and that is how `:streamGenerateContent`
    came to apply no condition at all (`ADR-0032`). Candidates excluded on the way are appended to
    ``skipped``, so the caller reports them whether anything answered or not.
    """
    names = [request.model, *[model for model in fallback_models if model != request.model]]
    for index, model in enumerate(names):
        # Asked per candidate: a chain may cross providers, and a catalogued model resolves too
        # (`FRD-507`).
        routing = await routing_of(model) if routing_of is not None else Routing()
        provider = registry.provider_for(model, routing.provider, routing.publisher)
        if provider is None:
            skipped.append(Skipped(model, "no provider serves this model"))
            continue
        if permits is not None:
            refusal = await permits(model)
            if refusal is not None:
                skipped.append(Skipped(model, refusal))
                continue
        # The addressing moves with the model (see `Routing`). Without ``routing_of`` the request
        # keeps what it arrived with, as a caller that resolved it itself expects.
        update: dict[str, Any] = {"model": model}
        if routing_of is not None:
            update["addressing"] = routing.addressing
        addressed = request.model_copy(update=update)
        if fit is not None:
            fitted = await fit(addressed)
            if isinstance(fitted, str):
                skipped.append(Skipped(model, fitted))
                continue
            addressed = fitted
        yield index, model, provider, addressed


async def dispatch_with_fallback(
    registry: ProviderRegistry,
    request: CanonicalRequest,
    fallback_models: tuple[str, ...],
    *,
    permits: Permits | None = None,
    routing_of: RoutingOf | None = None,
    fit: Fit | None = None,
) -> Dispatched:
    skipped: list[Skipped] = []
    failed: list[Attempted] = []
    last_error: UpstreamError | None = None
    unsayable: list[DialectUnsupported] = []

    async for index, model, provider, addressed in _candidates(
        registry, request, fallback_models, permits, routing_of, skipped, fit
    ):
        try:
            # Per attempt, so each model tried leaves its own model-access record (`FRD-619`).
            with model_call_span(model, purpose="serve"):
                response = await provider.generate(addressed)
        except UpstreamError as exc:
            last_error = exc
            failed.append(Attempted(model, _why(exc)))
        except (AmbiguousModel, RegionNotAllowed) as exc:
            # A candidate that cannot be addressed — no region catalogued, or one outside
            # `AIRA_ALLOWED_REGIONS` — is a configuration fault, not a 500: skip it and move on.
            skipped.append(Skipped(model, str(exc)))
        except DialectUnsupported as exc:
            unsayable.append(exc)
            skipped.append(Skipped(model, str(exc)))
        else:
            return Dispatched(response, index, skipped, failed)

    # An upstream that was *tried* and failed is an outage, which the caller may usefully retry.
    # Everything else is a chain that had nothing to offer. Either way the error carries who was
    # passed over, for the refusal's row (`PassedOver`).
    if last_error is not None:
        raise _exhausted(last_error, skipped, failed)
    raise _exhausted(_nothing_could_serve(skipped, unsayable), skipped, failed)


async def stream_with_fallback(
    registry: ProviderRegistry,
    request: CanonicalRequest,
    fallback_models: tuple[str, ...],
    *,
    permits: Permits | None = None,
    routing_of: RoutingOf | None = None,
    fit: Fit | None = None,
) -> Streaming:
    """The chain for a streamed request, opened rather than awaited (`ADR-0032`, `FRD-302` FR-6).

    **The first chunk is the test.** A streaming adapter issues its HTTP request on the first
    ``__anext__``, not when the iterator is built — so pulling one chunk is the moment a candidate
    has proven it can serve, and the last moment at which replacing it is invisible to the caller.
    A candidate that fails before that chunk is passed over like any other; one that produced it
    owns the rest of the answer, because text from two models spliced into one answer is worse
    than the failure it would be hiding.

    An upstream whose stream ends with **no chunk at all** has answered. The chain stops there: a
    stream with a finish reason and no text is how a vendor's own safety filter refuses, and
    retrying that down the chain pays three models to refuse the same request three times.
    """
    skipped: list[Skipped] = []
    failed: list[Attempted] = []
    last_error: UpstreamError | None = None
    unsayable: list[DialectUnsupported] = []

    async for index, model, provider, addressed in _candidates(
        registry, request, fallback_models, permits, routing_of, skipped, fit
    ):
        chunks: AsyncIterator[CanonicalChunk] | None = None
        try:
            # Per attempt and **around the first chunk**, which is where the HTTP request is made,
            # so each tried model leaves its own model-access record (`FRD-619`). Entered and left
            # in one context: an entered context manager handed to the surface's generator would
            # reset its contextvar token in a context that never created it.
            with model_call_span(model, purpose="serve"):
                chunks = provider.stream_generate(addressed)
                first = await anext(chunks)
        except StopAsyncIteration:
            return Streaming(model, index, _nothing(), skipped, failed, provider, addressed)
        except UpstreamError as exc:
            last_error = exc
            failed.append(Attempted(model, _why(exc)))
            await _closed(chunks, model)
        except (AmbiguousModel, RegionNotAllowed) as exc:
            skipped.append(Skipped(model, str(exc)))
            await _closed(chunks, model)
        except DialectUnsupported as exc:
            unsayable.append(exc)
            skipped.append(Skipped(model, str(exc)))
            await _closed(chunks, model)
        else:
            return Streaming(
                model,
                index,
                _rejoined(first, chunks, model),
                skipped,
                failed,
                provider,
                addressed,
                first.served_region or "",
            )

    if last_error is not None:
        raise _exhausted(last_error, skipped, failed)
    raise _exhausted(_nothing_could_serve(skipped, unsayable), skipped, failed)


def _nothing_could_serve(
    skipped: list[Skipped], unsayable: list[DialectUnsupported]
) -> NoCapableModel | DialectUnsupported:
    """Why a chain with no answer and no outage had nothing to offer.

    Where **every** candidate was skipped because its wire format cannot carry the request, the
    dialect's own refusal is the answer: the request as written cannot be sent, which is a request
    to change — not a model to go looking for, which is what `NoCapableModel` tells a caller.
    """
    if unsayable and len(unsayable) == len(skipped):
        return unsayable[-1]
    return NoCapableModel(skipped)


async def _rejoined(
    first: CanonicalChunk, rest: AsyncIterator[CanonicalChunk], model: str
) -> AsyncIterator[CanonicalChunk]:
    """The chunk that proved the candidate, followed by everything after it.

    **The `finally` is load-bearing, and it was missing.** A caller that hangs up mid-answer closes
    the iterator it is reading — which is this wrapper, not the adapter's. Closing an async
    generator does not close the one it was iterating: `async for` has no such contract, so the
    upstream's response stayed open until the garbage collector reached it. Before the chain
    existed the surface iterated the adapter directly and a disconnect released it, so inserting a
    wrapper quietly took that away. It is given back here, where the wrapper knows what it wrapped.
    """
    try:
        yield first
        async for chunk in rest:
            yield chunk
    finally:
        await _closed(rest, model)


async def _nothing() -> AsyncIterator[CanonicalChunk]:
    """An upstream that answered with no chunks — an answer, and the end of the chain."""
    return
    yield  # pragma: no cover — unreachable, and what makes this an async generator


async def _closed(chunks: AsyncIterator[CanonicalChunk] | None, model: str) -> None:
    """Release the stream of a candidate that is being passed over.

    An HTTP response left open holds its connection out of the adapter's pool until the garbage
    collector gets to it, which under the outage a chain exists for is exactly the wrong moment.
    A failure to close is logged and never raised: the chain's next candidate is the answer the
    caller is waiting for.
    """
    close = getattr(chunks, "aclose", None)
    if close is None:
        return
    try:
        await close()
    except Exception:  # noqa: BLE001 — see above
        _log.warning("stream_not_closed", model=model, exc_info=True)
