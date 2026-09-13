"""What the gateway is allowed to do with a model (FRD-114).

Management authors the declarations; this is where they become decisions. The rule that shapes
everything here is FR-7: **an undeclared model gets the baseline, and nothing more** — absence of
information is not permission. Every refusal names the missing declaration, because the fix is a
catalog edit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_common.models import (
    BASELINE_CAPABILITIES,
    Capability,
    Hosting,
    ThinkingMode,
    parse_capabilities,
)
from aira_gateway.db.models import ModelRead

#: The widest model name the catalog can hold — `model_catalog.model` and `request_logs.model` are
#: both `String(128)`. A longer name cannot name a declared model, by construction.
MAX_MODEL_NAME = 128

#: The largest token figure this gateway can account for, and so the largest a caller may ask for.
#:
#: Derived, not chosen: `budget_usage.tokens` and `budgets.limit_tokens` are `Integer`. A larger
#: figure would overflow the reservation's 64-bit `HINCRBY`, which reads as the counter store being
#: away and switches budget enforcement to its racy fallback (`FRD-405` §4.2). Both caller-named
#: figures — `maxOutputTokens` and a `limited` thinking budget — are checked against it
#: unconditionally, because the model's own bounds are nullable.
MAX_ACCOUNTABLE_TOKENS = 2**31 - 1

_log = structlog.get_logger(__name__)


class AmbiguousModelId(Exception):
    """Two catalog entries claim the same KIRA integer id.

    A configuration fault, raised rather than resolved: choosing one would answer, bill and audit
    under a model the caller never named, with nothing in the response looking wrong.
    """

    def __init__(self, numeric_id: int, models: list[str]) -> None:
        self.numeric_id = numeric_id
        self.models = models
        super().__init__(f"Model id {numeric_id} is claimed by {', '.join(models)}.")


@dataclass(frozen=True, slots=True)
class ModelDeclaration:
    """What the catalog says about one model. Absent from the catalog is a declaration too — an
    undeclared one, which is why this is never ``None`` and why ``declared`` exists."""

    name: str
    declared: bool = False
    #: Whether the catalog holds a row at all — distinct from :attr:`declared`, which means somebody
    #: also wrote down what it can do. Only this one is what `FRD-307` requires.
    in_catalog: bool = False
    #: Whether a Global Administrator has released it (`FRD-307`; see :class:`ModelApproved`).
    approved: bool = True
    capabilities: frozenset[Capability] = BASELINE_CAPABILITIES
    #: Published on the model list; nothing on the request path reads it — the upstream decides.
    context_window: int | None = None
    max_output_tokens: int | None = None
    default_max_output_tokens: int | None = None
    thinking: dict[str, Any] | None = None
    embedding: dict[str, Any] | None = None
    attachments: dict[str, Any] = field(default_factory=dict)
    #: Which adapter serves it (`FRD-507`), so a model becomes usable by being catalogued.
    provider: str = ""
    #: How to reach it on its platform: `{"regions": [...]}` on Vertex, a deployment on Azure.
    #: Values stay `str | list[str]`, because a region list is a list; read through :attr:`regions`.
    addressing: dict[str, Any] = field(default_factory=dict)
    publisher: str = ""
    platform: str = ""
    hosting: str = ""
    deprecated: bool = False
    #: The KIRA-style integer alias, when one is assigned (`FRD-114` FR-6, `FRD-107` FR-4).
    numeric_id: int | None = None

    @property
    def regions(self) -> tuple[str, ...]:
        """Where this model may be addressed, **in the order it should be tried** (`FRD-609`).

        The one reader for both spellings — `{"region": "x"}` from older rows and redelivered
        events, `{"regions": [...]}` now. Order is meaning: the first permitted region is tried
        first and a failure falls through to the next (`vertex/adapters.py`). Duplicates are dropped
        so a failure is not retried in the same place.
        """
        block = self.addressing or {}
        raw = block.get("regions")
        if raw is None:
            single = block.get("region")
            raw = [single] if isinstance(single, str) else []
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            return ()
        seen: dict[str, None] = {}
        for region in raw:
            if isinstance(region, str) and region.strip():
                seen.setdefault(region.strip(), None)
        return tuple(seen)

    def can(self, capability: Capability) -> bool:
        return capability in self.capabilities

    @property
    def is_self_deployed(self) -> bool:
        """Cold starts of minutes and capacity-shaped 429s (`ADR-0012` §5) — the dispatch timeout,
        the retry decision and the readiness probe read this."""
        return self.hosting == Hosting.SELF_DEPLOYED

    @property
    def media_types(self) -> frozenset[str]:
        """Attachment media types **this model** accepts. `FRD-110` intersects its own allow-list
        with this one; an undeclared model accepts none, which is FR-7 again."""
        types = (self.attachments or {}).get("media_types")
        return frozenset(types) if isinstance(types, dict) else frozenset()

    def attachment_tokens(self, media_types: list[str]) -> int:
        """What the declared attachments are expected to cost in **input** tokens.

        Nothing in the request body predicts what a document costs, and a reservation that ignored
        it would reopen for documents the race `FRD-405` closed for text. Wrong **high** by design,
        corrected by `settle`; never a silent zero.
        """
        declared = (self.attachments or {}).get("media_types")
        if not isinstance(declared, dict):
            return 0
        total = 0
        for media_type in media_types:
            spec = declared.get(media_type)
            if isinstance(spec, dict):
                total += int(spec.get("tokens", 0) or 0)
        return total

    # -- thinking (FRD-111) ---------------------------------------------------------------
    #
    # Read off the declaration rather than parsed at construction: the block is validated in
    # Management (`FRD-114` FR-3), and a second parser here would drift from it.

    @property
    def thinking_modes(self) -> frozenset[ThinkingMode]:
        """Which of the gateway's three control settings this model offers. Levels are separate."""
        modes = (self.thinking or {}).get("modes")
        if not isinstance(modes, list):
            return frozenset()
        known = {member.value for member in ThinkingMode}
        return frozenset(ThinkingMode(mode) for mode in modes if mode in known)

    @property
    def offers_thinking(self) -> bool:
        """Whether this model offers *any* thinking setting — a control mode or a level word."""
        return bool(self.thinking_modes or self.thinking_levels)

    @property
    def thinking_bounds(self) -> tuple[int | None, int | None]:
        """``(min_tokens, max_tokens)`` for a ``limited`` budget."""
        block = self.thinking or {}
        minimum = block.get("min_tokens")
        maximum = block.get("max_tokens")
        return (
            minimum if isinstance(minimum, int) and not isinstance(minimum, bool) else None,
            maximum if isinstance(maximum, int) and not isinstance(maximum, bool) else None,
        )

    @property
    def thinking_default(self) -> dict[str, Any] | None:
        """What the model does when the caller says nothing (`FRD-111` FR-4).

        The per-model default, as the predecessor applies it — not the provider's, and not none.
        """
        default = (self.thinking or {}).get("default")
        return default if isinstance(default, dict) else None

    @property
    def thinking_levels(self) -> tuple[str, ...]:
        """The **vendor's own** level words this model accepts, in declared order (`ADR-0021`).

        Free text checked against the model, so a vendor's next word is a catalog edit rather than
        a code change. No token count is derived from a level: a guessed figure would silently
        truncate the model's reasoning.
        """
        levels = (self.thinking or {}).get("levels")
        if not isinstance(levels, list):
            return ()
        seen: dict[str, None] = {}
        for level in levels:
            if isinstance(level, str) and level.strip():
                seen.setdefault(level.strip().lower(), None)
        return tuple(seen)

    # -- embedding (FRD-113) --------------------------------------------------------------

    @property
    def embedding_task_types(self) -> frozenset[str]:
        types = (self.embedding or {}).get("task_types")
        return frozenset(str(value) for value in types) if isinstance(types, list) else frozenset()

    @property
    def supports_batch(self) -> bool:
        return bool((self.embedding or {}).get("supports_batch"))

    @property
    def embedding_dimensions(self) -> frozenset[int]:
        values = (self.embedding or {}).get("dimensions")
        if not isinstance(values, list):
            return frozenset()
        return frozenset(v for v in values if isinstance(v, int) and not isinstance(v, bool))

    @property
    def default_dimensions(self) -> int | None:
        value = (self.embedding or {}).get("default")
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def output_cap(self, requested: int | None) -> int | None:
        """The output token cap to send upstream: the caller's, else the model's default.

        Anthropic **requires** ``max_tokens`` (`FRD-119` §5.3), so a caller who omits it would
        otherwise get a vendor error about a field they never set.
        """
        return requested if requested is not None else self.default_max_output_tokens


def is_lookupable(model: str) -> bool:
    """Whether ``model`` is a name this catalog could possibly hold.

    The name arrives in a URL path segment and is used as a primary key. A NUL byte makes Postgres
    raise (a 500 for a caller's mistake), and an over-long name makes the refusal's own audit row
    fail to write (`FRD-122`) — neither visible under SQLite. Refused before the query, so the
    answer never depends on which database is behind it. Control characters generally, because
    none can appear in a declared name.
    """
    if not model or len(model) > MAX_MODEL_NAME:
        return False
    return not any(ord(character) < 0x20 or ord(character) == 0x7F for character in model)


class ModelCatalog:
    """Reads model declarations from the read-model. Never calls Management (FR-8)."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    def per_request(self) -> ModelCatalog:
        """A view of this catalog that answers each model **once**, for the life of one request.

        One request asks the same question several times (pipeline, routing, declaration check,
        reservation, provenance, each candidate). A request's lifetime, never the app's: the catalog
        is a runtime authority, and an app-scoped cache would keep a revoked model approved
        (`FRD-307`). Within a request the answer must not change, so every check decides about the
        same declaration.
        """
        return _MemoisedCatalog(self)

    async def declaration(self, model: str) -> ModelDeclaration:
        if not is_lookupable(model):
            # Undeclared, which is what it is: the caller meets the ordinary `model_not_found`.
            return ModelDeclaration(name=model)
        async with self._sessionmaker() as session:
            record = await session.get(ModelRead, model)
        if record is None:
            return ModelDeclaration(name=model)
        return _from_record(model, record)

    async def by_numeric_id(self, numeric_id: int) -> str | None:
        """The model a KIRA-style integer id refers to (`FRD-114` FR-6).

        Exists only for `FRD-107`; if `ADR-0010` is revisited, this and the column go together.
        """
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(ModelRead.model).where(ModelRead.numeric_id == numeric_id)
            )
            names: list[str] = [str(row[0]) for row in result.all()]
        if not names:
            return None
        if len(names) > 1:
            # Management enforces uniqueness where the declaration is written; this is the
            # read-model's side of the same rule. Picking one would route and bill silently.
            _log.error(
                "ambiguous_numeric_model_id",
                numeric_id=numeric_id,
                models=sorted(names),
            )
            raise AmbiguousModelId(numeric_id, sorted(names))
        return names[0]

    async def exceeds_output_cap(self, model: str, requested: int | None) -> int | None:
        """The model's cap if ``requested`` is above it, else ``None``.

        Refused here rather than by the provider, so the same mistake gets the same error on every
        vendor.
        """
        if requested is None:
            return None
        declaration = await self.declaration(model)
        cap = declaration.max_output_tokens
        return cap if cap is not None and requested > cap else None


class _MemoisedCatalog(ModelCatalog):
    """One request's view: the same model is read from the database once.

    A subclass, so everything typed against `ModelCatalog` takes one without knowing.
    `by_numeric_id` is **not** memoised: it runs once per KIRA request, and caching a lookup that
    raises on an ambiguous id would cache the raise.
    """

    def __init__(self, source: ModelCatalog) -> None:
        self._source = source
        self._seen: dict[str, ModelDeclaration] = {}

    async def declaration(self, model: str) -> ModelDeclaration:
        cached = self._seen.get(model)
        if cached is None:
            cached = await self._source.declaration(model)
            self._seen[model] = cached
        return cached

    async def by_numeric_id(self, numeric_id: int) -> str | None:
        return await self._source.by_numeric_id(numeric_id)


def _from_record(model: str, record: ModelRead) -> ModelDeclaration:
    capabilities = parse_capabilities(record.capabilities)
    declared = bool(capabilities)
    return ModelDeclaration(
        name=model,
        declared=declared,
        in_catalog=True,
        approved=bool(record.approved),
        # A row with prices but no capability list is *undeclared*, so it gets the baseline —
        # not an empty set, which would refuse the generation that already works today.
        capabilities=capabilities if declared else BASELINE_CAPABILITIES,
        context_window=record.context_window,
        max_output_tokens=record.max_output_tokens,
        default_max_output_tokens=record.default_max_output_tokens,
        thinking=record.thinking if isinstance(record.thinking, dict) else None,
        embedding=record.embedding if isinstance(record.embedding, dict) else None,
        attachments=record.attachments if isinstance(record.attachments, dict) else {},
        provider=record.provider or "",
        # Values carried as they arrive, not stringified (a `regions` list would become a string
        # nothing matches); `ModelDeclaration.regions` shapes them.
        addressing=(
            {str(key): value for key, value in record.addressing.items()}
            if isinstance(record.addressing, dict)
            else {}
        ),
        publisher=record.publisher or "",
        platform=record.platform or "",
        hosting=record.hosting or "",
        deprecated=bool(record.deprecated),
        numeric_id=record.numeric_id,
    )
