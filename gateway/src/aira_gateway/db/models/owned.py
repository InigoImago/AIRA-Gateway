"""Tables the gateway writes itself: credentials, the audit trail, incidents and usage counters."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from aira_gateway.db.base import Base, new_id


class ApiKey(Base):
    """A self-generated API key. Only the hash of the full key is stored (`FRD-101`)."""

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    prefix: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    key_hash: Mapped[str] = mapped_column(String(64))
    #: The **owner**: who answers for the key, and whose name every audit row carries.
    subject: Mapped[str] = mapped_column(String(255))
    #: The use case the key is bound to (`FRD-205`). Null for the demo/CLI break-glass keys, which
    #: are usable only with an explicit `/uc` selector.
    use_case: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Who created the key, when that is not its owner — a team's shared credential (`FRD-604`
    #: FR-5). Empty for an ordinary key. Kept here so an incident can be worked from the gateway's
    #: data alone.
    issued_by: Mapped[str | None] = mapped_column(String(150), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: When the key stops working on its own. **NULL means never** — older keys and the break-glass
    #: key carry it, and an expiry that cannot be omitted is one an operator sets to the year 3000.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RequestLog(Base):
    """A persisted API request and response with its attribution (`FRD-103`)."""

    __tablename__ = "request_logs"
    __table_args__ = (
        # The trace view's keyset page (`FRD-502`, `0033`): it filters `use_case IN (…)` and orders
        # by `(created_at DESC, id DESC)`, so the index is shaped like the cursor.
        Index(
            "ix_request_logs_use_case_page",
            "use_case",
            "created_at",
            "id",
            postgresql_ops={"created_at": "DESC", "id": "DESC"},
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    # -- attribution (`FRD-102`) ------------------------------------------------------------------
    subject: Mapped[str] = mapped_column(String(255), index=True)
    #: What the subject was **called** at the time — descriptive, never an identity (`FRD-606`). An
    #: OIDC subject is a directory id and a key's is its owner's username; this groups the two.
    #: Null where the credential names nobody.
    username: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    auth_method: Mapped[str] = mapped_column(String(32))
    #: Which Keycloak realm minted the token (`FRD-118`). NULL for an API key and for demo mode.
    issuer: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    use_case: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    #: Indexed (`0033`): the incident view filters on it (`/v1beta/traces?source_ip=`).
    source_ip: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    #: Which *system* called (`FRD-122` FR-5): an API key's prefix or an OIDC client id, never any
    #: part of a secret. Distinct from `subject`, so a leaked key's blast radius can be assessed.
    credential: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # -- request and response ---------------------------------------------------------------------
    api: Mapped[str] = mapped_column(String(32))
    operation: Mapped[str] = mapped_column(String(64))
    #: What answered.
    model: Mapped[str] = mapped_column(String(128), index=True)
    #: What the caller named, before routing or fallback (`FRD-122` FR-3).
    requested_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: ``direct`` | ``route`` | ``fallback:N`` — how the served model was arrived at.
    model_selection: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: Where the request was actually processed (`FRD-115` FR-10): residency as evidence, per
    #: request rather than per deployment.
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    publisher: Mapped[str | None] = mapped_column(String(32), nullable=True)
    region: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    status: Mapped[int] = mapped_column(Integer)
    #: Why the request ended this way (:class:`aira_gateway.audit.Outcome`). Indexed for reporting.
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    #: Which pipeline steps ran and what each decided — never the classifier's reasoning text.
    pipeline_decisions: Mapped[Any | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    #: A pipeline step blocked or flagged this request (`FRD-505` FR-5). A column rather than a
    #: query over `pipeline_decisions`: JSON containment is spelled differently on SQLite and on
    #: Postgres, so such a filter would be tested on one of the two only.
    flagged: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    #: `{"declared": n, "called": [name, …]}` (`FRD-131` FR-7). **Names and counts, never
    #: arguments**: arguments are caller content and belong in the payload, under retention and
    #: redaction.
    tool_calls: Mapped[Any | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    #: Which controls were running on a fallback while this request was handled (`FRD-405`).
    degraded: Mapped[Any | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: **Of which** came from a provider cache, and **of which** populated one (`FRD-133`) —
    #: subsets of `prompt_tokens`, kept apart because they are priced apart.
    cached_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_write_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: **Of which** the model spent thinking (`FRD-135`), a subset of `completion_tokens`. NULL on
    #: older rows, because zero would claim the model did not think.
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Bytes the caller sent, as counted by the body-size middleware (`FRD-501`). NULL where
    #: unknown, never 0; such rows are left out of both sides of the `payload_size` share.
    request_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    #: Cost in nano-units of the installation currency. NULL means the model had no price on file,
    #: which is distinct from a genuine zero (`FRD-403`).
    cost_nanos: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # -- payloads: redacted, absent when `store_payloads` is off, cleared by retention (`FRD-404`) -
    # `none_as_null` writes SQL NULL rather than JSON `null`; without it "no payload" and "a null
    # payload" are indistinguishable and the retention pruner rewrites the same rows forever.
    request_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    response_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )


class PayloadAccess(Base):
    """One reading of a stored prompt or response (`FRD-505` FR-6).

    The record is what allows the view at all (`ADR-0009`): content is shown to people outside the
    use case only because every read names who read what, and on what authority. Written **before**
    the payload is handed over, and kept independently of `request_logs` retention — the content
    expires, the fact that somebody read it does not.
    """

    __tablename__ = "payload_access"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    #: The request whose content was read. Not a foreign key: retention deletes that row, and the
    #: access record must outlive it.
    request_log_id: Mapped[str] = mapped_column(String(36), index=True)
    use_case: Mapped[str] = mapped_column(String(64), default="", index=True)
    #: Who read it.
    subject: Mapped[str] = mapped_column(String(255), index=True)
    #: What that reader was **called** — the same pairing `RequestLog` keeps (`FRD-606`), so one
    #: person's reads are not filed under two names. NULL where the credential names nobody.
    username: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    #: On what authority: `incident`, `use_case_admin` or `use_case_member`.
    ground: Mapped[str] = mapped_column(String(32), default="")
    #: The reader's organisation-wide roles at that moment, comma-separated (`FRD-622` FR-3). NULL
    #: for a read recorded before they were kept — unknown, which is not "none".
    roles: Mapped[str | None] = mapped_column(String(255), nullable=True)


class AnomalyEvent(Base):
    """One finding: a rule crossed its threshold for one target (`FRD-501`).

    The row says what was **measured** — how bad, out of how many — not merely that something fired.
    """

    __tablename__ = "anomaly_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    rule_id: Mapped[int] = mapped_column(Integer, index=True)
    rule_name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(32), index=True)
    #: The use case the traffic belonged to. Nullable so a future kind whose target spans use cases
    #: is not blocked by the column.
    use_case: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    #: What the rule grouped by (`subject` | `credential` | `use_case`) and the value it found.
    target: Mapped[str] = mapped_column(String(16))
    target_value: Mapped[str] = mapped_column(String(255), index=True)
    #: The measurement, its threshold, and how many rows it was drawn from.
    observed: Mapped[int] = mapped_column(Integer)
    threshold: Mapped[int] = mapped_column(Integer)
    sample: Mapped[int] = mapped_column(Integer)
    window_minutes: Mapped[int] = mapped_column(Integer)
    #: What was actually done — apart from the rule's configured action, because recording is not
    #: enforcing (`ADR-0014` §3).
    action_taken: Mapped[str] = mapped_column(String(32), default="alert")
    #: One sentence a person can read without joining anything.
    detail: Mapped[str] = mapped_column(String(500), default="")


class AccessSuspension(Base):
    """A written decision that some traffic is stopped (`FRD-503`).

    Made by a rule that fired or by a person in an incident, and always says **who** (`author`),
    **why** (`reason`) and **until when** (`expires_at`). Kept after it is lifted, as history.
    """

    __tablename__ = "access_suspensions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    #: The use case this applies within. NULL means everywhere.
    use_case: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    target: Mapped[str] = mapped_column(String(16))
    target_value: Mapped[str] = mapped_column(String(255), index=True)
    action: Mapped[str] = mapped_column(String(16))
    #: Requests per minute a throttled target is held to. NULL for a block.
    throttle_rpm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: When it stops applying. NULL only for one made by a person, who can also lift it; a rule's
    #: always expires (`ADR-0014` §2).
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: ``rule:<name>`` or ``user:<subject>``. Never blank: an unattributable restriction is
    #: indistinguishable from a fault.
    author: Mapped[str] = mapped_column(String(255))
    reason: Mapped[str] = mapped_column(String(500), default="")
    #: Set when somebody ends it early. The row stays.
    lifted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lifted_by: Mapped[str | None] = mapped_column(String(255), nullable=True)


class RetentionRun(Base):
    """One pass of the retention sweep, and what it removed (`FRD-608` §2.4).

    Erasure as evidence rather than as a setting. One row per pass, a few a day at most, and never
    pruned: a table of erasure evidence that erased its own history would defeat itself.
    """

    __tablename__ = "retention_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    #: When the pass ran, from the clock the sweep used to decide what had expired — not the column
    #: default, so it is testable.
    ran_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    #: Bodies stripped from rows whose metadata was kept (`FRD-404`'s first clock).
    payloads_cleared: Mapped[int] = mapped_column(Integer, default=0)
    #: Whole rows removed, where an installation has opted into a record retention.
    rows_deleted: Mapped[int] = mapped_column(Integer, default=0)


class BudgetUsage(Base):
    """Running usage per scope and period, accounted by the gateway to enforce budgets (`FRD-401`).

    The system of record; the Redis ledger is a cache seeded from it (`FRD-405`).
    """

    __tablename__ = "budget_usage"

    scope_key: Mapped[str] = mapped_column(String(320), primary_key=True)
    period_key: Mapped[str] = mapped_column(String(10), primary_key=True)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    requests: Mapped[int] = mapped_column(Integer, default=0)
    cost_nanos: Mapped[int] = mapped_column(BigInteger, default=0)
    #: Requests served by a model with no price on file, counted apart so they cannot read as free
    #: (`FRD-403`).
    unpriced_requests: Mapped[int] = mapped_column(Integer, default=0)
