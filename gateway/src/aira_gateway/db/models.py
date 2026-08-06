"""Gateway ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from aira_gateway.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class ApiKey(Base):
    """A self-generated API key. Only the hash of the full key is stored (FRD-101)."""

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    prefix: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    key_hash: Mapped[str] = mapped_column(String(64))
    subject: Mapped[str] = mapped_column(String(255))
    # Use case the key is bound to (FRD-205). Management-issued keys carry it; the demo/CLI
    # break-glass keys leave it null (usable only with an explicit /uc selector).
    use_case: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RequestLog(Base):
    """A persisted API request + response with its attribution (FRD-103)."""

    __tablename__ = "request_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    # attribution (FRD-102)
    subject: Mapped[str] = mapped_column(String(255), index=True)
    auth_method: Mapped[str] = mapped_column(String(32))
    use_case: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    source_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Which *system* called (FRD-122 FR-5): an API key's prefix, or an OIDC client id. Distinct
    # from `subject`, which is who the credential belongs to. Without it, five keys issued for one
    # use case by one administrator are one identity in the log — and a leaked key can be revoked
    # but its blast radius cannot be assessed. Never any part of a secret.
    credential: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # request/response metadata
    api: Mapped[str] = mapped_column(String(32))
    operation: Mapped[str] = mapped_column(String(64))
    #: What answered. Unchanged in meaning, so every existing query, report and index still holds.
    model: Mapped[str] = mapped_column(String(128), index=True)
    #: What the caller named, before routing or fallback (FRD-122 FR-3). With cross-vendor chains
    #: (ADR-0012) these differ, and "why did the Anthropic spend triple" has no answer without it.
    requested_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: ``direct`` | ``route`` | ``fallback:N`` — how the served model was arrived at.
    model_selection: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[int] = mapped_column(Integer)
    #: Why the request ended this way (:class:`aira_gateway.audit.Outcome`). Indexed: reporting
    #: groups by it, and a refusal that is not groupable is a log line rather than a figure.
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    #: Which pipeline steps ran and what each decided — never the classifier's reasoning text.
    pipeline_decisions: Mapped[Any | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    #: Which controls were running on a fallback while this request was handled (FRD-405).
    degraded: Mapped[Any | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # What the request cost, in nano-units of the installation currency. NULL means the model
    # had no price on file — deliberately distinct from a genuine zero (FRD-403).
    cost_nanos: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Payloads (redacted; absent when store_payloads is off, and removed once the use case's
    # retention period has passed — FRD-404).
    #
    # `none_as_null` matters: without it SQLAlchemy writes the JSON value `null` instead of SQL
    # NULL, so "has no payload" and "has a payload that is null" become indistinguishable and
    # the retention pruner rewrites the same rows forever.
    request_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    response_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )


class UseCaseRead(Base):
    """Gateway read-model of a use case, fed from Management via Kafka (FRD-204)."""

    __tablename__ = "use_cases"

    slug: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(String(2000), default="")
    processing_notes: Mapped[str] = mapped_column(String(2000), default="")
    # Whether prompts/responses are written at all for this use case, and for how long they are
    # kept once written (FRD-404).
    store_payloads: Mapped[bool] = mapped_column(Boolean, default=True)
    retention_days: Mapped[int] = mapped_column(Integer, default=7)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UseCaseMemberRead(Base):
    """Gateway read-model of use-case membership (FRD-204)."""

    __tablename__ = "use_case_members"
    __table_args__ = (UniqueConstraint("use_case_slug", "subject", name="uq_member"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    use_case_slug: Mapped[str] = mapped_column(String(64), index=True)
    subject: Mapped[str] = mapped_column(String(255), index=True)
    role: Mapped[str] = mapped_column(String(16), default="user")


class PipelineConfigRead(Base):
    """Gateway read-model of a use case's pre-dispatch pipeline, fed from Management (FRD-300)."""

    __tablename__ = "pipeline_configs"

    use_case: Mapped[str] = mapped_column(String(64), primary_key=True)
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    fallback_models: Mapped[list[str]] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BudgetRead(Base):
    """Gateway read-model of a usage budget, fed from Management (FRD-400/403)."""

    __tablename__ = "budgets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    use_case: Mapped[str] = mapped_column(String(64), index=True)
    scope: Mapped[str] = mapped_column(String(16))
    subject: Mapped[str] = mapped_column(String(255), default="")
    period: Mapped[str] = mapped_column(String(8))
    # Money as integer nano-units — never a float; see aira_common.money.
    limit_cost_nanos: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    limit_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    limit_requests: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class RateLimitRead(Base):
    """Gateway read-model of a request-rate limit, fed from Management (FRD-405).

    A budget states how much may be spent over a day or a month; this states how fast. The two
    are independent: a monthly budget is no protection against a retry loop burning it in an
    afternoon, and a rate limit says nothing about the total.
    """

    __tablename__ = "rate_limits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    use_case: Mapped[str] = mapped_column(String(64), index=True)
    scope: Mapped[str] = mapped_column(String(16))
    subject: Mapped[str] = mapped_column(String(255), default="")
    limit_rpm: Mapped[int] = mapped_column(Integer)
    # How many may arrive at once. Bursts are normal traffic; sustained flooding is not.
    burst: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class BudgetUsage(Base):
    """Running usage per scope+period, accounted by the gateway to enforce budgets (FRD-401)."""

    __tablename__ = "budget_usage"

    scope_key: Mapped[str] = mapped_column(String(320), primary_key=True)
    period_key: Mapped[str] = mapped_column(String(10), primary_key=True)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    requests: Mapped[int] = mapped_column(Integer, default=0)
    cost_nanos: Mapped[int] = mapped_column(BigInteger, default=0)
    # Requests served by a model with no price on file. Counted apart so the gap is visible
    # instead of quietly reading as "this consumption was free" (FRD-403).
    unpriced_requests: Mapped[int] = mapped_column(Integer, default=0)


class ModelPriceRead(Base):
    """Gateway read-model of the model catalog's prices, fed from Management (FRD-403).

    Prices are stored per one million tokens in nano-units of the installation currency, split
    by direction because every provider bills input and output differently.
    """

    __tablename__ = "model_prices"

    model: Mapped[str] = mapped_column(String(128), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    provider: Mapped[str] = mapped_column(String(64), default="")
    input_price_per_million_nanos: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_price_per_million_nanos: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
