"""The register of processing activities (`FRD-608`).

The owner's question was *"there is still no overview for IT Steuerung where they can list use
cases, the description in them, the models used, all the controls like how many days data is
stored, and generally how the data processing happens."* Read against the code, two thirds of it
already existed: IT Steuerung already sees every use case, and every field it needs is already
stored. What was missing was the **shape**.

That distinction is the whole design. Governance is a **comparison** activity — *which use cases
store prompts? which keep them longer than thirty days? which were processed outside the EU?* —
and none of those is answered by opening forty detail pages one at a time. So this is a reading of
data the system already holds, in one row per use case, and **not a new datapath**: nothing here
writes, nothing here is authored, and no field below exists because this module wanted it.

**Two halves, and the second is the point.**

The configuration half says what the installation has *decided*: purpose, processing notes, which
models are released, whether prompts are kept and for how long, which controls are on. Printed, it
is close to a *Verzeichnis von Verarbeitungstätigkeiten* — purpose, recipients, third-country
transfer, erasure deadlines — assembled from configuration the gateway actually enforces rather
than from a spreadsheet somebody maintains beside it.

The measured half says what *happened*: every audit row carries the region the request really went
to (`FRD-115` FR-10), so the register can put "where processing actually happened over this period"
beside "where the configuration says it may". **When those two disagree, that is the finding a
governance role exists to make** — and it was already there to be made: asked of a running
installation, two requests had been processed at `global`, in a log nothing surfaced.
`FRD-611` has since made that region unconfigurable, which closes the *configuration* door and
says nothing about the *measurement*; a model catalogued in a permitted region and served from
another would still be invisible without this.

**Unknown is not a violation.** A request whose row carries no region is not evidence of anything:
most dialects address a model by name alone, and the mock and local providers run in the container.
Those are reported under their provider with no region rather than counted as a transfer — the same
rule `FRD-403` applies to an unpriced request, one column along.

Served from the gateway rather than from Management, although Management authors the configuration.
The gateway is where the two halves meet: its read-model already carries every field of the first
(`UseCaseRead`), and the audit trail is the second. Assembling it in Management would mean shipping
the audit trail across the planes to reach the half that is already here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_gateway.db.models import (
    ModelRead,
    RequestLog,
    RetentionRun,
    UseCaseGroupRead,
    UseCaseMemberRead,
    UseCaseRead,
)

#: What a row says about a use case that Management has retired (`FRD-607`).
#:
#: In the register by default rather than behind a filter, and that is a decision the FRD argues
#: for: a retired use case is still a processing record for as long as its stored prompts exist,
#: and a register that omitted it would be a register that quietly stops describing the data it is
#: about. The status column is what keeps it honest.
LIVE = "live"
RETIRED = "retired"

#: How a region-less row is named. Not an empty cell: a reader has to be able to tell "processed
#: somewhere this column cannot express" from "nobody asked". The provider beside it is what makes
#: it benign or not.
NO_REGION = "(not applicable)"


@dataclass(frozen=True, slots=True)
class ReleasedModel:
    """A model this use case may call, and where the catalogue says it lives."""

    name: str
    provider: str
    publisher: str
    regions: tuple[str, ...]
    #: Whether the installation has approved it at all (`FRD-307`). A released-but-unapproved model
    #: is a real state and a governance-relevant one: the use case's configuration names it and no
    #: request will ever reach it.
    approved: bool
    #: False when the catalogue holds no row for it. A released model nobody catalogued is exactly
    #: the disagreement between the two planes this register is for.
    catalogued: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "provider": self.provider,
            "publisher": self.publisher,
            "regions": list(self.regions),
            "approved": self.approved,
            "catalogued": self.catalogued,
        }


@dataclass(frozen=True, slots=True)
class Processed:
    """Where traffic actually went, over the window."""

    region: str
    provider: str
    requests: int

    def as_dict(self) -> dict[str, Any]:
        return {"region": self.region, "provider": self.provider, "requests": self.requests}


@dataclass(frozen=True, slots=True)
class Entry:
    """One use case, as a register reads it."""

    slug: str
    name: str
    status: str
    purpose: str
    processing: str
    models: tuple[ReleasedModel, ...]
    prompts_stored: bool
    #: ``None`` where prompts are not stored at all — there is no erasure deadline for data that
    #: was never written, and printing the configured number beside "not stored" would read as one.
    retention_days: int | None
    own_requests_only: bool
    tools: bool
    prompt_caching: bool
    cache_ttl: str
    reasoning: bool
    members: int
    groups: int
    requests: int
    processed_in: tuple[Processed, ...]
    #: Regions traffic actually reached that no released model's catalogue entry names. **The
    #: finding.** Empty is the ordinary answer and is not the same as "nothing ran" — `requests`
    #: says that.
    unexpected_regions: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "status": self.status,
            "purpose": self.purpose,
            "processing": self.processing,
            "models": [model.as_dict() for model in self.models],
            "prompts_stored": self.prompts_stored,
            "retention_days": self.retention_days,
            "own_requests_only": self.own_requests_only,
            "tools": self.tools,
            "prompt_caching": self.prompt_caching,
            "cache_ttl": self.cache_ttl,
            "reasoning": self.reasoning,
            "members": self.members,
            "groups": self.groups,
            "requests": self.requests,
            "processed_in": [where.as_dict() for where in self.processed_in],
            "unexpected_regions": list(self.unexpected_regions),
        }


@dataclass(frozen=True, slots=True)
class Erasure:
    """The last retention pass, and what it removed (`FRD-608` §2.4).

    **Evidence rather than a setting.** Every row above states an erasure *deadline*; this states
    that the sweep which enforces them ran, when, and how much it took. A register that printed
    only the deadlines would be describing an intention.
    """

    ran_at: datetime
    payloads_cleared: int
    rows_deleted: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "ran_at": self.ran_at.isoformat(),
            "payloads_cleared": self.payloads_cleared,
            "rows_deleted": self.rows_deleted,
        }


@dataclass(frozen=True, slots=True)
class Register:
    """Every use case in scope, and where the installation's traffic actually went."""

    entries: tuple[Entry, ...] = ()
    #: The same measurement across everything in scope, including traffic that names no use case —
    #: break-glass keys, the console's own model checks, demo traffic. A register that only summed
    #: its rows would omit exactly the traffic `FRD-610` exists to make visible.
    processed_in: tuple[Processed, ...] = field(default_factory=tuple)
    #: Every model **the gateway** holds in its read-model, for a reader who oversees the
    #: installation. Empty for anybody else: an installation-wide list is not a member's to see.
    #:
    #: Here so the console can answer the question `FRD-608` §4 says this whole screen is for —
    #: *is what we think is configured what is actually running*. Both planes keep a catalogue, one
    #: feeds the other over Kafka, and **nothing compared them**: a model the gateway could serve
    #: sat in its read-model with no row in Management, so no console screen showed it and no role
    #: could remove it. It came from a test run and was harmless; the shape is not.
    #:
    #: The names alone. What each plane says *about* a model is already comparable through the
    #: catalogue screen; what was missing is whether the same models are there at all.
    catalogue: tuple[str, ...] = ()
    #: ``None`` when the sweep has not run since this was recorded at all — which is a fact about
    #: the installation and is reported as one. Rendering it as "0 cleared" would be the *unknown
    #: is not zero* mistake in the column where it matters most: it would read as "the sweep ran
    #: and there was nothing to remove".
    last_erasure: Erasure | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "use_cases": [entry.as_dict() for entry in self.entries],
            "processed_in": [where.as_dict() for where in self.processed_in],
            "catalogue": list(self.catalogue),
            "last_erasure": self.last_erasure.as_dict() if self.last_erasure else None,
        }


class RegisterService:
    """Assembles the register. Reads only."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def compile(
        self, scope: tuple[str, ...] | None, start: datetime, end: datetime
    ) -> Register:
        """The register for ``scope`` over ``[start, end)``.

        ``scope`` follows `visible_scope`: ``None`` is every use case, a tuple is exactly those,
        and the empty tuple is none — three answers, and folding the first into the third is the
        one mistake here that would show an installation's whole register to somebody entitled to
        one use case.
        """
        async with self._sessionmaker() as session:
            use_cases = await self._use_cases(session, scope)
            catalogue = await self._catalogue(session)
            members = await self._counts(session, UseCaseMemberRead.use_case_slug)
            groups = await self._counts(session, UseCaseGroupRead.use_case_slug)
            measured = await self._measured(session, scope, start, end)

            entries = tuple(
                self._entry(row, catalogue, members, groups, measured.get(row.slug, ()))
                for row in use_cases
            )
            return Register(
                entries=entries,
                processed_in=await self._overall(session, scope, start, end),
                catalogue=tuple(sorted(catalogue)) if scope is None else (),
                last_erasure=await self._last_erasure(session),
            )

    def _entry(
        self,
        row: UseCaseRead,
        catalogue: dict[str, ModelRead],
        members: dict[str, int],
        groups: dict[str, int],
        measured: tuple[Processed, ...],
    ) -> Entry:
        released = tuple(
            self._released(name, catalogue) for name in sorted(row.allowed_models or [])
        )
        # Where the configuration says this use case may be processed. A model whose name is its
        # whole address contributes none, which is why an empty set means "nothing to compare"
        # rather than "nowhere permitted" — see `_unexpected`.
        configured = {region for model in released for region in model.regions}
        return Entry(
            slug=row.slug,
            name=row.name,
            status=RETIRED if row.deleted_at is not None else LIVE,
            purpose=row.description,
            processing=row.processing_notes,
            models=released,
            prompts_stored=row.store_payloads,
            retention_days=row.retention_days if row.store_payloads else None,
            own_requests_only=row.restrict_members_to_own_requests,
            tools=row.tools_enabled,
            prompt_caching=row.prompt_caching_enabled,
            cache_ttl=row.prompt_cache_ttl,
            reasoning=row.include_reasoning,
            members=members.get(row.slug, 0),
            groups=groups.get(row.slug, 0),
            requests=sum(where.requests for where in measured),
            processed_in=measured,
            unexpected_regions=_unexpected(measured, configured),
        )

    @staticmethod
    def _released(name: str, catalogue: dict[str, ModelRead]) -> ReleasedModel:
        record = catalogue.get(name)
        if record is None:
            # Released and not catalogued. Reported rather than dropped: a use case naming a model
            # the catalogue does not have is a disagreement between the two planes, and hiding it
            # would make the register agree with itself by omission.
            return ReleasedModel(name, "", "", (), approved=False, catalogued=False)
        return ReleasedModel(
            name=name,
            provider=record.provider,
            publisher=record.publisher,
            regions=_regions(record.addressing),
            approved=record.approved,
            catalogued=True,
        )

    async def _use_cases(
        self, session: AsyncSession, scope: tuple[str, ...] | None
    ) -> list[UseCaseRead]:
        statement = select(UseCaseRead).order_by(UseCaseRead.slug)
        if scope is not None:
            if not scope:
                return []
            statement = statement.where(UseCaseRead.slug.in_(list(scope)))
        return list((await session.execute(statement)).scalars().all())

    async def _catalogue(self, session: AsyncSession) -> dict[str, ModelRead]:
        rows = (await session.execute(select(ModelRead))).scalars().all()
        return {row.model: row for row in rows}

    async def _counts(self, session: AsyncSession, column: Any) -> dict[str, int]:
        rows = await session.execute(select(column, func.count()).group_by(column))
        return {str(slug): int(count) for slug, count in rows.all()}

    async def _measured(
        self,
        session: AsyncSession,
        scope: tuple[str, ...] | None,
        start: datetime,
        end: datetime,
    ) -> dict[str, tuple[Processed, ...]]:
        """Where each use case's traffic actually went, from the audit trail."""
        statement = select(
            RequestLog.use_case,
            RequestLog.region,
            RequestLog.provider,
            func.count().label("requests"),
        ).where(
            RequestLog.created_at >= start,
            RequestLog.created_at < end,
            RequestLog.use_case.is_not(None),
        )
        if scope is not None:
            if not scope:
                return {}
            statement = statement.where(RequestLog.use_case.in_(list(scope)))
        statement = statement.group_by(RequestLog.use_case, RequestLog.region, RequestLog.provider)

        out: dict[str, list[Processed]] = {}
        for slug, region, provider, requests in (await session.execute(statement)).all():
            out.setdefault(str(slug), []).append(
                Processed(region or NO_REGION, provider or "", int(requests))
            )
        return {slug: tuple(sorted(rows, key=_busiest)) for slug, rows in out.items()}

    async def _last_erasure(self, session: AsyncSession) -> Erasure | None:
        """The most recent pass of the retention sweep.

        Not filtered by the window: *"has the erasure this register promises actually been
        happening"* is a question about now, and a register for last March that reported no sweep
        because none ran **in March** would be answering a question nobody asked with a figure that
        reads as an alarm.
        """
        row = (
            await session.execute(
                select(RetentionRun).order_by(RetentionRun.ran_at.desc()).limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return Erasure(
            # **UTC on both databases.** SQLite hands back a naive datetime and Postgres does not,
            # so without this the same pass serialises as `03:00` on one and `03:00+00:00` on the
            # other — a timestamp in a register whose meaning depends on which database is behind
            # it, which is exactly the thing a register may not have. The same normalisation
            # `SuspensionService` makes, for the same reason.
            ran_at=row.ran_at if row.ran_at.tzinfo else row.ran_at.replace(tzinfo=UTC),
            payloads_cleared=row.payloads_cleared,
            rows_deleted=row.rows_deleted,
        )

    async def _overall(
        self,
        session: AsyncSession,
        scope: tuple[str, ...] | None,
        start: datetime,
        end: datetime,
    ) -> tuple[Processed, ...]:
        """The same question of everything in scope, **including traffic that names no use case**.

        Only for a reader who sees every use case: for anybody else, unattributed traffic is not
        theirs to see, and folding it into their summary would be the widening `visible_scope`
        exists to prevent.
        """
        if scope is not None:
            return ()
        statement = (
            select(
                RequestLog.region,
                RequestLog.provider,
                func.count().label("requests"),
            )
            .where(RequestLog.created_at >= start, RequestLog.created_at < end)
            .group_by(RequestLog.region, RequestLog.provider)
        )
        rows = [
            Processed(region or NO_REGION, provider or "", int(requests))
            for region, provider, requests in (await session.execute(statement)).all()
        ]
        return tuple(sorted(rows, key=_busiest))


def _busiest(where: Processed) -> tuple[int, str, str]:
    """Most traffic first, then by name so two equal rows do not swap between reads."""
    return (-where.requests, where.region, where.provider)


def _unexpected(measured: tuple[Processed, ...], configured: set[str]) -> tuple[str, ...]:
    """Regions traffic reached that no released model's catalogue entry names.

    Empty when the configuration names no region at all: a use case whose models are addressed by
    name alone has nothing to disagree with, and reporting every region as unexpected there would
    make the column noise — which is the reliable way to have a finding ignored.

    A row with no region is never unexpected. It is not a transfer; it is a dialect that addresses
    a model by name, or a provider running in the container. **Absence of information is not
    evidence of a violation**, which is the same rule this project applies to an unpriced request
    and to an undeclared capability, read in the direction that matters here.
    """
    if not configured:
        return ()
    return tuple(
        sorted(
            {
                where.region
                for where in measured
                if where.region != NO_REGION and where.region not in configured
            }
        )
    )


def _regions(addressing: Any) -> tuple[str, ...]:
    """The catalogue's regions for one model, in both spellings.

    The third reader of this shape, and deliberately not a fourth definition: `ModelDeclaration
    .regions` and `vertex/adapters._declared_regions` say the same thing for the request path.
    `test_the_two_readers_of_a_region_list_agree` pins the format; this one is a *reading* of the
    same field for a document, and it answers `()` rather than raising for anything it cannot
    parse — a register must describe a malformed entry, not fail to print because of one.
    """
    block = addressing if isinstance(addressing, dict) else {}
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
