"""What the gateway is allowed to do with a model (FRD-114).

Management authors the declarations; this is where they become decisions. The rule that shapes
everything here is FR-7:

    **An undeclared model gets the baseline, and nothing more.**

The tempting default is the opposite — let an undeclared model accept everything and let the
provider complain. That is wrong for the same reason "unpriced is not free" is wrong: absence of
information is not permission. An undeclared model would otherwise accept a 32 768-token thinking
budget, which the pre-dispatch reservation would then have to estimate against nothing.

So every refusal here names the missing declaration. The fix is a catalog edit, and saying so is
the difference between a support ticket and a two-minute correction.
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

_log = structlog.get_logger(__name__)


class AmbiguousModelId(Exception):
    """Two catalog entries claim the same KIRA integer id.

    A configuration fault, not a caller's mistake — which is why it is raised rather than resolved.
    Choosing one would answer, bill and audit under a model the caller never named, and nothing in
    the response would look wrong.
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
    #: Whether the catalog holds a row for this model at all — distinct from :attr:`declared`,
    #: which means somebody also wrote down what it can do. A model can be catalogued and priced
    #: without a capability list; only the first of those is what `FRD-307` requires.
    in_catalog: bool = False
    #: Whether a Global Administrator has released it (`FRD-307`). Undeclared models are not
    #: gated by this — see :class:`ModelApproved` for why.
    approved: bool = True
    capabilities: frozenset[Capability] = BASELINE_CAPABILITIES
    #: What the model can hold at once. Carried so the model list can publish it; nothing on
    #: the request path reads it, because the upstream is the authority on what fits.
    context_window: int | None = None
    max_output_tokens: int | None = None
    default_max_output_tokens: int | None = None
    thinking: dict[str, Any] | None = None
    embedding: dict[str, Any] | None = None
    attachments: dict[str, Any] = field(default_factory=dict)
    #: Which adapter serves it (`FRD-507`). The catalog is already the authority on *what may be
    #: served*; carrying the provider makes it the authority on *who serves it* too, so a model
    #: becomes usable by being catalogued rather than by also being named in configuration.
    provider: str = ""
    #: How to reach this model on its platform: `{"regions": ["europe-west1", "europe-west4"]}` on
    #: Vertex, a deployment on Azure. **A column that existed in both planes, travelled over Kafka,
    #: and nothing read** — which is why a Vertex model could be catalogued and would never answer,
    #: and the console had to say so at the moment of declaring. Read now, so it can.
    #:
    #: Values stay `str | list[str]` rather than `str`, because a region list is a list.
    addressing: dict[str, Any] = field(default_factory=dict)

    @property
    def regions(self) -> tuple[str, ...]:
        """Where this model may be addressed, **in the order it should be tried** (`FRD-609`).

        One reader for two spellings, and only one of them is current. `{"region": "x"}` was the
        shape until a model could name several; rows written before that still carry it, and a
        redelivered Kafka event can carry it after a rollback. Normalising here rather than
        migrating in five readers is the same argument `thinking_levels` makes one field along:
        the shape is read in one place, so a second spelling cannot mean two different things in
        two of them.

        Order is meaning, not presentation: the first region a request may use is the first one
        this installation's residency policy permits, and a failure falls through to the next
        (`vertex/adapters.py`). Duplicates are dropped and blanks ignored, because a list that
        names the same place twice would retry the failure it just had.
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

    publisher: str = ""
    platform: str = ""
    hosting: str = ""
    deprecated: bool = False
    #: The KIRA-style integer alias, when one is assigned (`FRD-114` FR-6, `FRD-107` FR-4).
    numeric_id: int | None = None

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

        An image or a PDF costs hundreds to thousands of input tokens that no property of the
        request body predicts. Without this the pre-dispatch reservation would treat a request
        carrying a 20 000-token document as a sentence — reopening under documents exactly the
        race `FRD-405` closed for text, where N concurrent requests all pass a limit with room
        for one.

        Wrong **high** by design, and corrected by `settle` the moment the real usage arrives.
        What must not happen is a silent zero: that is the "unknown is not zero" rule, and a
        reservation that ignores the expensive half of a request is not a limit.
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
    # Read off the declaration rather than parsed into a dataclass at construction: the block is
    # authored in Management and validated *there* (`FRD-114` FR-3), so a second parser here would
    # be a second opinion about the same JSON — and the two would drift in whichever plane was not
    # under test.

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

        Not the provider's default and not *none*: the predecessor applies a per-model default,
        and a gateway that quietly sent no thinking where the predecessor sent some would answer
        differently for a reason nobody could see.
        """
        default = (self.thinking or {}).get("default")
        return default if isinstance(default, dict) else None

    @property
    def thinking_levels(self) -> tuple[str, ...]:
        """The **vendor's own** level words this model accepts, in the order they were declared.

        Free text, and that is the point (`ADR-0021`): the vendors converged on words after
        starting with numbers — Gemini 3 takes ``thinkingLevel``, OpenAI ``reasoning_effort`` —
        and they do not agree on the set. A closed enum here would make a vendor's next word a code
        change; a list typed into the catalog and **checked against the model** makes it a Tuesday.

        This replaced a ``{level: token count}`` table. The table asked whoever catalogued the
        model for a number no vendor publishes, and a wrong guess was not merely unfounded: a
        hand-typed ``medium = 2000`` silently truncates an agentic run that needed twenty thousand
        thinking tokens. Nothing is derived here now — a word is sent, or it is not offered.
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

        Not merely convenience — Anthropic **requires** ``max_tokens`` on every request
        (`FRD-119` §5.3), so a caller who omits it would otherwise receive a vendor error about a
        field they never set. It also sharpens the pre-dispatch reservation for every vendor.
        """
        return requested if requested is not None else self.default_max_output_tokens


#: The widest model name the catalog can hold — `model_catalog.model` and `request_logs.model` are
#: both `String(128)`. A name longer than this cannot name a declared model, by construction.
MAX_MODEL_NAME = 128


def is_lookupable(model: str) -> bool:
    """Whether ``model`` is a name this catalog could possibly hold.

    **A caller-supplied string reaching a database is the whole point.** The model name arrives in
    a URL path segment and is used as a primary key, and two shapes of it were found to reach
    Postgres and fail there — neither visible to the hermetic suite, because SQLite accepts both:

    - a **NUL byte** (`mock-1%00:generateContent`) raises `psycopg.DataError` and the caller gets
      a **500**, breaking this project's own rule that a caller's mistake is answered with an
      actionable status and never with our error;
    - a name of **300 characters** exceeds `String(128)`, and the row that records the refusal
      then fails to write — so an oversized name is a request the audit trail does not have
      (`FRD-122`), which is worse than the wrong status code.

    Refused *before* the query rather than caught after it: a lookup that cannot match anything is
    not worth a round trip, and catching a database error would make the answer depend on which
    database is behind it. The same reasoning as `is_valid_use_case`, one identifier over.

    Control characters generally, not just NUL: they cannot appear in a declared model name, and
    every one of them is a value that behaves differently in a log line, a URL and a database.
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

        Every reader here opens its own session, and one request asks the same question five times:
        the pipeline's `declaration_of`, the routed model's provider, `check_declaration`, the
        reservation's `estimate`, `provenance`, and once per candidate inside `requirements_for`.
        Measured on 2026-08-15 against the hermetic app: **15 sessions for one served request**, of
        which five were `declaration()` for the same model — each a connection checked out of the
        pool for a row that had already been read.

        A cache with a **request's** lifetime rather than the app's, deliberately. The catalog is a
        runtime authority: what it says decides whether a request is accepted, and configuration
        arrives over Kafka at any moment. An app-scoped cache would mean a model stayed approved
        after a Global Administrator revoked it, for as long as the entry lived — which is the
        opposite of what `FRD-307` is for. Within one request the answer must not change anyway:
        the pre-dispatch checks and the dispatch that follows are supposed to be deciding about the
        same declaration, and re-reading was how they could quietly disagree.
        """
        return _MemoisedCatalog(self)

    async def declaration(self, model: str) -> ModelDeclaration:
        if not is_lookupable(model):
            # Undeclared, which is what it is: no such row can exist. The caller then meets the
            # ordinary `model_not_found` 404 instead of a 500, and nothing about *which* database
            # is running decides the answer.
            return ModelDeclaration(name=model)
        async with self._sessionmaker() as session:
            record = await session.get(ModelRead, model)
        if record is None:
            return ModelDeclaration(name=model)
        return _from_record(model, record)

    async def by_numeric_id(self, numeric_id: int) -> str | None:
        """The model a KIRA-style integer id refers to (`FRD-114` FR-6).

        Exists only for `FRD-107`. If `ADR-0010` is ever revisited toward moving the clients
        instead, this and the column go together — an unused numeric alias left in a catalog reads
        as though it meant something.
        """
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(ModelRead.model).where(ModelRead.numeric_id == numeric_id)
            )
            names: list[str] = [str(row[0]) for row in result.all()]
        if not names:
            return None
        if len(names) > 1:
            # An **ambiguous** id, which is the routing-table problem of `ADR-0011` in the catalog:
            # picking one would silently send a caller's traffic to whichever row was read first,
            # and bill it accordingly. Management enforces uniqueness where the declaration is
            # written; this is the read-model's side of the same rule, and it was reached — a seed
            # run for a second local model reused an id, and `scalar_one_or_none()` answered the
            # KIRA surface with an unhandled 500 (2026-08-08).
            _log.error(
                "ambiguous_numeric_model_id",
                numeric_id=numeric_id,
                models=sorted(names),
            )
            raise AmbiguousModelId(numeric_id, sorted(names))
        return names[0]

    async def exceeds_output_cap(self, model: str, requested: int | None) -> int | None:
        """The model's cap if ``requested`` is above it, else ``None``.

        Refused here rather than passed on for the provider to reject differently: the same
        mistake would otherwise produce a different error per vendor, and a caller cannot write
        against that.
        """
        if requested is None:
            return None
        declaration = await self.declaration(model)
        cap = declaration.max_output_tokens
        return cap if cap is not None and requested > cap else None


class _MemoisedCatalog(ModelCatalog):
    """One request's view: the same model is read from the database once.

    A subclass so that everything typed against `ModelCatalog` — the requirements, the dispatch
    resolver, both surfaces — is handed one without knowing. `by_numeric_id` is **not** memoised:
    it happens once per KIRA request by construction, and caching a lookup that raises on an
    ambiguous id would cache the raise as well.
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
        # **Not stringified.** This used to coerce every value with `str()`, which turned a
        # `regions` list into the literal `"['europe-west1']"` — a region name nothing could match
        # and a residency claim nothing could read. Values are carried as they arrive and shaped by
        # the one reader that knows what each key means (`ModelDeclaration.regions`).
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
