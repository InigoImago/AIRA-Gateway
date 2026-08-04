"""Gateway ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from aira_gateway.db.base import Base


class ApiKey(Base):
    """A self-generated API key. Only the hash of the full key is stored (FRD-101)."""

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    prefix: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    key_hash: Mapped[str] = mapped_column(String(64))
    subject: Mapped[str] = mapped_column(String(255))
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
