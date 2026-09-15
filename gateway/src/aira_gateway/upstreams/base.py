"""The upstream contract: the `Upstream` protocol, its errors, and the model registry (`FRD-100`).

An adapter translates canonical requests to one backend. Nothing above `upstreams/` learns which
backends exist (`ADR-0011`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol, TypeIs, runtime_checkable

from aira_common.logging import get_logger
from aira_common.models import ThinkingMode
from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalEmbeddingRequest,
    CanonicalRequest,
    CanonicalResponse,
)

_log = get_logger("aira_gateway.upstreams")


# == errors =======================================================================================


class UpstreamError(Exception):
    """A failure talking to an upstream provider.

    ``status_code`` is the upstream HTTP status when the provider answered, so the surfaces can pass
    meaningful codes like 429/503 through; ``None`` for a transport failure with no response.
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class DialectUnsupported(Exception):
    """The request asks for something this wire format cannot express faithfully.

    Raised at mapping time rather than dropped. Normally unreachable — a model that cannot do a
    thing does not declare the capability, and `FRD-114` refuses before dispatch — so reaching it
    means a catalog entry claims what its dialect cannot deliver, which must not fail quietly.
    Defined here because every dialect raises it, and a dialect must not import from a sibling.
    """


class AmbiguousModel(Exception):
    """A model name that does not resolve to exactly one adapter and place.

    Raised at startup when two adapters offer one model or claim one provider: registration order
    would otherwise silently decide the region and credential, invisible in every log and report.
    Vertex also raises it for a catalogued model that names no region.
    """


def upstream_reason(response: Any) -> str:
    """The provider's stated reason for refusing, if it gave one, bounded and content-free.

    Callers apply this to a `400` alone: it names a fault in the body *we* built and is the most
    actionable thing anybody gets (`FRD-129`). A `401`/`403` is about our credentials and may name
    one; a `5xx` is the provider's internal noise. Only `error.message`, capped — the body can quote
    the request, and an upstream is not a trusted source of long strings for our error envelope and
    audit log. An unreadable body (including an unread stream) has no reason.
    """
    try:
        message = response.json().get("error", {}).get("message")
    except Exception:  # noqa: BLE001 — an unreadable or unexpected body simply has no reason
        return ""
    return f" {str(message)[:300]}" if message else ""


# == what an upstream describes ===================================================================


@dataclass(frozen=True, slots=True)
class UpstreamModel:
    """A model this gateway is wired for.

    ``provider``/``publisher``/``region`` are its **provenance** (`FRD-115` FR-10), recorded on
    every audit row and span: "the configuration says EU" is a claim, "this request went to `eu`"
    is evidence.
    """

    name: str
    version: str
    supported_methods: tuple[str, ...]
    provider: str = ""
    publisher: str = ""
    region: str = ""


@dataclass(frozen=True, slots=True)
class OfferedModel:
    """A model a **vendor** says this credential can reach (`FRD-507` stage C).

    Not an `UpstreamModel`, whose every field an operator configured: this is what the vendor
    answered, and the difference decides what a catalog import may copy.

    Every capability is **three-valued**: ``None`` means *the vendor said nothing*, which is not
    ``False``. Google returns an exhaustive method list, so a missing verb is a "no"; an
    OpenAI-compatible listing returns bare ids. Collapsing the two would turn silence into a
    declaration (`FRD-114` FR-7). There is deliberately no price: an invented price is worse than
    none (`FRD-403`).
    """

    name: str
    display_name: str = ""
    description: str = ""
    #: The vendor's own output ceiling, where it publishes one — the API refuses a larger request.
    max_output_tokens: int | None = None
    can_generate: bool | None = None
    can_embed: bool | None = None
    can_cache_prompts: bool | None = None
    #: Whether the vendor describes the model as reasoning. Shown to whoever declares the model,
    #: never a declaration: `FRD-114` needs modes and budgets, which a listing does not give.
    thinking: bool | None = None


# == the protocols ================================================================================


@runtime_checkable
class Enumerable(Protocol):
    """An adapter that can be asked what its vendor offers this credential.

    Two members, because whether a listing is usable is a property of the **platform**, not the
    dialect: the OpenAI adapter serves a plain endpoint, whose ids are model names, and Azure,
    whose listing names models nobody has deployed. One class, two answers.
    """

    #: Whether asking this instance would produce names a caller could actually use.
    enumerates: bool

    async def available_models(self) -> list[OfferedModel]: ...


def can_enumerate(upstream: object) -> TypeIs[Enumerable]:
    """Whether ``upstream`` can be asked for a model list that means something.

    One function for both callers — the provider list that offers the question and the offerings
    endpoint that answers it — so a picker never offers what the endpoint refuses (`FRD-206`). A
    ``TypeIs`` so the second caller need not restate the condition for the type checker.
    """
    return isinstance(upstream, Enumerable) and bool(upstream.enumerates)


@runtime_checkable
class Upstream(Protocol):
    """A provider AIRA can dispatch canonical requests to.

    The declarations below are per adapter and **never defaulted to "all"**: undeclared means
    unsupported, because a control silently dropped changes the answer and still returns a 200.
    `test_no_silent_drop.py` fails on an adapter that omits one.
    """

    #: Which of `SAMPLING_CONTROLS` this dialect can express (`FRD-124`).
    sampling_controls: frozenset[str]

    #: Which `ThinkingMode`s this **dialect** can express (`FRD-111` §5.2) — the *shape* (a token
    #: budget, or a word with no budget). Which modes a given model offers and what they cost is
    #: the *envelope*, and lives in the catalogue declaration. Keeping them apart makes a new model
    #: a catalogue entry and a new vendor one adapter.
    thinking_modes: frozenset[ThinkingMode]

    #: Whether this dialect has a field for a **level word** at all (`ADR-0021`). Gemini's
    #: `thinkingLevel` and `reasoning_effort` take a word; Anthropic's `budget_tokens` only a
    #: number, so a level there must be refused by name rather than dropped.
    expresses_thinking_levels: bool

    #: Whether this dialect can ask for speech (`FRD-624`): a modality and a voice on the wire.
    #: Which models speak is the catalogue's question; this is whether the wire has the words.
    speaks: bool

    def models(self) -> list[UpstreamModel]: ...

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse: ...

    def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]: ...

    async def embed(self, request: CanonicalEmbeddingRequest) -> list[list[float]]:
        """One call, one vector per submitted text, in the order submitted (`FRD-113` §5.1).

        A single text is a list of one. A second method for batches would be a second code path,
        and a batch metered on the single-text path is a rate limit with a hole in it.
        """
        ...


# == the registry =================================================================================


class ProviderRegistry:
    """Resolves model names to providers and lists available models."""

    def __init__(self, providers: list[Upstream]) -> None:
        #: Every adapter, in registration order. "Which upstreams exist" is a different question
        #: from "who serves this model", and an adapter with an empty configured list must still
        #: be visible, e.g. to the readiness probe (`FRD-507` stage C).
        self._all: list[Upstream] = list(providers)
        self._by_model: dict[str, Upstream] = {}
        self._models: dict[str, UpstreamModel] = {}
        #: Which adapter owns a **provider name**, so a catalogued model is served without also
        #: being named in configuration (`FRD-507`). Keyed by `(provider, publisher)`, publisher
        #: usually `""` meaning *any*: one platform can host two dialects (Vertex serves Google's
        #: models in the Gemini format and Anthropic's in theirs), and the catalogue's `publisher`
        #: is exactly what decides the format. The same pair twice still refuses to boot.
        self._by_provider: dict[tuple[str, str], Upstream] = {}
        for provider in providers:
            claimed = getattr(provider, "serves_provider", "")
            if claimed:
                publisher = str(getattr(provider, "serves_publisher", "") or "")
                if (claimed, publisher) in self._by_provider:
                    named = f"'{claimed}'" + (f" publisher '{publisher}'" if publisher else "")
                    raise AmbiguousModel(
                        f"Two adapters both claim provider {named}. A model catalogued under "
                        "it could be served by either, which decides its region and credential by "
                        "registration order — the same silent choice `ADR-0011` refuses."
                    )
                self._by_provider[(claimed, publisher)] = provider
            for model in provider.models():
                if model.name in self._by_model:
                    raise AmbiguousModel(
                        f"Model '{model.name}' is offered by both "
                        f"{type(self._by_model[model.name]).__name__} and "
                        f"{type(provider).__name__}. Configure it on exactly one."
                    )
                self._by_model[model.name] = provider
                self._models[model.name] = model

    def each(self) -> list[Upstream]:
        """Every registered adapter, whatever it serves and however it is addressed."""
        return list(self._all)

    async def aclose(self) -> None:
        """Close every adapter's HTTP connection pool, at application shutdown.

        Each adapter holds an `httpx.AsyncClient`; leaking them leaves sockets open after a
        redeploy. Never raises: during shutdown the useful outcome is that the *rest* of the
        teardown still happens.
        """
        for provider in self._all:
            close = getattr(provider, "aclose", None)
            if close is None:
                continue
            try:
                await close()
            except Exception as exc:  # noqa: BLE001 — see the docstring
                _log.warning(
                    "upstream_not_closed",
                    adapter=type(provider).__name__,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

    def by_name(self) -> dict[str, Upstream]:
        """Every adapter that owns a **provider name**, keyed by it (`FRD-507` stage C).

        Not derived from `models()`: an adapter whose configured list is empty (Google AI Studio's,
        typically) would otherwise be invisible, and *absent* reads as "no such thing" (`FRD-117`).
        An adapter that claims no name is not here — a caller cannot address it by name either.
        Where two dialects share a provider, either answers what this is asked.
        """
        return {provider: upstream for (provider, _), upstream in self._by_provider.items()}

    def _exact(self, provider: str, publisher: str) -> Upstream | None:
        """The adapter owning exactly ``(provider, publisher)``: one platform hosts several wire
        formats (Vertex: `google`, `anthropic`), so the provider name alone finds either."""
        return self._by_provider.get((provider, publisher)) if publisher else None

    def provenance_for(self, provider: str, publisher: str = "") -> tuple[str, str, str] | None:
        """Where an adapter that owns a provider name reaches its models (`FRD-507`).

        A catalogue-resolved model has no entry in `_models`, and a blank residency column on its
        audit row would be neither claim nor evidence (`FRD-115`). Read from one of the adapter's
        own models, else from its declared `provenance`; ``None`` rather than a guess.
        """
        exact = self._exact(provider, publisher)
        upstream = exact or self.by_name().get(provider)
        if upstream is None:
            return None
        for model in upstream.models():
            if model.provider:
                return (model.provider, model.publisher, model.region)
        declared = getattr(upstream, "provenance", None)
        if isinstance(declared, tuple):
            return declared
        # An adapter serving only catalogued models still names who it is; the region is the
        # caller's to add, from what answered or from what the catalogue lists.
        return (provider, publisher, "") if exact is not None else None

    def provider_for(self, model: str, provider: str = "", publisher: str = "") -> Upstream | None:
        """Which adapter serves this model.

        The configured name first, then the **catalogued** provider (`ModelDeclaration.provider`),
        which makes cataloguing enough to serve a model, without a restart. The exact
        `(provider, publisher)` pair is tried before a provider-wide claim, so only a platform that
        hosts two dialects (Vertex) needs the publisher.
        """
        direct = self._by_model.get(model)
        if direct is not None:
            return direct
        if not provider:
            return None
        return self._exact(provider, publisher) or self._by_provider.get((provider, ""))

    def models(self) -> list[UpstreamModel]:
        return list(self._models.values())

    def get_model(self, name: str) -> UpstreamModel | None:
        return self._models.get(name)
