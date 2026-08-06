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

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_common.models import BASELINE_CAPABILITIES, Capability, Hosting, parse_capabilities
from aira_gateway.db.models import ModelRead


@dataclass(frozen=True, slots=True)
class ModelDeclaration:
    """What the catalog says about one model. Absent from the catalog is a declaration too — an
    undeclared one, which is why this is never ``None`` and why ``declared`` exists."""

    name: str
    declared: bool = False
    capabilities: frozenset[Capability] = BASELINE_CAPABILITIES
    max_output_tokens: int | None = None
    default_max_output_tokens: int | None = None
    thinking: dict[str, Any] | None = None
    embedding: dict[str, Any] | None = None
    attachments: dict[str, Any] = field(default_factory=dict)
    publisher: str = ""
    platform: str = ""
    hosting: str = ""
    deprecated: bool = False

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

    def output_cap(self, requested: int | None) -> int | None:
        """The output token cap to send upstream: the caller's, else the model's default.

        Not merely convenience — Anthropic **requires** ``max_tokens`` on every request
        (`FRD-119` §5.3), so a caller who omits it would otherwise receive a vendor error about a
        field they never set. It also sharpens the pre-dispatch reservation for every vendor.
        """
        return requested if requested is not None else self.default_max_output_tokens


class ModelCatalog:
    """Reads model declarations from the read-model. Never calls Management (FR-8)."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def declaration(self, model: str) -> ModelDeclaration:
        async with self._sessionmaker() as session:
            record = await session.get(ModelRead, model)
        if record is None:
            return ModelDeclaration(name=model)
        return _from_record(model, record)

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


def _from_record(model: str, record: ModelRead) -> ModelDeclaration:
    capabilities = parse_capabilities(record.capabilities)
    declared = bool(capabilities)
    return ModelDeclaration(
        name=model,
        declared=declared,
        # A row with prices but no capability list is *undeclared*, so it gets the baseline —
        # not an empty set, which would refuse the generation that already works today.
        capabilities=capabilities if declared else BASELINE_CAPABILITIES,
        max_output_tokens=record.max_output_tokens,
        default_max_output_tokens=record.default_max_output_tokens,
        thinking=record.thinking if isinstance(record.thinking, dict) else None,
        embedding=record.embedding if isinstance(record.embedding, dict) else None,
        attachments=record.attachments if isinstance(record.attachments, dict) else {},
        publisher=record.publisher or "",
        platform=record.platform or "",
        hosting=record.hosting or "",
        deprecated=bool(record.deprecated),
    )
