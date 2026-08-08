"""API-key persistence and verification service (FRD-101)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aira_gateway.auth import keys
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings
from aira_gateway.db.models import ApiKey


def _default_key_days() -> int:
    """The configured lifetime, read here rather than passed in.

    One definition shared with Management (`aira_common.config`), because a key policy with two
    definitions is a key policy with two answers.
    """
    return GatewaySettings().api_key_default_days


class ApiKeyService:
    """CRUD + verification for API keys, bound to an async session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, subject: str, label: str | None = None, *, expires_in_days: int | None = None
    ) -> tuple[str, ApiKey]:
        """Create a key; return the plaintext (shown once) and the stored record.

        ``expires_in_days`` defaults to the installation's configured lifetime. This is the
        **break-glass** path — an operator with database access, for the moment the control plane
        is unavailable — and it is bounded like every other key: a credential minted during an
        incident is exactly the one nobody remembers to take away afterwards.
        """
        full, prefix, key_hash = keys.generate_api_key()
        days = expires_in_days if expires_in_days is not None else _default_key_days()
        expires_at = datetime.now(UTC) + timedelta(days=days) if days > 0 else None
        record = ApiKey(
            prefix=prefix,
            key_hash=key_hash,
            subject=subject,
            label=label,
            expires_at=expires_at,
        )
        self._session.add(record)
        await self._session.commit()
        await self._session.refresh(record)
        return full, record

    async def verify(self, full: str) -> Principal | None:
        """Resolve a presented key to a Principal, or None if invalid/revoked."""
        prefix = keys.parse_prefix(full)
        if prefix is None:
            return None
        record = await self._active_by_prefix(prefix)
        if record is None or not keys.verify_hash(full, record.key_hash):
            return None
        if record.expires_at is not None and self._aware(record.expires_at) <= datetime.now(UTC):
            # Expiry is checked *here* rather than filtered in the query, so that an expired key is
            # a refused credential and not a missing one: the two look identical to a caller and
            # very different to whoever has to explain what changed at 03:00.
            return None
        use_cases = (record.use_case,) if record.use_case else ()
        return Principal(
            subject=record.subject,
            method="api_key",
            # The prefix *is* the key's identity — it is the public half of the credential and is
            # already stored unhashed, so recording it discloses nothing the database does not.
            credential=record.prefix,
            label=record.label,
            use_cases=use_cases,
        )

    async def revoke(self, prefix: str) -> bool:
        """Deactivate an active key by prefix. Returns True if one was revoked."""
        record = await self._active_by_prefix(prefix)
        if record is None:
            return False
        record.is_active = False
        record.revoked_at = datetime.now(UTC)
        await self._session.commit()
        return True

    async def ensure_demo_key(self) -> None:
        """Idempotently seed the deterministic demo key (demo mode only).

        The one key with **no** expiry, and the exemption is the same one `ADR-0015` makes for the
        deployment guard: demo mode is a loud, deliberate declaration, the key's plaintext is
        published in this repository, and a demo that stops working a month after somebody cloned
        the repository is a demo nobody trusts. Its security property comes from `AIRA_DEMO_MODE`
        being a declared state, not from a date.
        """
        prefix = keys.parse_prefix(keys.DEMO_API_KEY)
        assert prefix is not None  # constant is a valid key
        existing = await self._session.execute(select(ApiKey).where(ApiKey.prefix == prefix))
        if existing.scalar_one_or_none() is not None:
            return
        self._session.add(
            ApiKey(
                prefix=prefix,
                key_hash=keys.hash_api_key(keys.DEMO_API_KEY),
                subject="demo",
                label="demo-key",
            )
        )
        await self._session.commit()

    @staticmethod
    def _aware(moment: datetime) -> datetime:
        """SQLite hands back naive datetimes; Postgres does not. Compare in UTC either way."""
        return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)

    async def _active_by_prefix(self, prefix: str) -> ApiKey | None:
        result = await self._session.execute(
            select(ApiKey).where(ApiKey.prefix == prefix, ApiKey.is_active.is_(True))
        )
        return result.scalar_one_or_none()
