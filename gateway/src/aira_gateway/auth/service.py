"""API-key persistence and verification service (`FRD-101`)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aira_gateway.auth import keys
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings
from aira_gateway.db.models import ApiKey


def _default_key_days() -> int:
    """The configured key lifetime, defined once with Management (`aira_common.config`)."""
    return GatewaySettings().api_key_default_days


class ApiKeyService:
    """CRUD + verification for API keys, bound to an async session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, subject: str, label: str | None = None, *, expires_in_days: int | None = None
    ) -> tuple[str, ApiKey]:
        """Create a key; return the plaintext (shown once) and the stored record.

        This is the **break-glass** path, and it is bounded like every other key: a credential
        minted during an incident is the one nobody remembers to take away afterwards.
        ``expires_in_days`` defaults to the installation's configured lifetime.
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
            # Checked here rather than filtered in the query, so an expired key is a refused
            # credential and not a missing one.
            return None
        use_cases = (record.use_case,) if record.use_case else ()
        return Principal(
            subject=record.subject,
            method="api_key",
            # An API key's subject already **is** the owner's username (`FRD-604`).
            username=record.subject,
            # The prefix is the public half of the credential, already stored unhashed.
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

        The one key with **no** expiry (`ADR-0015`): its plaintext is published in this repository,
        and its safety comes from `AIRA_DEMO_MODE` being a declared state, not from a date.
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
