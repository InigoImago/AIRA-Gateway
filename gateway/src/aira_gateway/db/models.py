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
    #: When the key stops working on its own (2026-08-08). **NULL means never**, which is what
    #: every key issued before this existed carries and what the break-glass key needs — an expiry
    #: that cannot be omitted is one an operator sets to the year 3000. A stated end date is what
    #: turns "who still has a key" from an inventory exercise into a property of the system.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    #: Where the request was actually processed (FRD-115 FR-10). Residency is a configuration
    #: claim; these three columns are what make it evidence, per request rather than per
    #: deployment.
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    publisher: Mapped[str | None] = mapped_column(String(32), nullable=True)
    region: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    status: Mapped[int] = mapped_column(Integer)
    #: Why the request ended this way (:class:`aira_gateway.audit.Outcome`). Indexed: reporting
    #: groups by it, and a refusal that is not groupable is a log line rather than a figure.
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    #: Which pipeline steps ran and what each decided — never the classifier's reasoning text.
    pipeline_decisions: Mapped[Any | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    #: True when a pipeline step objected to this request — blocked it, or flagged it and let it
    #: through (`FRD-505` FR-5).
    #:
    #: A **column**, not a query over `pipeline_decisions`. Two reasons, and the second is the one
    #: that decided it: JSON containment is written differently on SQLite and Postgres, and the
    #: hermetic tests run on one while production runs on the other — a filter exercised against
    #: only one of the two is a filter tested on one of the two. The first is simpler: this is the
    #: question an incident opens with, and it should be an index rather than a scan.
    flagged: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    #: What the model asked to have run, and how much it was offered (`FRD-131` FR-7).
    #:
    #: **Names and counts only, never arguments.** Arguments are caller content: they belong under
    #: `store_payloads`, inside the retention clock and behind `FRD-406`'s redaction, not in a
    #: metadata column that no clock covers. The shape is `{"declared": n, "called": [name, …]}`,
    #: written through an allow-list for the reason `FRD-122` gives — a fact recorded in one place
    #: is a fact a later change cannot quietly widen.
    #:
    #: `declared` is worth as much as `called`: "the model was offered ten functions and asked for
    #: none" and "it was offered none" are different events, and only one of them is a model
    #: behaving oddly.
    tool_calls: Mapped[Any | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    #: Which controls were running on a fallback while this request was handled (FRD-405).
    degraded: Mapped[Any | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: How many bytes the caller sent. Counted by the body-size middleware, which was already
    #: counting them to enforce the ceiling (`FRD-122` §12) — so this costs nothing new. NULL on
    #: every row written before `FRD-501`, and such rows are excluded from **both** sides of the
    #: `payload_size` share, so an old row cannot look like a small one.
    request_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
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


class PayloadAccess(Base):
    """One reading of a stored prompt or response (`FRD-505` FR-6).

    The record is not a nicety attached to the feature — it is the **reason the feature could be
    granted at all**. `ADR-0009` refused this view because it shows content to people outside the
    use case that produced it; what makes that acceptable is that the act leaves a trail naming who
    read what and on what authority. An access nobody can review is exactly what the ADR was
    protecting against.

    Written **before** the payload is handed over, so a reader cannot receive content whose access
    failed to record. Kept independently of `request_logs` retention: the content expires, the fact
    that somebody read it does not.
    """

    __tablename__ = "payload_access"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    #: The request whose content was read. Not a foreign key: the row it points at is deleted by
    #: retention, and losing the access record with it would be the wrong way round.
    request_log_id: Mapped[str] = mapped_column(String(36), index=True)
    use_case: Mapped[str] = mapped_column(String(64), default="", index=True)
    #: Who read it.
    subject: Mapped[str] = mapped_column(String(255), index=True)
    #: On what authority — `incident`, `use_case_admin` or `use_case_member`. Two people may read
    #: the same prompt for entirely different reasons, and a review asks which.
    ground: Mapped[str] = mapped_column(String(32), default="")


class AnomalyEvent(Base):
    """One finding: a rule crossed its threshold for one target (`FRD-501`).

    The row says what was **measured**, not merely that something fired. A finding nobody can check
    is a finding nobody acts on — and the first question anyone asks is "how bad, out of how many".
    """

    __tablename__ = "anomaly_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    rule_id: Mapped[int] = mapped_column(Integer, index=True)
    rule_name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(32), index=True)
    #: The use case the traffic belonged to. NULL only when a global rule fired on a target that
    #: spans use cases, which today is never — kept nullable so a future kind is not blocked by a
    #: column definition.
    use_case: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    #: What the rule grouped by (`subject` | `credential` | `use_case`) and the value it found.
    target: Mapped[str] = mapped_column(String(16))
    target_value: Mapped[str] = mapped_column(String(255), index=True)
    #: The measurement, its threshold, and how many rows it was drawn from.
    observed: Mapped[int] = mapped_column(Integer)
    threshold: Mapped[int] = mapped_column(Integer)
    sample: Mapped[int] = mapped_column(Integer)
    window_minutes: Mapped[int] = mapped_column(Integer)
    #: What was actually done — deliberately separate from the rule's configured action, because
    #: recording is not enforcing (`ADR-0014` §3) and the row has to say which happened.
    action_taken: Mapped[str] = mapped_column(String(32), default="alert")
    #: One sentence a person can read without joining anything.
    detail: Mapped[str] = mapped_column(String(500), default="")


class AccessSuspension(Base):
    """A written decision that some traffic is stopped (`FRD-503`).

    Created by a rule that fired, or by a person in an incident. Three fields are what make it a
    decision rather than a side effect: **who** (`author`), **why** (`reason`) and **until when**
    (`expires_at`). An automatic block with none of those is an outage with a good reason, and the
    first thing anyone asks at 03:00 is who did this.

    Kept after it is lifted rather than deleted: "this caller was blocked for two hours last
    Tuesday" is exactly the question an incident review asks.
    """

    __tablename__ = "access_suspensions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    #: The use case this applies within. NULL means everywhere — an operator stopping a credential
    #: does not have to know which use cases it is bound to.
    use_case: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    target: Mapped[str] = mapped_column(String(16))
    target_value: Mapped[str] = mapped_column(String(255), index=True)
    action: Mapped[str] = mapped_column(String(16))
    #: Requests per minute a throttled target is held to. NULL for a block.
    throttle_rpm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: When it stops applying. NULL only for one made by a person, who can also lift it — a rule
    #: cannot, which is why an automatic one always has an expiry (`ADR-0014` §2).
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: ``rule:<name>`` or ``user:<subject>``. Never blank: an unattributable restriction is
    #: indistinguishable from a fault.
    author: Mapped[str] = mapped_column(String(255))
    reason: Mapped[str] = mapped_column(String(500), default="")
    #: Set when somebody ends it early. The row stays; this is what makes it history.
    lifted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lifted_by: Mapped[str | None] = mapped_column(String(255), nullable=True)


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
    #: When true, a use-case **user** sees only the requests they made themselves; an
    #: administrator of the use case still sees all of them (`FRD-505`).
    #:
    #: Default **false**, which is the behaviour that already existed: a team sees its own team's
    #: traffic. This is an added restriction an administrator may impose, not a permission that
    #: was previously assumed — flipping the default would silently narrow every existing use case
    #: on the day it shipped.
    restrict_members_to_own_requests: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Whether this use case may declare functions for the model to call (`FRD-131`). **Default
    #: false**, and the default is the feature: least privilege is not a setting somebody remembers
    #: to switch off, it is the state a use case starts in.
    tools_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UseCaseMemberRead(Base):
    """Gateway read-model of use-case membership (FRD-204)."""

    __tablename__ = "use_case_members"
    __table_args__ = (UniqueConstraint("use_case_slug", "subject", name="uq_member"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    use_case_slug: Mapped[str] = mapped_column(String(64), index=True)
    subject: Mapped[str] = mapped_column(String(255), index=True)
    role: Mapped[str] = mapped_column(String(16), default="user")


class UseCaseGroupRead(Base):
    """Gateway read-model of access granted to a **Keycloak group** (`FRD-209`).

    The gateway cannot ask Management on the request path (`FRD-204`), so a group grant arrives the
    same way members, keys, budgets and limits do — over Kafka, into a table this side owns.
    """

    __tablename__ = "use_case_groups"
    __table_args__ = (UniqueConstraint("use_case_slug", "group_path", name="uq_group_grant"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    use_case_slug: Mapped[str] = mapped_column(String(64), index=True)
    #: Keycloak's group path, exactly as a token reports it.
    group_path: Mapped[str] = mapped_column(String(255), index=True)
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


class AnomalyRuleRead(Base):
    """Gateway read-model of an anomaly rule, fed from Management (`FRD-500`).

    ``use_case`` is nullable and that is the whole point: ``NULL`` means the rule applies
    everywhere. Deliberately not an empty string, which would be a use case named "" and would
    match nothing while looking like it matched everything.

    Everything the engine needs is here, because the gateway never asks Management while a request
    is in flight (`FRD-500` FR-7) — the same rule the model catalog follows.
    """

    __tablename__ = "anomaly_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    use_case: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(32), index=True)
    window_minutes: Mapped[int] = mapped_column(Integer)
    #: Percent for rate and ratio kinds, a count for event kinds. What it means comes from `kind`.
    threshold: Mapped[int] = mapped_column(Integer)
    #: The kind's second number, when it needs one — today only `payload_size`'s byte figure.
    parameter: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: Below this many requests a rate says nothing. 0 for kinds that are not proportions.
    min_sample: Mapped[int] = mapped_column(Integer, default=0)
    action: Mapped[str] = mapped_column(String(16), default="alert")
    target: Mapped[str] = mapped_column(String(16), default="subject")
    #: How long a throttle or block lasts. NULL for `alert`, which does not expire because it
    #: never took anything away.
    action_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Requests per minute a throttled target is held to. NULL unless the action is `throttle`.
    throttle_rpm: Mapped[int | None] = mapped_column(Integer, nullable=True)
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


class ModelRead(Base):
    """Gateway read-model of the model catalog, fed from Management (FRD-403, FRD-114).

    Renamed from ``model_prices`` when `FRD-114` added capabilities: a table called *prices* that
    decides whether a thinking budget is accepted is a name that lies, and the next person to read
    it pays for that.

    Prices are per one million tokens in nano-units of the installation currency, split by
    direction because every provider bills input and output differently. The capability columns
    are what validation reads — and an **undeclared** model gets the baseline and nothing more
    (FRD-114 FR-7): absence of information is not permission.
    """

    __tablename__ = "model_catalog"

    model: Mapped[str] = mapped_column(String(128), primary_key=True)
    #: Whether a Global Administrator has released this model for use (`FRD-307`).
    #:
    #: **True by default here, false by default in Management** — and the asymmetry is deliberate.
    #: Management is where the decision is made, so a new declaration starts unapproved. This
    #: table is fed by events, and an event from an older Management carries no such field; reading
    #: its absence as "not approved" would take every model out of service the moment one plane is
    #: upgraded before the other.
    approved: Mapped[bool] = mapped_column(Boolean, default=True)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    provider: Mapped[str] = mapped_column(String(64), default="")
    input_price_per_million_nanos: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_price_per_million_nanos: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # -- what it can do, and how it is reached (FRD-114) -----------------------------------
    capabilities: Mapped[Any | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    publisher: Mapped[str] = mapped_column(String(32), default="")
    platform: Mapped[str] = mapped_column(String(32), default="")
    addressing: Mapped[Any | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    #: What the price attaches to when the caller-facing name is not the vendor's (ADR-0011 r2).
    underlying_model: Mapped[str] = mapped_column(String(128), default="")
    max_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    default_max_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    thinking: Mapped[Any | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    embedding: Mapped[Any | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    attachments: Mapped[Any | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    hosting: Mapped[str] = mapped_column(String(16), default="")
    deprecated: Mapped[bool] = mapped_column(Boolean, default=False)
    numeric_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
