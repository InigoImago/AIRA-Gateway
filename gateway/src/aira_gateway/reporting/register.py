"""The register of processing activities (`FRD-608`): one row per use case, for governance.

Governance is a **comparison** activity — which use cases store prompts, which keep them longest,
which were processed outside the EU — so this reads data the system already holds into one row per
use case. Nothing here writes and nothing here is authored.

Two halves. The **configuration** says what the installation decided — purpose, released models,
retention, controls — close to a *Verzeichnis von Verarbeitungstätigkeiten*, assembled from what the
gateway enforces. The **measurement** says what happened: every audit row carries the region the
request really went to (`FRD-115` FR-10). Where the two disagree is the finding a governance role
exists to make.

**Unknown is not a violation.** A row with no region — a dialect that addresses a model by name, a
provider in the container — is reported under its provider, never counted as a transfer.
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

#: A use case's status. A retired one (`FRD-607`) stays in the register by default: it is still a
#: processing record for as long as its stored prompts exist.
LIVE = "live"
RETIRED = "retired"

#: How a region-less row is named. Not an empty cell, so "processed somewhere this column cannot
#: express" stays distinguishable from "nobody asked".
NO_REGION = "(not applicable)"


@dataclass(frozen=True, slots=True)
class ReleasedModel:
    """A model this use case may call, and where the catalogue says it lives."""

    name: str
    provider: str
    publisher: str
    regions: tuple[str, ...]
    #: Whether the installation approved it at all (`FRD-307`). Released but unapproved means the
    #: configuration names a model no request will ever reach.
    approved: bool
    #: False when the catalogue holds no row for it — a disagreement between the two planes.
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
    #: ``None`` where prompts are not stored: there is no erasure deadline for data never written.
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
    #: **The finding**: regions traffic reached that no released model's catalogue entry names.
    #: Empty is the ordinary answer, and is not "nothing ran" — `requests` says that.
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
    """The last retention pass, and what it removed (`FRD-608` §2.4) — evidence that the deadlines
    every entry states are enforced, rather than a setting."""

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
    #: break-glass keys, the console's model checks, demo traffic (`FRD-610`).
    processed_in: tuple[Processed, ...] = field(default_factory=tuple)
    #: The model names **the gateway's** read-model holds, for an oversight reader only, so the
    #: console can compare the two planes' catalogues (`FRD-608` §4). Empty for anybody else.
    catalogue: tuple[str, ...] = ()
    #: ``None`` when the sweep has no recorded pass — never "0 cleared", which would read as a sweep
    #: that ran and found nothing.
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

        ``scope`` follows `visible_scope`: ``None`` is every use case, a tuple exactly those, and
        the empty tuple none. Folding the first into the last would show the whole register to
        somebody entitled to one use case.
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
        # Where the configuration says this use case may be processed; see `_unexpected` for why an
        # empty set means "nothing to compare".
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
            # Released and not catalogued: reported, not dropped, or the register would agree with
            # itself by omission.
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

        Not filtered by the window: whether erasure has been happening is a question about now.
        """
        row = (
            await session.execute(
                select(RetentionRun).order_by(RetentionRun.ran_at.desc()).limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return Erasure(
            # UTC on both databases: SQLite hands back a naive datetime and Postgres does not, and
            # a register timestamp may not depend on which one is behind it.
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

        Only for a reader who sees every use case: for anybody else unattributed traffic is not
        theirs to see, and including it would be the widening `visible_scope` exists to prevent.
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

    Empty when the configuration names no region: models addressed by name alone leave nothing to
    disagree with, and flagging every region there would make the column noise. A row with no
    region is never unexpected — absence of information is not evidence of a violation.
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

    The same field `ModelDeclaration.regions` reads for the request path
    (`test_the_two_readers_of_a_region_list_agree` pins the format), but answering ``()`` for
    anything it cannot parse: a register must describe a malformed entry, not fail on it.
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
