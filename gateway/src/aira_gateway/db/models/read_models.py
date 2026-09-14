"""Read-models of Management's configuration, applied from Kafka (`FRD-204`).

The gateway never asks Management while a request is in flight, so everything the request path
decides on arrives here first (`aira_gateway.consumer.apply`). Events from an older Management
lack newer fields during a rolling update, which is why several defaults below are deliberate.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from aira_gateway.db.base import Base, new_id


class UseCaseRead(Base):
    """A use case (`FRD-204`)."""

    __tablename__ = "use_cases"

    slug: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    #: Set when Management retires the use case, cleared never (`FRD-607`). Such a row grants
    #: **nothing** — its keys are deactivated and its children deleted by the same event — and
    #: survives so retention and the payload view can still answer for the use case.
    #: Indexed here as in `0040_use_case_tombstone`: the two must agree, or `alembic
    #: --autogenerate` proposes dropping the index.
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    description: Mapped[str] = mapped_column(String(2000), default="")
    processing_notes: Mapped[str] = mapped_column(String(2000), default="")
    #: Whether prompts and responses are written at all, and for how long they are kept (`FRD-404`).
    store_payloads: Mapped[bool] = mapped_column(Boolean, default=True)
    retention_days: Mapped[int] = mapped_column(Integer, default=7)
    #: A use-case **user** sees only their own requests; an administrator sees all (`FRD-505`).
    #: Default false, the behaviour that existed before: an added restriction, not a new default.
    restrict_members_to_own_requests: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Whether the use case may declare functions for the model to call (`FRD-131`). Default false:
    #: least privilege is the state a use case starts in.
    tools_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Whether a model's reasoning is returned and stored (`FRD-135`). Default off.
    include_reasoning: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Mark the stable prefix as cacheable (`FRD-133`). Off by default: on Vertex the cache scope is
    #: the **whole organisation**, so a confidential system prompt must be opted in deliberately.
    prompt_caching_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    #: `5m` or `1h` (`FRD-133`). An hour costs 2x base input to write against 1.25x, so the cheap
    #: one is the default.
    prompt_cache_ttl: Mapped[str] = mapped_column(String(4), default="5m")
    #: The models this use case has been released (`FRD-308`). **Empty means none; `None` means the
    #: event could not say** (an older Management) and is read as unrestricted — collapsing the two
    #: would stop every use case on a partly upgraded stack. A JSON list rather than a relation:
    #: the gateway only asks it of the one row it already fetched.
    allowed_models: Mapped[list[str] | None] = mapped_column(JSON, nullable=True, default=None)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UseCaseMemberRead(Base):
    """Membership of a use case (`FRD-204`)."""

    __tablename__ = "use_case_members"
    __table_args__ = (UniqueConstraint("use_case_slug", "subject", name="uq_member"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    use_case_slug: Mapped[str] = mapped_column(String(64), index=True)
    subject: Mapped[str] = mapped_column(String(255), index=True)
    role: Mapped[str] = mapped_column(String(16), default="user")


class UseCaseGroupRead(Base):
    """Access to a use case granted to a **Keycloak group** (`FRD-209`)."""

    __tablename__ = "use_case_groups"
    __table_args__ = (UniqueConstraint("use_case_slug", "group_path", name="uq_group_grant"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    use_case_slug: Mapped[str] = mapped_column(String(64), index=True)
    #: Keycloak's group path, exactly as a token reports it.
    group_path: Mapped[str] = mapped_column(String(255), index=True)
    role: Mapped[str] = mapped_column(String(16), default="user")


class PipelineConfigRead(Base):
    """A use case's pre-dispatch pipeline (`FRD-300`)."""

    __tablename__ = "pipeline_configs"

    use_case: Mapped[str] = mapped_column(String(64), primary_key=True)
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    fallback_models: Mapped[list[str]] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BudgetRead(Base):
    """A usage budget (`FRD-400`, `FRD-403`)."""

    __tablename__ = "budgets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: Empty for an **installation** budget (`FRD-610`): the residual bucket for spend no use case
    #: owns. Empty rather than NULL, so `use_case == ""` and `IS NULL` are not two spellings of one
    #: question on a column compared on every request; `Scope.applying` reads the emptiness.
    use_case: Mapped[str] = mapped_column(String(64), index=True, default="")
    scope: Mapped[str] = mapped_column(String(16))
    subject: Mapped[str] = mapped_column(String(255), default="")
    period: Mapped[str] = mapped_column(String(8))
    #: Money as integer nano-units, never a float (`aira_common.money`).
    limit_cost_nanos: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    limit_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    limit_requests: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class RateLimitRead(Base):
    """A request-rate limit (`FRD-405`). A budget caps how much; this caps how fast."""

    __tablename__ = "rate_limits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    use_case: Mapped[str] = mapped_column(String(64), index=True)
    scope: Mapped[str] = mapped_column(String(16))
    subject: Mapped[str] = mapped_column(String(255), default="")
    limit_rpm: Mapped[int] = mapped_column(Integer)
    #: How many may arrive at once. Bursts are normal traffic; sustained flooding is not.
    burst: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class AnomalyRuleRead(Base):
    """An anomaly rule (`FRD-500`), with everything the engine needs to evaluate it locally.

    ``use_case`` of ``NULL`` means the rule applies everywhere — deliberately not an empty string,
    which would be a use case named "" matching nothing while looking like it matched everything.
    """

    __tablename__ = "anomaly_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    use_case: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(32), index=True)
    window_minutes: Mapped[int] = mapped_column(Integer)
    #: Percent for rate and ratio kinds, a count for event kinds; `kind` says which.
    threshold: Mapped[int] = mapped_column(Integer)
    #: The kind's second number, when it needs one — today only `payload_size`'s byte figure.
    parameter: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: Below this many requests a rate says nothing. 0 for kinds that are not proportions.
    min_sample: Mapped[int] = mapped_column(Integer, default=0)
    action: Mapped[str] = mapped_column(String(16), default="alert")
    target: Mapped[str] = mapped_column(String(16), default="subject")
    #: How long a throttle or block lasts. NULL for `alert`, which takes nothing away.
    action_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Requests per minute a throttled target is held to. NULL unless the action is `throttle`.
    throttle_rpm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ModelRead(Base):
    """The model catalogue: prices, capabilities and addressing (`FRD-403`, `FRD-114`).

    Prices are per million tokens in nano-units of the installation currency, per direction. An
    **undeclared** model gets the baseline capabilities and nothing more (`FRD-114` FR-7): absence
    of information is not permission.
    """

    __tablename__ = "model_catalog"

    model: Mapped[str] = mapped_column(String(128), primary_key=True)
    #: Whether a Global Administrator has released the model (`FRD-307`). **True by default here,
    #: false in Management**, deliberately: an event from an older Management carries no such
    #: field, and reading its absence as "not approved" would retire every model mid-upgrade.
    approved: Mapped[bool] = mapped_column(Boolean, default=True)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    provider: Mapped[str] = mapped_column(String(64), default="")
    input_price_per_million_nanos: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_price_per_million_nanos: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: What a cache read and a cache write cost (`FRD-133`). Absent means the ordinary input rate,
    #: which never under-bills.
    cached_input_price_per_million_nanos: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    cache_write_price_per_million_nanos: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )

    # -- what it can do, and how it is reached (`FRD-114`) ----------------------------------------
    capabilities: Mapped[Any | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    publisher: Mapped[str] = mapped_column(String(32), default="")
    platform: Mapped[str] = mapped_column(String(32), default="")
    addressing: Mapped[Any | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    #: What the price attaches to when the caller-facing name is not the vendor's (`ADR-0011` r2).
    underlying_model: Mapped[str] = mapped_column(String(128), default="")
    #: Prompt and answer together (`FRD-132` §11). Published as `inputTokenLimit`; never enforced.
    context_window: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    default_max_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    thinking: Mapped[Any | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    embedding: Mapped[Any | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    attachments: Mapped[Any | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    hosting: Mapped[str] = mapped_column(String(16), default="")
    deprecated: Mapped[bool] = mapped_column(Boolean, default=False)
    numeric_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)


class RoleRead(Base):
    """What a stored role may do and which group confers it (`FRD-614` FR-8).

    The Global Administrator and IT Security are not here: they are fixed in code, so a read model
    that cannot be read never takes them away.
    """

    __tablename__ = "roles"

    slug: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str] = mapped_column(String(120), default="")
    #: Empty for IT Steuerung, whose group is configuration (`AIRA_ROLE_GROUPS`).
    group_path: Mapped[str] = mapped_column(String(255), default="")
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
