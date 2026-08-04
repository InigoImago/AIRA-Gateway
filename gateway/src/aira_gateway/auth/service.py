"""API-key persistence and verification service (FRD-101)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aira_gateway.auth import keys
from aira_gateway.auth.principal import Principal
from aira_gateway.db.models import ApiKey


class ApiKeyService:
    """CRUD + verification for API keys, bound to an async session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, subject: str, label: str | None = None) -> tuple[str, ApiKey]:
        """Create a key; return the plaintext (shown once) and the stored record."""
        full, prefix, key_hash = keys.generate_api_key()
        record = ApiKey(prefix=prefix, key_hash=key_hash, subject=subject, label=label)
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
        return Principal(subject=record.subject, method="api_key", label=record.label)

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
        """Idempotently seed the deterministic demo key (demo mode only)."""
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

    async def _active_by_prefix(self, prefix: str) -> ApiKey | None:
        result = await self._session.execute(
            select(ApiKey).where(ApiKey.prefix == prefix, ApiKey.is_active.is_(True))
        )
        return result.scalar_one_or_none()
