"""The register the privacy notice is written from: every processing of personal data (`FRD-625`).

**What this file promises.** Every column either plane stores is either named here under the
activity that stores it, or declared not personal — and
`test_the_privacy_notice_names_every_column.py` fails on a column that is neither. A new column,
table or plane is therefore a change to this file; this file is part of the notice's digest
(`edition.py`), and a changed digest refuses the suite until the notice's edition moves. That chain
is the rule *"a change to the processing is a change to the notice"* made into something the suite
enforces rather than something a reviewer remembers.

What it cannot see: processing that stores nothing — a transmission, a telemetry attribute, a
browser's storage. Those are activities with no ``stores``, kept here so they have a text in every
language, and they stay a reviewer's duty.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from aira_common.permissions import Permission


class Plane(StrEnum):
    """Whose database a table is in."""

    GATEWAY = "gateway"
    MANAGEMENT = "management"


@dataclass(frozen=True, slots=True)
class Stored:
    """Columns of one table that an activity writes."""

    plane: Plane
    table: str
    columns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Activity:
    """One processing activity, as the notice presents it."""

    key: str
    stores: tuple[Stored, ...] = ()


def _gw(table: str, *columns: str) -> Stored:
    return Stored(Plane.GATEWAY, table, columns)


def _mg(table: str, *columns: str) -> Stored:
    return Stored(Plane.MANAGEMENT, table, columns)


#: Every column of ``request_logs`` but the two bodies: who, from where, when, what, at what cost.
REQUEST_METADATA = (
    "id", "created_at", "subject", "username", "auth_method", "issuer", "use_case", "source_ip",
    "credential", "api", "operation", "model", "requested_model", "model_selection", "provider",
    "publisher", "region", "status", "outcome", "pipeline_decisions", "flagged", "tool_calls",
    "degraded", "prompt_tokens", "cached_input_tokens", "cache_write_tokens", "completion_tokens",
    "reasoning_tokens", "total_tokens", "latency_ms", "request_bytes", "trace_id", "cost_nanos",
)  # fmt: skip

ACCOUNT = Activity(
    "account",
    (
        _mg(
            "auth_user",
            "id",
            "password",
            "last_login",
            "is_superuser",
            "username",
            "first_name",
            "last_name",
            "email",
            "is_staff",
            "is_active",
            "date_joined",
        ),  # fmt: skip
        _mg("auth_user_groups", "id", "user_id", "group_id"),
        _mg("auth_user_user_permissions", "id", "user_id", "permission_id"),
        _mg("api_oidcidentity", "id", "subject", "user_id", "created_at"),
        _mg("api_pendingidentity", "user_id", "created_at"),
        _mg(
            "guardian_userobjectpermission",
            "id",
            "permission_id",
            "content_type_id",
            "object_pk",
            "user_id",
        ),  # fmt: skip
        _mg("usecases_usecasemembership", "id", "use_case_id", "user_id", "role", "created_at"),
        _mg("outbox_outboxevent", "id", "topic", "key", "event_type", "payload", "created_at"),
        _gw("use_case_members", "id", "use_case_slug", "subject", "role"),
    ),
)
ADMINISTRATION = Activity(
    "administration",
    (
        _mg("roles_rolechange", "id", "role_slug", "action", "actor", "at", "before", "after"),
        _mg("roles_storedrole", "created_by"),
        _mg("usecases_usecase", "deleted_by"),
        _mg("usecases_usecasegroupgrant", "granted_by"),
        _mg("api_pendingidentity", "invited_by"),
        _mg("outbox_outboxevent", "traceparent", "published_at"),
    ),
)
API_KEYS = Activity(
    "api_keys",
    (
        _mg(
            "apikeys_apikey",
            "id",
            "use_case_id",
            "owner_id",
            "issued_by",
            "prefix",
            "key_hash",
            "label",
            "is_active",
            "created_at",
            "revoked_at",
            "expires_at",
        ),  # fmt: skip
        _gw(
            "api_keys",
            "id",
            "prefix",
            "key_hash",
            "subject",
            "use_case",
            "label",
            "issued_by",
            "is_active",
            "created_at",
            "revoked_at",
            "expires_at",
        ),  # fmt: skip
    ),
)
REQUEST_RECORD = Activity("request_record", (_gw("request_logs", *REQUEST_METADATA),))
CONTENT = Activity("content", (_gw("request_logs", "request_payload", "response_payload"),))
CONTENT_READS = Activity(
    "content_reads",
    (
        _gw(
            "payload_access",
            "id",
            "created_at",
            "request_log_id",
            "use_case",
            "subject",
            "username",
            "ground",
            "roles",
        ),  # fmt: skip
    ),
)
CONSUMPTION = Activity(
    "consumption",
    (
        _gw(
            "budget_usage",
            "scope_key",
            "period_key",
            "tokens",
            "requests",
            "cost_nanos",
            "unpriced_requests",
        ),  # fmt: skip
        _gw("budgets", "subject"),
        _gw("rate_limits", "subject"),
        _mg("budgets_budget", "subject"),
        _mg("ratelimits_ratelimit", "subject"),
    ),
)
SECURITY = Activity(
    "security",
    (
        _gw(
            "anomaly_events",
            "id",
            "created_at",
            "rule_id",
            "rule_name",
            "kind",
            "use_case",
            "target",
            "target_value",
            "observed",
            "threshold",
            "sample",
            "window_minutes",
            "action_taken",
            "detail",
        ),  # fmt: skip
        _gw(
            "access_suspensions",
            "id",
            "created_at",
            "use_case",
            "target",
            "target_value",
            "action",
            "throttle_rpm",
            "expires_at",
            "author",
            "reason",
            "lifted_at",
            "lifted_by",
        ),  # fmt: skip
    ),
)
#: Prompts leave the installation for the model that answers them. Nothing is stored for it.
MODEL_PROVIDERS = Activity("model_providers")
#: Spans and log lines to the installation's observability stack and any forwarding channel.
TELEMETRY = Activity("telemetry")
PIPELINE_TESTS = Activity(
    "pipeline_tests",
    (
        _mg("smoketests_testrun", "requested_by_id"),
        _mg("smoketests_testresult", "response", "note", "rated_by_id", "rated_at"),
    ),
)
ACKNOWLEDGEMENT = Activity(
    "acknowledgement",
    (
        _mg(
            "privacy_noticeacknowledgement",
            "id",
            "user_id",
            "version",
            "language",
            "first_at",
            "last_at",
        ),  # fmt: skip
    ),
)

#: In the order the notice presents them.
ACTIVITIES: tuple[Activity, ...] = (
    ACCOUNT,
    ADMINISTRATION,
    API_KEYS,
    REQUEST_RECORD,
    CONTENT,
    CONTENT_READS,
    CONSUMPTION,
    SECURITY,
    MODEL_PROVIDERS,
    TELEMETRY,
    PIPELINE_TESTS,
    ACKNOWLEDGEMENT,
)
ACTIVITY_KEYS: tuple[str, ...] = tuple(activity.key for activity in ACTIVITIES)

#: Tables that hold no personal data, and why. A column shaped like a person in one of them still
#: fails the check (`PERSON_SHAPED`), so a `created_by` added to the catalogue is not waved through.
NOT_PERSONAL_TABLES: dict[tuple[Plane, str], str] = {
    (Plane.MANAGEMENT, "django_content_type"): "Django's own table of model names",
    (Plane.MANAGEMENT, "auth_permission"): "permission names",
    (Plane.MANAGEMENT, "auth_group"): "group names: Keycloak group paths and role names",
    (Plane.MANAGEMENT, "auth_group_permissions"): "which permission a group carries",
    (Plane.MANAGEMENT, "guardian_groupobjectpermission"): "grants to groups, not to people",
    (Plane.MANAGEMENT, "catalog_model"): "the model catalogue",
    (Plane.MANAGEMENT, "usecases_usecase_allowed_models"): "which models a use case may call",
    (Plane.MANAGEMENT, "pipelines_pipelineconfig"): "pipeline configuration",
    (Plane.MANAGEMENT, "anomalies_anomalyrule"): "rules, not their findings",
    (Plane.MANAGEMENT, "smoketests_testcase"): "the question catalogue",
    (Plane.GATEWAY, "use_cases"): "use-case configuration",
    (Plane.GATEWAY, "use_case_groups"): "grants to groups, not to people",
    (Plane.GATEWAY, "pipeline_configs"): "pipeline configuration",
    (Plane.GATEWAY, "anomaly_rules"): "rules, not their findings",
    (Plane.GATEWAY, "model_catalog"): "the model catalogue",
    (Plane.GATEWAY, "roles"): "what a role may do",
    (Plane.GATEWAY, "retention_runs"): "counts of what a retention pass removed",
}

#: Columns of a table an activity writes that are not themselves about a person — the rest of a
#: use case's configuration beside its `deleted_by`, for instance.
NOT_PERSONAL_COLUMNS: dict[tuple[Plane, str], tuple[str, ...]] = {
    (Plane.MANAGEMENT, "usecases_usecase"): (
        "id",
        "slug",
        "name",
        "description",
        "processing_notes",
        "store_payloads",
        "tools_enabled",
        "prompt_caching_enabled",
        "include_reasoning",
        "prompt_cache_ttl",
        "restrict_members_to_own_requests",
        "retention_days",
        "created_at",
        "updated_at",
        "deleted_at",
    ),  # fmt: skip
    (Plane.MANAGEMENT, "usecases_usecasegroupgrant"): (
        "id",
        "use_case_id",
        "group_path",
        "role",
        "created_at",
    ),  # fmt: skip
    (Plane.MANAGEMENT, "roles_storedrole"): (
        "id",
        "slug",
        "label",
        "group_path",
        "permissions",
        "builtin",
        "created_at",
        "updated_at",
    ),  # fmt: skip
    (Plane.MANAGEMENT, "budgets_budget"): (
        "id",
        "use_case_id",
        "scope",
        "period",
        "limit_cost",
        "limit_tokens",
        "limit_requests",
        "enabled",
        "created_at",
        "updated_at",
    ),  # fmt: skip
    (Plane.MANAGEMENT, "ratelimits_ratelimit"): (
        "id",
        "use_case_id",
        "scope",
        "limit_rpm",
        "burst",
        "enabled",
        "created_at",
        "updated_at",
    ),  # fmt: skip
    (Plane.MANAGEMENT, "smoketests_testrun"): (
        "id",
        "model",
        "use_case",
        "started_at",
        "finished_at",
    ),  # fmt: skip
    (Plane.MANAGEMENT, "smoketests_testresult"): (
        "id",
        "run_id",
        "case_id",
        "error",
        "latency_ms",
        "verdict",
    ),  # fmt: skip
    (Plane.GATEWAY, "budgets"): (
        "id",
        "use_case",
        "scope",
        "period",
        "limit_cost_nanos",
        "limit_tokens",
        "limit_requests",
        "enabled",
    ),  # fmt: skip
    (Plane.GATEWAY, "rate_limits"): ("id", "use_case", "scope", "limit_rpm", "burst", "enabled"),
}

#: A column name that is about a person wherever it appears: who did something, who it was done to,
#: where they called from. A name, not a list of tables, so a table nobody thought of is covered.
PERSON_SHAPED = re.compile(
    r"(^|_)(user|subject|email|username|actor|owner|author|person|member|ip|address|agent)(_|$)"
    r"|_by(_id)?$"
)

#: Installation-wide permissions that reach somebody else's personal data. The notice lists, for
#: every role this installation defines, which of these it holds — so an installation that gives
#: IT Steuerung `payload.read_any` says so to everybody, in every language.
PERSONAL_DATA_PERMISSIONS: tuple[Permission, ...] = (
    Permission.USECASE_READ_ALL,
    Permission.USECASE_MANAGE_ALL,
    Permission.TRACE_READ_ALL,
    Permission.REPORT_READ_ALL,
    Permission.ANOMALY_READ_ALL,
    Permission.INCIDENT_INVESTIGATE,
    Permission.PAYLOAD_READ_ANY,
    Permission.CONTENT_READ_READ,
    Permission.INCIDENT_SUSPEND,
    Permission.DIRECTORY_SEARCH,
)

#: The rest of the catalogue, and why each reaches nobody's personal data. A permission that is in
#: neither fails `test_the_privacy_notice_names_every_column`, so a new one is decided, not assumed.
NOT_PERSONAL_PERMISSIONS: dict[Permission, str] = {
    Permission.USECASE_CREATE: "creates a use case, which holds no person",
    Permission.USECASE_READ_RETIRED: "lists retired use cases' configuration",
    Permission.USECASE_PURGE: "erases a retired use case",
    Permission.CATALOG_WRITE: "the model catalogue and its prices",
    Permission.BUDGET_INSTALLATION_WRITE: "the installation's own budget",
    Permission.ANOMALY_RULE_GLOBAL_WRITE: "writes rules; their findings are anomaly.read_all",
    Permission.OPERATIONS_DIAGNOSE: "checks whether a model answers",
    Permission.SMOKETEST_AUTHOR: "writes test questions",
    Permission.SMOKETEST_RUN_ANY: "runs test questions against a pipeline",
    Permission.ROLE_READ: "reads what roles may do",
    Permission.ROLE_MANAGE: "changes what roles may do — which the notice then states",
}
