"""Upstream provider protocol and a small model registry (FRD-100).

Providers translate canonical requests to a concrete backend. In Phase 1 the only provider
is the deterministic mock; real adapters (Gemini Enterprise, Microsoft Foundry) arrive in
Phase 3 (FRD-304) implementing the same protocol.
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


def upstream_reason(response: Any) -> str:
    """The provider's stated reason for refusing, if it gave one, bounded and content-free.

    **One owner, because two adapters answered this differently.** The OpenAI dialect carried the
    reason for a `400` and the Vertex one did not — its comment reasoning that *"a Vertex error can
    quote the request"*, which is true of the response **body** and not of `error.message`. The
    difference cost a diagnosis on 2026-08-19: fourteen media types were confirmed against a real
    Gemini model and the fifteenth answered `Vertex upstream returned 400.`, while Vertex itself had
    said

        Unable to submit request because it has a mimeType parameter with value
        application/x-javascript, which is not supported.

    — the whole answer, discarded one layer down. `FRD-129`'s rule is that a `400` names a fault in
    the body *we* built and is the most actionable thing anybody gets.

    Only the `message` field, capped: an upstream is not a trusted source of arbitrarily long
    strings for our error envelope and audit log. Callers apply this to `400` alone — a `401`/`403`
    is about our credentials and may name one, and a `5xx` is the provider's internal noise.
    """
    try:
        message = response.json().get("error", {}).get("message")
    except Exception:  # noqa: BLE001 — an unreadable or unexpected body simply has no reason
        return ""
    return f" {str(message)[:300]}" if message else ""


class UpstreamError(Exception):
    """A failure talking to an upstream provider.

    ``status_code`` is the upstream HTTP status when the provider answered (used by the
    routes to pass meaningful codes like 429/503 through to the client); it is ``None`` for
    transport-level failures where no response was received.
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class UpstreamModel:
    """Metadata describing a model an upstream exposes.

    ``provider``/``publisher``/``region`` are the **provenance** (FRD-115 FR-10): under a residency
    requirement, "the configuration says EU" is a claim and "this request went to `eu`" is
    evidence. They are recorded on every audit row and span, so `FRD-601` can break down by them.
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

    Not an `UpstreamModel`: that one describes a model this gateway is *wired for*, and every
    field of it is something an operator configured. This one describes what the vendor answered
    when asked, and the difference decides what a catalog import may copy.

    Every capability here is **three-valued on purpose**. ``None`` means *the vendor said nothing*,
    which is not the same answer as ``False`` — Google returns an exhaustive method list, so a verb
    missing from it really is a "no"; an OpenAI-compatible listing returns bare ids and says
    nothing at all. Collapsing the two would turn silence into a declaration, which is `FRD-114`
    FR-7's mistake in a dataclass field, and the console would pre-fill an unticked box that reads
    as a decision somebody made.

    What is deliberately **absent**: a price. No listing publishes one in a usable form, and an
    invented price is worse than none (`FRD-403`).
    """

    name: str
    display_name: str = ""
    description: str = ""
    #: The vendor's own output ceiling, where it publishes one. An interface fact: the API refuses
    #: a larger request, so this is measured rather than claimed.
    max_output_tokens: int | None = None
    can_generate: bool | None = None
    can_embed: bool | None = None
    can_cache_prompts: bool | None = None
    #: Whether the vendor describes the model as reasoning. Information for whoever declares it,
    #: never a declaration: `FRD-114` needs modes and budgets, and `FRD-132` measured two models of
    #: one family answering differently. A catalog import shows this and fills in nothing.
    thinking: bool | None = None


@runtime_checkable
class Enumerable(Protocol):
    """An adapter that can be asked what its vendor offers this credential.

    Two members rather than one, and the second is the point. Whether a listing exists is a
    property of the **platform**, not of the dialect: the OpenAI adapter serves both a plain
    endpoint — whose ids *are* model names — and Azure, whose listing names models that cannot be
    reached until somebody creates a deployment for them. One class, two answers, so the answer
    cannot be an ``isinstance`` check alone.
    """

    #: Whether asking this instance would produce names a caller could actually use.
    enumerates: bool

    async def available_models(self) -> list[OfferedModel]: ...


def can_enumerate(upstream: object) -> TypeIs[Enumerable]:
    """Whether ``upstream`` can be asked for a model list that means something.

    One function because two callers need the same answer — the provider list, which *offers* the
    question, and the offerings endpoint, which *answers* it. A picker that offered a provider the
    endpoint then refuses is `FRD-206`'s complaint in miniature: an action nobody can carry out.

    A ``TypeIs`` rather than a ``bool`` so the second caller does not have to restate the condition
    to satisfy the type checker — a restated rule is the shape of defect this project has recorded
    under `FRD-126`, `FRD-206` and `FRD-602`.
    """
    return isinstance(upstream, Enumerable) and bool(upstream.enumerates)


@runtime_checkable
class Upstream(Protocol):
    """A provider AIRA can dispatch canonical requests to."""

    #: Which of `SAMPLING_CONTROLS` this provider's dialect can express (`FRD-124`).
    #:
    #: Declared per adapter and **never defaulted to "all"**. The same rule the catalog follows —
    #: undeclared means unsupported — for the same reason: a control silently dropped changes the
    #: answer and returns a 200. `test_every_adapter_declares_its_sampling_support` makes the
    #: omission a test failure rather than a quiet permission.
    sampling_controls: frozenset[str]

    #: Which `ThinkingMode`s this provider's **dialect** can express (`FRD-111` §5.2).
    #:
    #: The second axis of vendor variation, declared the same way as the first and for the same
    #: reason. There are two axes and they are different things:
    #:
    #: - the **shape**: Gemini and Anthropic take a token budget, the OpenAI dialect takes a word
    #:   and has no budget at all. That is a property of the dialect, and it lives here.
    #: - the **envelope**: which modes a given model offers and what each level costs. That is a
    #:   property of the model, and it lives in the catalogue declaration.
    #:
    #: Keeping them apart is what lets a new *model* be a catalogue entry and a new *vendor* be one
    #: adapter. What was missing is this line: a dialect's limits were scattered `if` branches, so
    #: `limited` was refused by one mapper's exception and `auto` was **silently omitted** by
    #: another — a caller asking Anthropic for `auto` with no resolved budget received a body
    #: identical to `disabled`, answered `200`, and was told nothing.
    #:
    #: Never defaulted to "all", exactly as above: undeclared means unsupported, and
    #: `test_every_adapter_declares_its_thinking_support` makes the omission a failure at the point
    #: somebody can still choose the right answer.
    thinking_modes: frozenset[ThinkingMode]

    #: Whether this dialect has a field for a **level word** at all (`ADR-0021`).
    #:
    #: The second half of the same declaration, and it became a separate question when levels
    #: stopped being members of :class:`ThinkingMode`: `reasoning_effort` and Gemini's
    #: `thinkingLevel` both take a word, and Anthropic's `budget_tokens` takes only a number, so
    #: there is no field on that dialect for `low` to go into and it must be refused by name.
    #:
    #: Declared rather than inferred from the mapper, for the reason every declaration here exists:
    #: the failure it prevents is a mapper that quietly drops what it cannot express, which is
    #: exactly what the Anthropic one did before `FRD-124`.
    expresses_thinking_levels: bool

    def models(self) -> list[UpstreamModel]: ...

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse: ...

    def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]: ...

    async def embed(self, request: CanonicalEmbeddingRequest) -> list[list[float]]: ...

    """One call, one vector per submitted text, in the order submitted (`FRD-113` §5.1).

    A single text is a list of one. Adding a second method for batches instead would mean two code
    paths, and a batch metered on the single-text path is a rate limit with a hole in it — so the
    breaking change to this protocol was taken once, deliberately, rather than avoided.
    """


class AmbiguousModel(Exception):
    """Two providers offer the same model name.

    Raised at startup. With one adapter the old behaviour — last registration wins — was harmless;
    with three (Generative Language, Vertex Gemini, Vertex Anthropic) it becomes a **silent**
    decision about which region and which credential handled a request, and the wrong answer is
    invisible in every log and every report. Failing to boot is the correct response to an
    ambiguous routing table; a running gateway that sometimes leaves the EU is not.
    """


class ProviderRegistry:
    """Resolves model names to providers and lists available models."""

    def __init__(self, providers: list[Upstream]) -> None:
        #: Every adapter, in registration order. Kept because "which upstreams does this
        #: installation have" is a different question from "who serves this model name", and
        #: answering the first by walking the second is what left an adapter with an empty
        #: configured list invisible to the readiness probe (`FRD-507` stage C).
        self._all: list[Upstream] = list(providers)
        self._by_model: dict[str, Upstream] = {}
        self._models: dict[str, UpstreamModel] = {}
        #: Which adapter owns a **provider name**, so a model the catalog knows can be served
        #: without having been named in configuration as well (`FRD-507`).
        #:
        #: The configured list was the only way in, and it made the catalog a second place to type
        #: the same name: you declared a model in `AIRA_GEMINI_MODELS` so the adapter would offer
        #: it, then declared it again in the catalog so `FRD-307` would permit it. The catalog is
        #: already the authority on *what may be served* — a model in it names its provider, and
        #: that is enough to know who serves it.
        #:
        #: Configured models keep working unchanged; this is the fallback for one the catalog knows
        #: and the configuration does not.
        # Keyed by `(provider, publisher)`, and the publisher is usually `""` meaning *any*.
        #
        # **Because one platform can host two dialects.** Vertex serves Google's models in the
        # Gemini wire format and Anthropic's in theirs, so `vertex` alone identifies neither and
        # the two adapters could not both claim it — which is why cataloguing a Vertex model
        # produced an entry that would never answer, and the console had to say so. The catalogue
        # already carries the discriminator: `publisher` is `google` or `anthropic`, and that is
        # exactly the thing that decides the format.
        #
        # A genuine collision — the same provider *and* the same publisher twice — still refuses,
        # for the reason it always did: registration order would silently pick the region and the
        # credential.
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
        """Close every adapter's HTTP connection pool.

        `create_app` builds one `httpx.AsyncClient` per configured upstream — Vertex, Google AI
        Studio, Foundry and one per OpenAI-dialect server — and the lifespan closed the database
        engine, the counter store, the writer and the probe, and none of these. Each holds a
        connection pool and, under TLS, its own SSL context; leaking them means a redeploy leaves
        sockets open until the process dies, and the hermetic suite, which builds an application
        per test, accumulates them by the hundred.

        Found by asking what `create_app` opens and `lifespan` closes, and listing the difference.

        Never raises. This runs during shutdown, where the useful outcome is that the *rest* of the
        teardown still happens — the same reasoning `RequestLogWriter._write_remaining` records.
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

        Not derived from `models()`, and that is the whole reason this exists. Since cataloguing a
        model became enough to serve it (stage B), a working deployment can have an adapter with an
        **empty** configured list — Google AI Studio's is exactly that — and every consumer that
        walked the model list therefore could not see it at all. The readiness probe was the first
        casualty: it reported nothing whatsoever about that upstream, and *absent* reads as "no such
        thing", which is the wrong half of `FRD-117`'s distinction between "we did not look" and
        "it is fine".

        An adapter that claims no provider name is not here, deliberately: a caller cannot address
        it by name either, so there is nothing for a console to offer.
        """
        # Flattened to the provider name, because that is what a console offers and what a
        # readiness probe reports. Where two dialects share a provider, either answers the question
        # this is asked — *"is cataloguing enough here"* — and the answer is the same for both.
        return {provider: upstream for (provider, _), upstream in self._by_provider.items()}

    def provenance_for(self, provider: str) -> tuple[str, str, str] | None:
        """Where an adapter that owns a provider name reaches its models (`FRD-507`).

        A model resolved through the **catalog** has no entry in `_models`, so the provenance the
        audit row needs cannot be read from there — and leaving it blank would be worse than the
        second list this feature removes: `FRD-115`'s point is that "the configuration says EU" is
        a claim and "this request went to `eu`" is evidence, and an empty column is neither.

        Read from one of the adapter's own models, because that is where the adapter put it. An
        adapter that owns a namespace and serves no configured model has nothing to answer with,
        and says so rather than guessing.
        """
        upstream = self.by_name().get(provider)
        if upstream is None:
            return None
        for model in upstream.models():
            if model.provider:
                return (model.provider, model.publisher, model.region)
        declared = getattr(upstream, "provenance", None)
        return declared if isinstance(declared, tuple) else None

    def provider_for(self, model: str, provider: str = "", publisher: str = "") -> Upstream | None:
        """Which adapter serves this model.

        The configured name first, and a **catalogued** model's provider second. `provider` is what
        the catalog says (`ModelDeclaration.provider`); passing it is what lets a model become
        servable by being catalogued, without a second entry in configuration and without a
        restart. Callers that have no declaration to hand pass nothing and get the old behaviour.

        `publisher` is the second half, and it exists because one platform can host two wire
        formats: on Vertex, `google` is the Gemini dialect and `anthropic` is Anthropic's. The
        exact pair is tried first and a provider-wide claim second, so an adapter that owns a whole
        provider — every one but Vertex today — is unaffected.
        """
        direct = self._by_model.get(model)
        if direct is not None:
            return direct
        if not provider:
            return None
        exact = self._by_provider.get((provider, publisher)) if publisher else None
        return exact or self._by_provider.get((provider, ""))

    def models(self) -> list[UpstreamModel]:
        return list(self._models.values())

    def get_model(self, name: str) -> UpstreamModel | None:
        return self._models.get(name)


class DialectUnsupported(Exception):
    """The request asks for something this wire format cannot express faithfully.

    Raised at mapping time rather than dropped. It should be unreachable in practice — a model that
    cannot do a thing does not declare the capability, and `FRD-114` refuses the request before
    dispatch — so reaching it means a catalog entry claims something its dialect cannot deliver,
    which is exactly the state that must not fail quietly.

    **Lives here rather than in a dialect** (moved 2026-08-08). It was defined in the OpenAI
    mapping, which was fine while only that dialect raised it — and the moment `FRD-131` widened
    the part union, the Gemini and Anthropic adapters needed it too and would have imported it
    *from a sibling dialect*. That is the import the architecture assertion caught once before,
    with `to_json_schema`, and the answer is the same: a thing every dialect needs was never one
    dialect's to own.
    """
