"""Break each guarded property on purpose and check that the suite notices.

**Why this exists.** On 2026-08-05 a review found seven real defects in code whose test suite was
green and whose line coverage was 99%. Coverage cannot see a missing requirement: every line of
the rate limiter was executed, and it still drained the wrong bucket. A test that has never been
observed to fail is not evidence — it only proves that the test and the code agree, which they
inevitably do when both were written from the same mental model.

So each entry below is a **defect that would matter**, expressed as a one-line edit to the source.
Running this applies each in turn and checks that some test fails. A mutation that survives is a
property nothing is defending — a gap, whether or not the code is currently correct.

    make mutants

Adding a mutation is the cheapest way to state "this property must stay true". When you fix a bug,
add the mutation that reintroduces it: that is what stops it coming back silently.

Notes for whoever extends this:

- The baseline suite must be green first, or every mutation looks "caught" for the wrong reason.
- **A property enforced twice cannot live here either, and that is not a reason to weaken it.**
  "A validation detail carries nothing unserialisable" is guarded by a flag *and* by a
  comprehension that copies two named fields — either alone is sufficient, so no single-line edit
  reproduces the 500 it fixed. It was written as a mutation, observed not to fail, and removed
  rather than kept as a claim. Its two hermetic tests in `test_edge_cases.py` are the record.
- **Some properties cannot live here, and saying so is part of being honest.** "A client dropping
  a real socket still leaves its request settled and logged" is one: closing a generator in-process
  raises `GeneratorExit` and a bare `await` in a `finally` runs fine, so no hermetic test can tell
  the shielded version from the unshielded one. It is guarded by `tests/integration/
  test_request_path.py` instead. A mutation that survives here would be a false claim, and a
  harness that makes one is worse than no harness.
- **A second one of that family, 2026-08-13.** "A caller who hangs up mid-stream is recorded under
  `499` on **both** surfaces" cannot live here either, and it was checked rather than assumed:
  the defect was reintroduced — the Gemini stream assigning `acct.status = 200` unconditionally —
  and the whole hermetic gateway suite passed, 1424 tests. It cannot see it, because the two
  versions differ only when nothing was served, and `TestClient` buffers a whole streamed body
  before a test can hang up. Guarded by `test_dev_round_evidence.py`, which drives both streams
  over a real socket and asserts they record the same status as well as the same outcome.
- Keep the test selection **wide enough**. A too-narrow selection reports a false gap: M25 was
  first reported as surviving only because the test that catches it lives in another file, and
  T10/E8/X3/X4 repeated the mistake three more times. **The rule that would have prevented all of
  them: when you add a mutation, name every file whose tests you expect to fail — not the file the
  code lives beside.** The two are unrelated, and the second is the one that comes to mind. When a mutation survives, check *which files run* before
  concluding the property is undefended — the wrong conclusion costs a test nobody needed.
- **A mutation that survives may mean the rule is enforced twice.** C4 survived because the
  embedding capability was checked in two places, so removing either changed nothing observable.
  That is not a missing test; it is redundancy, and the fix is to delete one of the copies. Two
  places deciding one rule drift, and the one that drifts is whichever is not under test.
- The anchor text must be unique in the file; a missing anchor is reported rather than skipped
  silently, because a mutation that no longer applies is a mutation that stopped protecting
  anything.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parent.parent

# A killed run cannot restore anything from its own `finally`, and a source file left mutated is
# a booby trap for whoever runs the suite next — it looks like a real defect and wastes the day.
# So the original is written to disk *before* the edit and removed only once it is back.
JOURNAL = ROOT / ".mutation-journal.json"


@dataclass(frozen=True, slots=True)
class Mutation:
    ident: str
    property_defended: str
    path: str
    old: str
    new: str
    tests: str


NO_SILENT_DROP = "gateway/tests/test_no_silent_drop.py"
RATELIMIT = "gateway/tests/test_ratelimit.py"
RATELIMIT_ROUTES = "gateway/tests/test_ratelimit_routes.py"
BUDGET_RESERVATION = "gateway/tests/test_budget_reservation.py"
BUDGET_ROUTES = "gateway/tests/test_budget_routes.py"
BUDGET_SERVICE = "gateway/tests/test_budget_service.py"
LOG_WRITER = "gateway/tests/test_log_writer.py"
COUNTERS = "libs/tests/test_counters.py"

AUTH = "gateway/tests/test_auth_service.py gateway/tests/test_auth_oidc.py"
ATTRIBUTION = "gateway/tests/test_attribution.py"
HARDENING = "gateway/tests/test_hardening.py"
CONSUMER = "gateway/tests/test_consumer_apply.py"
MGMT_RBAC = "management/backend/tests/test_rbac.py management/backend/tests/test_usecases.py"
ANOMALY_RULES = "management/backend/tests/test_anomaly_rules.py"
ANOMALY_CONSUMER = "gateway/tests/test_consumer_apply.py"
ANOMALY_ENGINE = "gateway/tests/test_anomaly_engine.py"
SUSPENSIONS = "gateway/tests/test_suspensions.py"
REPORTING = "gateway/tests/test_reporting.py"
TOPICS = "tools/tests/test_kafka_topics_are_created.py"
MGMT_HARDENING = "management/backend/tests/test_hardening.py"
MGMT_SETTINGS = (
    "management/backend/tests/test_settings.py management/backend/tests/test_hardening.py"
)
MONEY = "libs/tests/test_money.py"
COST = "gateway/tests/test_cost_budgets.py"
CATALOG = "management/backend/tests/test_catalog.py"
PIPELINE = "gateway/tests/test_pipeline_engine.py gateway/tests/test_pipeline_routes.py"
CLASSIFIERS = "gateway/tests/test_pipeline_classifiers.py gateway/tests/test_pipeline_engine.py"
PII = "gateway/tests/test_pipeline_pii_filter.py"
NOTICE = "gateway/tests/test_response_notice.py"
ACCOUNTING = "gateway/tests/test_pipeline_accounting.py"
RETENTION = "gateway/tests/test_retention.py gateway/tests/test_store_payloads.py"
MODEL_CATALOG = "gateway/tests/test_model_catalog.py"
VERTEX = "gateway/tests/test_vertex.py"
REQUIREMENTS = "gateway/tests/test_dispatch_requirements.py"
ATTACHMENTS = "gateway/tests/test_attachments.py"
KIRA = "gateway/tests/test_kira_surface.py"
TOKENS = "libs/tests/test_tokens.py"
CATALOG_DECLARATION = "management/backend/tests/test_catalog_declaration.py"
OPENAI_DIALECT = "gateway/tests/test_openai_dialect.py"
EDGE = "gateway/tests/test_edge_cases.py"
FOUNDRY = "gateway/tests/test_foundry.py"
SECRETS = "libs/tests/test_secrets.py"
DIAGNOSTICS = "gateway/tests/test_diagnostics.py"
PROVIDER_OFFERINGS = "gateway/tests/test_provider_offerings.py"
RELEASE = "gateway/tests/test_use_case_model_release.py"
DRYRUN = "gateway/tests/test_pipeline_dryrun.py"
CSV_EXPORT = "gateway/tests/test_csv_export.py"
THINKING = "gateway/tests/test_thinking.py gateway/tests/test_serving_options.py"
RESPONSE_SCHEMA = "gateway/tests/test_response_schema.py gateway/tests/test_serving_options.py"
EMBEDDING = "gateway/tests/test_embedding_options.py gateway/tests/test_serving_options.py"
TOOLS = "gateway/tests/test_tool_calling.py gateway/tests/test_tool_parts.py"
SELECTOR = "gateway/tests/test_selector_never_grants.py"
DEPLOYMENT_SAFETY = "gateway/tests/test_deployment_safety.py"
ACCESS_LOGS = "libs/tests/test_access_log_redaction.py"
AUTH_BOUND = "gateway/tests/test_auth_attempt_bound.py"
SECURITY_HEADERS = "gateway/tests/test_security_headers.py"
REDACTION = "gateway/tests/test_redaction.py gateway/tests/test_store_payloads.py"
KEY_EXPIRY = "gateway/tests/test_auth_service.py management/backend/tests/test_apikeys.py"
STREAM_CONDITIONS = (
    "gateway/tests/test_streaming_takes_the_same_conditions.py gateway/tests/test_hardening.py"
)
THROTTLE_WIRE = (
    "gateway/tests/test_throttle_reaches_the_limiter.py gateway/tests/test_ratelimit.py "
    "gateway/tests/test_auth_attempt_bound.py"
)
ERROR_HEADERS = (
    "gateway/tests/test_error_responses_are_headered.py gateway/tests/test_security_headers.py"
)
REALLY_STREAMS = "gateway/tests/test_streams_actually_stream.py"
REFUSAL_PARITY = (
    "gateway/tests/test_surfaces_record_refusals_alike.py gateway/tests/test_kira_surface.py"
)
FIELD_SPELLINGS = "gateway/tests/test_kira_field_spellings.py gateway/tests/test_kira_surface.py"
KIRA_ENVELOPE = "gateway/tests/test_kira_envelope_everywhere.py gateway/tests/test_kira_surface.py"
WIRE_CONTRACT = (
    "gateway/tests/test_kira_wire_contract.py gateway/tests/test_kira_surface.py "
    "gateway/tests/test_kira_compatibility_round.py"
)
GOOGLE_SDK = "gateway/tests/test_google_sdk_speaks_to_us.py"
COMPACTION_KEYS = "management/backend/tests/test_outbox_routing.py"
CONSUMER_SURVIVES = "gateway/tests/test_config_distribution_survives_a_bad_event.py"
DETECTION_WINDOW = "gateway/tests/test_a_failed_tick_keeps_its_window.py"
REVOCATION_TIME = "gateway/tests/test_consumer_apply.py"
KIRA_COMPAT = "gateway/tests/test_kira_compatibility_round.py gateway/tests/test_kira_surface.py"
SURFACE_PARITY = (
    "gateway/tests/test_pipeline_accounting.py "
    "gateway/tests/test_every_surface_records_alike.py "
    "gateway/tests/test_tool_calling.py"
)
MODE_PARSE = "gateway/tests/test_thinking.py gateway/tests/test_kira_surface.py"

SERVING_OPTIONS = (
    "gateway/tests/test_serving_options.py gateway/tests/test_kira_surface.py "
    "gateway/tests/test_vertex.py gateway/tests/test_gemini_upstream.py"
)

MUTATIONS = [
    # ---- authentication and the tenant boundary (FRD-101/102, ADR-0006/0007) -------------
    Mutation(
        "A1",
        "a revoked key is refused at verification",
        "gateway/src/aira_gateway/auth/service.py",
        "select(ApiKey).where(ApiKey.prefix == prefix, ApiKey.is_active.is_(True))",
        "select(ApiKey).where(ApiKey.prefix == prefix)",
        AUTH,
    ),
    Mutation(
        "A2",
        "a replayed creation never reactivates a revoked key",
        "gateway/src/aira_gateway/consumer/apply.py",
        '        record.key_hash = payload["key_hash"]',
        '        record.key_hash = payload["key_hash"]\n        record.is_active = True',
        CONSUMER,
    ),
    Mutation(
        "A3",
        "a token from the wrong issuer is rejected",
        "libs/src/aira_common/oidc.py",
        "                issuer=self._issuer,",
        "                # issuer intentionally dropped",
        AUTH,
    ),
    Mutation(
        "A4",
        "a configured audience is enforced",
        "libs/src/aira_common/oidc.py",
        '                    "verify_aud": self._audience is not None,',
        '                    "verify_aud": False,',
        AUTH,
    ),
    Mutation(
        "A5",
        "an OIDC principal may only act on a use case they are a member of",
        "gateway/src/aira_gateway/auth/dependencies.py",
        'if principal.method == "oidc" and use_case not in principal.use_cases:',
        'if principal.method == "oidc" and False:',
        f"{ATTRIBUTION} {HARDENING}",
    ),
    Mutation(
        "A6",
        "a key bound to one use case cannot act on another",
        "gateway/src/aira_gateway/auth/dependencies.py",
        'if principal.method == "api_key" and principal.use_cases and use_case != principal.use_cases[0]:',
        'if principal.method == "api_key" and principal.use_cases and False:',
        f"{ATTRIBUTION} {HARDENING}",
    ),
    Mutation(
        "A7",
        "the use-case header wins over the path selector",
        "gateway/src/aira_gateway/auth/attribution.py",
        "    if header and header.strip():",
        "    if False:",
        ATTRIBUTION,
    ),
    Mutation(
        "A8",
        "a use-case selector must match the Management slug charset",
        "gateway/src/aira_gateway/auth/attribution.py",
        '_SLUG = re.compile(r"^[a-z0-9-]{1,64}$")',
        '_SLUG = re.compile(r"^.*$")',
        f"{HARDENING} {ATTRIBUTION}",
    ),
    Mutation(
        "A9",
        "a credential in the query string never reaches a span",
        "libs/src/aira_common/observability.py",
        '            parts.append(f"{name}={REDACTED}")',
        "            parts.append(pair)",
        f"{HARDENING} libs/tests/test_observability.py",
    ),
    Mutation(
        "A10",
        "an oversized streamed body is refused even without a declared length",
        "gateway/src/aira_gateway/middleware.py",
        "                    raise RequestTooLarge(self.max_bytes)",
        "                    pass",
        HARDENING,
    ),
    # ---- the management control plane (FRD-200/201/202/204, ADR-0007) --------------------
    Mutation(
        "G1",
        "an oversight role sees every use case, a normal user only their own",
        "management/backend/src/aira_management/rbac.py",
        "    if has_oversight_role(user):",
        "    if True:",
        MGMT_RBAC,
    ),
    Mutation(
        "G2",
        "a role removed from the token is removed in Django",
        "management/backend/src/aira_management/rbac.py",
        # Re-anchored 2026-08-15: adding and removing are one function now (`_reconcile`), because
        # both syncs ran unconditionally on every request — 17 statements and 8 writes for a plain
        # `GET`. The property is unchanged, and it is the half that is easy to lose while making
        # something cheaper: a sync that only ever adds is an access list nobody can shrink.
        "    if drop:\n        user.groups.remove(*Group.objects.filter(name__in=drop))",
        "    if False:\n        user.groups.remove(*Group.objects.filter(name__in=drop))",
        MGMT_RBAC,
    ),
    Mutation(
        "G3",
        "editing a use case needs the change permission, not mere visibility",
        "management/backend/src/aira_management/apps/usecases/access.py",
        "    return has_role(user, Role.GLOBAL_ADMIN) or user.has_perm(CHANGE, usecase)",
        "    return has_role(user, Role.GLOBAL_ADMIN) or user.has_perm(VIEW, usecase)",
        MGMT_RBAC,
    ),
    Mutation(
        "G4",
        "removing a membership revokes the permissions it granted",
        "management/backend/src/aira_management/apps/usecases/views.py",
        "            _revoke(user, usecase)\n            emit(",
        "            emit(",
        MGMT_RBAC,
    ),
    Mutation(
        "G5",
        "granting access grants the permission it promises, to a person or a group",
        "management/backend/src/aira_management/apps/usecases/views.py",
        # Re-anchored by `FRD-209`: the parameter is `holder` now, because guardian takes a user
        # **or a Django group** and that is the whole mechanism behind group grants. The selection
        # widened with it — both kinds go through this one function, so a test file covering only
        # one of them would let the other's break go unnoticed.
        "    assign_perm(_VIEW, holder, usecase)",
        "    pass",
        MGMT_RBAC + " management/backend/tests/test_group_grants.py",
    ),
    Mutation(
        "G6",
        "issuing a key needs membership, not read visibility",
        "management/backend/src/aira_management/apps/usecases/views.py",
        "        if not self._is_member(usecase):",
        "        if False:",
        MGMT_HARDENING,
    ),
    Mutation(
        "G7",
        "a subject already bound to a user is never reachable by reusing its username",
        "management/backend/src/aira_management/apps/api/authentication.py",
        "        if existing is not None and not OidcIdentity.objects.filter(user=existing).exists():",
        "        if existing is not None:",
        MGMT_HARDENING,
    ),
    Mutation(
        "G8",
        "management refuses to boot outside local with the dev secret key",
        "management/backend/src/aira_management/config/security.py",
        "    if settings.secret_key == DEV_SECRET_KEY or not settings.secret_key:",
        "    if False:",
        MGMT_SETTINGS,
    ),
    Mutation(
        "G9",
        "management refuses to boot outside local with a wildcard ALLOWED_HOSTS",
        "management/backend/src/aira_management/config/security.py",
        '    if "*" in settings.allowed_hosts_list:',
        "    if False:",
        MGMT_SETTINGS,
    ),
    Mutation(
        "G10",
        "applying a use-case event twice converges instead of duplicating",
        "gateway/src/aira_gateway/consumer/apply.py",
        '    if existing is None:\n        session.add(UseCaseRead(slug=payload["slug"], **fields))',
        '    if True:\n        session.add(UseCaseRead(slug=payload["slug"], **fields))',
        CONSUMER,
    ),
    Mutation(
        "G11",
        "applying a membership event twice updates rather than duplicating",
        "gateway/src/aira_gateway/consumer/apply.py",
        "    if member is None:",
        "    if True:",
        CONSUMER,
    ),
    # ---- money, pricing and the budget model (FRD-400/401/403) --------------------------
    Mutation(
        "B1",
        "an amount converts to nano-units exactly, never through a float",
        "libs/src/aira_common/money.py",
        "return int(value.quantize(_QUANTUM, rounding=ROUND_HALF_UP) * NANOS_PER_UNIT)",
        "return int(float(value) * NANOS_PER_UNIT)",
        MONEY,
    ),
    Mutation(
        "B2",
        "an unpriced model prices as unknown, never as free",
        "gateway/src/aira_gateway/pricing.py",
        "        price = await self.price_for(model)\n        if price is None:\n            return None",
        "        price = await self.price_for(model)\n        if price is None:\n            return 0",
        COST,
    ),
    Mutation(
        "B3",
        "a request of unknown cost is counted apart, not summed as zero",
        "gateway/src/aira_gateway/budgets/service.py",
        "    unpriced = requests if cost_nanos is None else 0",
        "    unpriced = 0",
        f"{COST} gateway/tests/test_budget_service.py",
    ),
    Mutation(
        "B4",
        "input and output tokens are priced at their own rates",
        "libs/src/aira_common/money.py",
        "        completion_tokens, output_price_per_million_nanos",
        "        completion_tokens, input_price_per_million_nanos",
        MONEY,
    ),
    Mutation(
        "B5",
        "a daily budget rolls over at the day boundary",
        "gateway/src/aira_gateway/budgets/service.py",
        'return now.strftime("%Y-%m-%d") if period == "day" else now.strftime("%Y-%m")',
        'return now.strftime("%Y-%m")',
        "gateway/tests/test_budget_service.py",
    ),
    # B6 ("a member-scoped budget binds only that member") is superseded by S1: the rule now
    # lives in one place for budgets and rate limits alike, so it has one mutation rather than
    # two that could drift apart.
    Mutation(
        "B7",
        "a disabled budget does not bind",
        "gateway/src/aira_gateway/budgets/service.py",
        "            select(BudgetRead).where(BudgetRead.use_case == use_case, BudgetRead.enabled.is_(True))",
        "            select(BudgetRead).where(BudgetRead.use_case == use_case)",
        "gateway/tests/test_budget_service.py",
    ),
    Mutation(
        "B8",
        "a model priced in only one direction is refused",
        "management/backend/src/aira_management/apps/catalog/serializers.py",
        "        if has_input != has_output:",
        "        if has_input and not has_output:",
        CATALOG,
    ),
    Mutation(
        "B9",
        "a spend limit crosses to Kafka as a decimal string, never a number",
        "management/backend/src/aira_management/apps/usecases/views.py",
        '        "limit_cost": str(budget.limit_cost) if budget.limit_cost is not None else None,',
        '        "limit_cost": budget.limit_cost if budget.limit_cost is not None else None,',
        "management/backend/tests/test_budgets.py",
    ),
    Mutation(
        "B10",
        "a model with no price arrives unpriced, not priced at zero",
        "gateway/src/aira_gateway/consumer/apply.py",
        "    return None if value is None else to_nanos(str(value))",
        "    return 0 if value is None else to_nanos(str(value))",
        f"{COST} {CONSUMER}",
    ),
    Mutation(
        "B11",
        "only a global admin may write model prices",
        "management/backend/src/aira_management/apps/catalog/views.py",
        "        return [IsAuthenticated(), MayCatalogueModels()]",
        "        return [IsAuthenticated()]",
        CATALOG,
    ),
    Mutation(
        "B12",
        "a non-zero amount is never displayed as zero",
        "libs/src/aira_common/money.py",
        "    if nanos == 0 or Decimal(rendered) != 0:",
        "    if True:",
        MONEY,
    ),
    Mutation(
        "B13",
        "the usage endpoint refuses a caller not entitled to that use case",
        "gateway/src/aira_gateway/api/usage.py",
        "    authorize_use_case(principal, use_case)",
        "    pass",
        "gateway/tests/test_budget_routes.py",
    ),
    # ---- the pre-dispatch pipeline (FRD-300/303/306, ADR-0007) --------------------------
    Mutation(
        "P1",
        "an injection filter set to block refuses the request",
        "gateway/src/aira_gateway/pipeline/engine.py",
        # Re-anchored by `FRD-125`: the decision moved into `_blocks` when the verdict became
        # three-valued, and a mutation whose anchor has moved protects nothing.
        "    if verdict is Verdict.INJECTION:\n        return True",
        "    if verdict is Verdict.INJECTION:\n        return False",
        f"{PIPELINE} {CLASSIFIERS}",
    ),
    Mutation(
        "P2",
        "an injection filter set to flag does not refuse the request",
        "gateway/src/aira_gateway/pipeline/engine.py",
        '    if config.get("action", "block") != "block":\n        return False',
        "    if False:\n        return False",
        f"{PIPELINE} {CLASSIFIERS}",
    ),
    Mutation(
        "P4",
        "the fallback chain tries the requested model first",
        "gateway/src/aira_gateway/pipeline/dispatch.py",
        "candidates = [request.model, *[m for m in fallback_models if m != request.model]]",
        "candidates = [*[m for m in fallback_models if m != request.model], request.model]",
        "gateway/tests/test_pipeline_dispatch.py",
    ),
    Mutation(
        "P5",
        "a regex with a nested quantifier is refused at authoring time (ReDoS)",
        "management/backend/src/aira_management/apps/pipelines/serializers.py",
        "    if is_catastrophic(pattern):",
        "    if is_catastrophic(pattern) and False:",
        "management/backend/tests/test_pipelines.py",
    ),
    Mutation(
        "P6",
        "the step-count bound on a pipeline config is enforced",
        "management/backend/src/aira_management/apps/pipelines/serializers.py",
        "        if len(value) > MAX_STEPS:",
        "        if len(value) > MAX_STEPS + 1000:",
        "management/backend/tests/test_pipelines.py",
    ),
    # ---- retention (FRD-404) ------------------------------------------------------------
    Mutation(
        "R1",
        "retention removes both payloads, not just the request",
        "gateway/src/aira_gateway/retention.py",
        ".values(request_payload=None, response_payload=None)",
        ".values(request_payload=None)",
        RETENTION,
    ),
    Mutation(
        "R2",
        "a second retention run clears nothing",
        "gateway/src/aira_gateway/retention.py",
        "                (RequestLog.request_payload.is_not(None))\n                | (RequestLog.response_payload.is_not(None)),",
        "                RequestLog.id.is_not(None),",
        RETENTION,
    ),
    Mutation(
        "R3",
        "unclaimed traffic follows the installation default, it is not exempt",
        "gateway/src/aira_gateway/retention.py",
        # **Re-anchored** when the same pass grew to cover slugs the read-model does not name — a
        # deleted use case's rows, which matched no pass at all and were never cleared. The call
        # gained an argument and the mutation stopped applying, which the anchor test reported as
        # STALE rather than green: exactly what `N2` is for.
        "                now - timedelta(days=self._default_retention_days),",
        "                now - timedelta(days=self._default_retention_days * 1000),",
        RETENTION,
    ),
    Mutation(
        "R4",
        "whole-row deletion stays off unless it is switched on",
        "gateway/src/aira_gateway/config.py",
        "    log_retention_days: int = 0",
        "    log_retention_days: int = 30",
        f"{RETENTION} gateway/tests/test_config.py",
    ),
    Mutation(
        "R5",
        "an absent payload is SQL NULL, not the JSON value null",
        "gateway/src/aira_gateway/db/models.py",
        "    request_payload: Mapped[dict[str, Any] | None] = mapped_column(\n"
        "        JSON(none_as_null=True), nullable=True\n"
        "    )",
        "    request_payload: Mapped[dict[str, Any] | None] = mapped_column(\n"
        "        JSON(), nullable=True\n"
        "    )",
        f"{RETENTION} gateway/tests/test_persistence_service.py",
    ),
    # ---- rate limiting -------------------------------------------------------------------
    Mutation(
        "M1",
        "a refused request debits no bucket at all",
        "gateway/src/aira_gateway/ratelimit/buckets.py",
        "            self._state[request.key] = (tokens - cost if decision.allowed else tokens, now)",
        "            self._state[request.key] = (tokens - cost, now)",
        RATELIMIT,
    ),
    Mutation(
        "M2",
        "every applicable scope is checked, not just the first",
        "gateway/src/aira_gateway/ratelimit/service.py",
        "        decision = await self._bucket.take(buckets, units)",
        "        decision = await self._bucket.take(buckets[:1], units)",
        RATELIMIT,
    ),
    Mutation(
        "M3",
        "a configured limit is actually enforced",
        "gateway/src/aira_gateway/ratelimit/service.py",
        "        if not self._enforce:\n            return",
        "        if True:\n            return",
        f"{RATELIMIT} {RATELIMIT_ROUTES}",
    ),
    Mutation(
        "M4",
        "an unset burst means the per-minute figure, not zero",
        # Re-anchored: the rule moved out of the service and into `per_minute`, which is now the
        # one reading of "n per minute as a bucket" for all three callers (a configured limit, the
        # bound on failed authentications, a throttling suspension). A mutation whose anchor has
        # moved protects nothing.
        "gateway/src/aira_gateway/ratelimit/buckets.py",
        "        capacity=burst if burst and burst > 0 else rate,",
        "        capacity=burst or 0,",
        RATELIMIT,
    ),
    Mutation(
        "M5",
        "a newly saved limit takes effect without a restart",
        "gateway/src/aira_gateway/ratelimit/service.py",
        "        if cached is not None and now < cached[0]:",
        "        if cached is not None:",
        RATELIMIT,
    ),
    Mutation(
        "M6",
        "Retry-After never invites an immediate retry",
        "gateway/src/aira_gateway/ratelimit/buckets.py",
        "        return str(max(1, math.ceil(self.retry_after_seconds)))",
        "        return str(int(self.retry_after_seconds))",
        f"{RATELIMIT} {RATELIMIT_ROUTES}",
    ),
    Mutation(
        "M7",
        "losing Redis degrades the limit, it does not remove it",
        "gateway/src/aira_gateway/ratelimit/buckets.py",
        "            return await self._local.take(requests, cost)",
        "            return ALLOWED",
        RATELIMIT,
    ),
    Mutation(
        "M26",
        "a limit switched off in Management stops binding; a missing flag does not switch it off",
        "gateway/src/aira_gateway/consumer/apply.py",
        '        "enabled": payload.get("enabled", True),\n'
        "    }\n"
        "    if record is None:\n"
        '        session.add(RateLimitRead(id=payload["id"], **fields))',
        '        "enabled": False,\n'
        "    }\n"
        "    if record is None:\n"
        '        session.add(RateLimitRead(id=payload["id"], **fields))',
        RATELIMIT,
    ),
    # ---- budget reservation --------------------------------------------------------------
    Mutation(
        "M8",
        "no exit path leaves a reservation unresolved",
        "gateway/src/aira_gateway/budgets/service.py",
        "            if not reservation.resolved:",
        "            if False:",
        f"{RATELIMIT_ROUTES} {BUDGET_ROUTES}",
    ),
    Mutation(
        "M9",
        "a cost limit is tested before the reservation is granted",
        "gateway/src/aira_gateway/budgets/ledger.py",
        "if limit_cost >= 0 and cost >= limit_cost then return 'cost' end",
        "if false then return 'cost' end",
        f"{BUDGET_RESERVATION} gateway/tests/test_cost_budgets.py",
    ),
    Mutation(
        "M10",
        "'already at the limit' refuses, rather than allowing one more",
        "gateway/src/aira_gateway/budgets/ledger.py",
        "if limit_requests >= 0 and requests >= limit_requests then return 'requests' end",
        "if limit_requests >= 0 and requests > limit_requests then return 'requests' end",
        f"{BUDGET_RESERVATION} {BUDGET_SERVICE}",
    ),
    Mutation(
        "M11",
        "a counter never goes negative and hands out free headroom",
        "gateway/src/aira_gateway/budgets/ledger.py",
        "  if tonumber(redis.call('HGET', key, fields[i])) < 0 then",
        "  if false then",
        BUDGET_RESERVATION,
    ),
    Mutation(
        "M12",
        "a counter is rebuilt from Postgres long before its period ends",
        "gateway/src/aira_gateway/budgets/ledger.py",
        "COUNTER_TTL_SECONDS = 300",
        "COUNTER_TTL_SECONDS = 40 * 24 * 3600",
        BUDGET_RESERVATION,
    ),
    Mutation(
        "M13",
        "a half-made reservation is handed back when Redis disappears mid-request",
        "gateway/src/aira_gateway/budgets/service.py",
        "                    await self.release(partial)",
        "                    pass",
        BUDGET_RESERVATION,
    ),
    Mutation(
        "M14",
        "a rebuilt counter is a reseed from Postgres, not a reset to zero",
        "gateway/src/aira_gateway/budgets/service.py",
        "                seed=Amounts(seed.tokens, seed.requests, seed.cost_nanos),",
        "                seed=Amounts(),",
        BUDGET_RESERVATION,
    ),
    Mutation(
        "M15",
        "budgets are still enforced when Redis is unreachable",
        "gateway/src/aira_gateway/budgets/service.py",
        # Re-anchored twice: once when a member rule learned to match a caller's name, and again
        # when that scope was removed (2026-08-14) and `username` went with it. The property is
        # unchanged both times — without Redis the budget is still checked, just not atomically.
        "            await self._check_only(session, budgets, now, subject)\n"
        "        return Reservation(budgets=budgets, subject=subject, atomic=False)",
        "        return Reservation(budgets=budgets, subject=subject, atomic=False)",
        f"{BUDGET_RESERVATION} {BUDGET_SERVICE}",
    ),
    Mutation(
        "M23",
        "every verb passes the pre-dispatch controls, not only the generate ones",
        "gateway/src/aira_gateway/api/serving.py",
        # Re-anchored by `FRD-126`: this step moved out of the surfaces and into the one
        # sequence that owns their order. A mutation whose anchor has moved protects nothing.
        "    reservation = await enforce_pre_dispatch(",
        "    reservation = Reservation() if embed is not None else await enforce_pre_dispatch(",
        RATELIMIT_ROUTES,
    ),
    Mutation(
        "M24",
        "the reservation uses the caller's own output bound where it gave one",
        "gateway/src/aira_gateway/api/serving.py",
        "    tokens = declaration.output_cap(max_output_tokens) or settings.budget_estimate_output_tokens",
        "    tokens = settings.budget_estimate_output_tokens",
        f"{RATELIMIT_ROUTES} gateway/tests/test_cost_budgets.py {BUDGET_ROUTES}",
    ),
    # ---- the audit log -------------------------------------------------------------------
    Mutation(
        "M16",
        "a full queue writes inline rather than dropping the row",
        "gateway/src/aira_gateway/persistence/writer.py",
        '            _log.warning("request_log_queue_full", operation=entry.operation)\n'
        "            await self._write(entry)",
        '            _log.warning("request_log_queue_full", operation=entry.operation)',
        LOG_WRITER,
    ),
    Mutation(
        "M17",
        "shutdown drains what was accepted",
        "gateway/src/aira_gateway/persistence/writer.py",
        # Re-anchored: the bare `await self._queue.join()` became a race between the drain and the
        # worker, so that a shutdown whose worker had already died writes what is left here rather
        # than waiting for a signal nobody will send. The property is the same one, and the
        # mutation removes the whole drain — which is the defect it was written for.
        "        self._stopping = True\n"
        "        drained = asyncio.create_task(self._queue.join())\n"
        "        await asyncio.wait({drained, self._worker}, return_when=asyncio.FIRST_COMPLETED)\n"
        "        if not drained.done():\n"
        "            drained.cancel()\n"
        "            with contextlib.suppress(asyncio.CancelledError):\n"
        "                await drained\n"
        "            await self._write_remaining()",
        "        self._stopping = True",
        LOG_WRITER,
    ),
    Mutation(
        "M18",
        "a row submitted during shutdown is still written",
        "gateway/src/aira_gateway/persistence/writer.py",
        "        if self._worker is None or self._stopping:",
        "        if self._worker is None:",
        LOG_WRITER,
    ),
    Mutation(
        "M19",
        "one failing write does not stop every later one",
        "gateway/src/aira_gateway/persistence/writer.py",
        "            except Exception as exc:  # a failed write must never take the worker down",
        "            except ValueError as exc:",
        LOG_WRITER,
    ),
    Mutation(
        "M20",
        "a use case that declined storage gets none",
        "gateway/src/aira_gateway/persistence/writer.py",
        "        return True if record is None else bool(record.store_payloads)",
        "        return True",
        f"{LOG_WRITER} gateway/tests/test_store_payloads.py",
    ),
    Mutation(
        "M25",
        "switching storage off purges what was already stored",
        "gateway/src/aira_gateway/retention.py",
        "            slug: (None if not store else max(1, days or self._default_retention_days))",
        "            slug: max(1, days or self._default_retention_days)",
        "gateway/tests/test_retention.py gateway/tests/test_store_payloads.py",
    ),
    Mutation(
        "M27",
        "deleting a use case revokes the keys bound to it",
        "gateway/src/aira_gateway/consumer/apply.py",
        "    await session.execute("
        "update(ApiKey).where(ApiKey.use_case == slug).values(is_active=False))",
        "    pass",
        "gateway/tests/test_consumer_apply.py",
    ),
    Mutation(
        "M28",
        "deleting a use case clears its budgets, limits, pipeline and counters",
        "gateway/src/aira_gateway/consumer/apply.py",
        "    await session.execute(delete(BudgetRead).where(BudgetRead.use_case == slug))",
        "    pass",
        "gateway/tests/test_consumer_apply.py",
    ),
    Mutation(
        "M29",
        "deleting a use case keeps its request log",
        "gateway/src/aira_gateway/consumer/apply.py",
        "    await session.execute(delete(UseCaseRead).where(UseCaseRead.slug == slug))",
        # The import is local to the mutation: without it this would fail on a NameError, and a
        # collection error counts as "caught" for entirely the wrong reason.
        "    from aira_gateway.db.models import RequestLog\n"
        "    await session.execute(delete(RequestLog).where(RequestLog.use_case == slug))\n"
        "    await session.execute(delete(UseCaseRead).where(UseCaseRead.slug == slug))",
        "gateway/tests/test_consumer_apply.py",
    ),
    Mutation(
        "S8",
        "a per-person row counts each caller separately, not everybody together",
        "gateway/src/aira_gateway/scopes.py",
        "        if scope == EACH_MEMBER and caller:\n"
        "            # The row names nobody; the **caller** is the subject. So one configured row "
        "produces a\n"
        "            # counter per person, under exactly the key a row naming that person would "
        "have used —\n"
        "            # which is why an administrator can narrow one individual later without the "
        "shared\n"
        "            # history moving to a different key.\n"
        "            return cls(use_case, caller)",
        "        if scope == EACH_MEMBER and caller:\n            return cls(use_case)",
        "gateway/tests/test_scopes.py gateway/tests/test_budget_service.py "
        "gateway/tests/test_ratelimit.py",
    ),
    Mutation(
        "S9",
        "a per-person budget is accounted under the caller, not under the row",
        "gateway/src/aira_gateway/budgets/service.py",
        # Re-anchored when the scope naming an individual was removed (2026-08-14): the fallback
        # to the row's own subject went with it, and the property — `each_member` keys on **who is
        # asking** — is what remains and what this still guards.
        "        caller=caller,",
        "        caller=None,",
        "gateway/tests/test_budget_service.py",
    ),
    Mutation(
        "S2",
        "the budget usage key keeps the shape already stored in the database",
        "gateway/src/aira_gateway/scopes.py",
        'return f"uc:{self.use_case}"',
        'return f"usecase:{self.use_case}"',
        "gateway/tests/test_scopes.py",
    ),
    Mutation(
        "S3",
        "every bucket of one use case shares a Redis Cluster slot",
        "gateway/src/aira_gateway/scopes.py",
        '        tag = f"rl:{{{self.use_case}}}"',
        '        tag = "rl" if self.member is None else f"rl:{{{self.member}}}"',
        "gateway/tests/test_scopes.py",
    ),
    Mutation(
        "S4",
        "a feature running on its fallback says so in /readyz",
        "gateway/src/aira_gateway/ratelimit/buckets.py",
        "            self._degradation.degraded(\n"
        '                self.FEATURE, "per-instance buckets; N instances allow N x the limit"\n'
        "            )",
        "            pass",
        "gateway/tests/test_ratelimit.py gateway/tests/test_health.py",
    ),
    Mutation(
        "S5",
        "a recovered store clears the feature's fallback record",
        "libs/src/aira_common/counters.py",
        "        self._degraded.pop(feature, None)",
        "        pass",
        "gateway/tests/test_ratelimit.py gateway/tests/test_health.py",
    ),
    Mutation(
        "S6",
        "a caller's degradation log is used, not silently replaced by a private one",
        "gateway/src/aira_gateway/ratelimit/buckets.py",
        "        self._degradation = degradation if degradation is not None else DegradationLog()",
        "        self._degradation = degradation or DegradationLog()",
        "gateway/tests/test_ratelimit.py",
    ),
    # ---- oversight (ADR-0009) ------------------------------------------------------------
    Mutation(
        "O1",
        "oversight is global-admin and it-steuerung, and no other role",
        "libs/src/aira_common/roles.py",
        "GOVERNANCE_ROLES: frozenset[Role] = frozenset({Role.GLOBAL_ADMIN, Role.IT_STEUERUNG})",
        # **Re-anchored 2026-08-10.** It used to add `Role.USE_CASE_ADMIN`, which left the
        # vocabulary with `ADR-0017`'s cleanup — the mutated file would not have imported, and an
        # `AttributeError` is a mutation "caught" for the wrong reason. `IT_SECURITY` is the
        # meaningful one anyway: it sees every use case and deliberately not every figure, which
        # is the distinction the tests below exist for.
        "GOVERNANCE_ROLES: frozenset[Role] = frozenset({Role.GLOBAL_ADMIN, Role.IT_STEUERUNG, Role.IT_SECURITY})",
        "libs/tests/test_roles.py management/backend/tests/test_rbac.py",
    ),
    Mutation(
        "O2",
        "a malformed groups claim yields no access rather than an error",
        # **Re-anchored 2026-08-12.** It defended the same shape one claim over: `realm_access` was
        # parsed defensively so a malformed claim conferred nothing instead of raising. `ADR-0017`
        # stopped reading realm roles and the helper was deleted on 2026-08-11 — so this anchor had
        # been pointing at a grave, which is worse than no mutation at all: it reports green about
        # nothing.
        #
        # The rule survived the move. Groups are now the single source of both roles and use-case
        # access, and `validate` narrows the claim to a list for exactly the reason the old one
        # narrowed a dict: a realm whose mapper is single-valued sends a **string**, and iterating
        # one yields characters; a numeric claim raises `TypeError` inside authentication, which
        # turns a realm misconfiguration into a 500 on every request that token makes. A
        # misconfiguration must stop *authority*, not authentication.
        # `or []` rather than a bare `raw_groups`, deliberately. The bare form is caught by
        # `test_no_groups_claim_yields_no_use_cases` — an **absent** claim is `None`, which is not
        # iterable — so it would report this property as defended while testing a different one.
        # `or []` handles absence correctly and mishandles malformation, which isolates the rule
        # this mutation is about. Verified: with it, only the malformed-claim cases fail.
        "gateway/src/aira_gateway/auth/oidc.py",
        "        groups = raw_groups if isinstance(raw_groups, list) else []",
        "        groups = raw_groups or []",
        "gateway/tests/test_auth_oidc.py",
    ),
    Mutation(
        # **Re-anchored 2026-08-09** (`ADR-0017`). It named `realm_roles(claims)`, which no longer
        # exists — so the harness replaced nothing and reported a property no test would have
        # noticed losing. The property itself is unchanged: the roles a token confers must reach
        # the principal, or every oversight decision in the data plane silently answers "no".
        "O3",
        "the roles a token confers reach the principal",
        "gateway/src/aira_gateway/auth/oidc.py",
        "            roles=roles_from_groups(",
        "            roles=(),  # was: roles_from_groups(",
        "gateway/tests/test_attribution.py gateway/tests/test_auth_oidc.py",
    ),
    Mutation(
        "D7",
        "the residency policy is one list for every cloud, defaulting to the EU",
        "gateway/src/aira_gateway/residency.py",
        "    return regions or DEFAULT_ALLOWED_REGIONS",
        "    return regions",
        REQUIREMENTS,
    ),
    Mutation(
        "D8",
        "the default policy covers Azure's EU regions, not only Google's",
        "gateway/src/aira_gateway/residency.py",
        "DEFAULT_ALLOWED_REGIONS = EU_REGIONS_GOOGLE + EU_REGIONS_AZURE",
        "DEFAULT_ALLOWED_REGIONS = EU_REGIONS_GOOGLE",
        REQUIREMENTS,
    ),
    # ---- the KIRA compatibility surface (FRD-107 Stage A) -----------------------------------
    Mutation(
        "K1",
        "a field the caller sent reaches the model rather than being dropped in the mapping",
        "gateway/src/aira_gateway/api/kira/mapping.py",
        "        thinking=thinking_of(request.thinking),",
        "        thinking=None,",
        KIRA,
    ),
    Mutation(
        "K2",
        "a response schema is forwarded rather than silently dropped",
        "gateway/src/aira_gateway/api/kira/mapping.py",
        "            parse_schema(request.response_schema, bounds)",
        "            None",
        KIRA,
    ),
    Mutation(
        "K3",
        "the error envelope is the predecessor's, not ours",
        "gateway/src/aira_gateway/api/kira/errors.py",
        '    body: dict[str, Any] = {"code": code, "message": message}',
        '    body: dict[str, Any] = {"error": {"code": code, "message": message}}',
        KIRA,
    ),
    Mutation(
        "K4",
        "an integer model id addresses a model, and an unknown one is refused",
        "gateway/src/aira_gateway/api/kira/routes.py",
        "    if name is None:",
        "    if False:",
        KIRA,
    ),
    Mutation(
        "K5",
        "the surface announces that it is transitional, on every response",
        "gateway/src/aira_gateway/api/kira/routes.py",
        '    "Deprecation": "true",',
        '    "X-Not-Deprecation": "true",',
        KIRA,
    ),
    Mutation(
        "K6",
        "a request on this surface passes the same pre-dispatch controls as any other",
        "gateway/src/aira_gateway/api/serving.py",
        # Re-anchored by `FRD-126`: this step moved out of the surfaces and into the one
        # sequence that owns their order. A mutation whose anchor has moved protects nothing.
        # `FRD-126` is what makes this one property rather than one per surface — which is the
        # change's whole claim, so the mutation now tests the claim instead of one copy of it.
        # Re-anchored again (2026-08-14) when `Prepared` gained the notices a step owes the
        # caller (`FRD-309`). The property is unchanged: one sequence, both surfaces.
        "    return Prepared(canonical, embed, fallbacks, declaration, reservation, notices)",
        "    return Prepared(canonical, embed, fallbacks, declaration, Reservation(), notices)",
        # The selection widened with the anchor. This was one surface's property and is now every
        # surface's, so testing it with one surface's tests would check a third of the claim —
        # which is exactly how it survived the first run after the move.
        f"{KIRA} {ACCOUNTING} gateway/tests/test_budget_routes.py",
    ),
    Mutation(
        "K7",
        "conversation history is placed before the current turn, oldest first",
        "gateway/src/aira_gateway/api/kira/mapping.py",
        "    parts = _parts(request.request, limits, counted)\n    messages.append(CanonicalMessage(role=Role.USER, parts=parts))",
        "    parts = _parts(request.request, limits, counted)\n    messages.insert(0, CanonicalMessage(role=Role.USER, parts=parts))",
        KIRA,
    ),
    Mutation(
        "K8",
        "a refusal on this surface reaches the audit trail like any other",
        "gateway/src/aira_gateway/api/kira/routes.py",
        '    if getattr(request.state, "attribution", None) is not None:',
        "    if False:",
        KIRA,
    ),
    # ---- documents and images (FRD-110) ----------------------------------------------------
    Mutation(
        "F1",
        "a model that cannot read the attachment is refused, never sent the prompt without it",
        "gateway/src/aira_gateway/requirements.py",
        "        if not declaration.can(Capability.ATTACHMENTS):",
        "        if False:",
        ATTACHMENTS,
    ),
    Mutation(
        "F2",
        "the media types are checked one by one, not merely 'does it do attachments'",
        "gateway/src/aira_gateway/requirements.py",
        "        unreadable = self._required - declaration.media_types",
        "        unreadable = frozenset()",
        ATTACHMENTS,
    ),
    Mutation(
        "F3",
        "the attachment requirement is applied to the request that carries one",
        "gateway/src/aira_gateway/api/serving.py",
        "    if canonical is not None and canonical.media_types:",
        "    if False:",
        ATTACHMENTS,
    ),
    Mutation(
        "F4",
        "invalid base64 is refused rather than silently truncated into a shorter document",
        "gateway/src/aira_gateway/attachments.py",
        "        return base64.b64decode(raw, validate=True)",
        "        return base64.b64decode(raw)",
        ATTACHMENTS,
    ),
    Mutation(
        "F5",
        "the media-type allow-list actually restricts",
        "gateway/src/aira_gateway/attachments.py",
        "    if media_type not in limits.media_types:",
        "    if False:",
        ATTACHMENTS,
    ),
    Mutation(
        "F6",
        "a mislabelled upload is caught by its signature",
        "gateway/src/aira_gateway/attachments.py",
        "    if not any(data.startswith(signature) for signature in signatures):",
        "    if False:",
        ATTACHMENTS,
    ),
    Mutation(
        "F7",
        "attachment bytes never reach the audit table",
        "gateway/src/aira_gateway/persistence/writer.py",
        "                stripped: dict[str, Any] = strip_attachments(payload)",
        "                stripped: dict[str, Any] = payload",
        ATTACHMENTS,
    ),
    Mutation(
        "F8",
        "the reservation counts the attachment rather than treating it as free",
        "gateway/src/aira_gateway/api/serving.py",
        "    tokens += declaration.attachment_tokens(attachments or [])",
        "    tokens += 0",
        ATTACHMENTS,
    ),
    Mutation(
        "F9",
        "the text view of a message excludes attachments, which is the pipeline's stated blind spot",
        "gateway/src/aira_gateway/core/canonical.py",
        '        return "".join(part.text for part in self.parts if isinstance(part, TextPart))',
        '        return "".join(getattr(part, "text", str(part)) for part in self.parts)',
        ATTACHMENTS,
    ),
    Mutation(
        "F10",
        "part order is preserved, because it changes the prompt",
        "gateway/src/aira_gateway/api/gemini/mapping.py",
        "            parts.append(TextPart(text=part.text))\n            continue",
        "            parts.insert(0, TextPart(text=part.text))\n            continue",
        ATTACHMENTS,
    ),
    Mutation(
        "F11",
        "embedding refuses an attachment rather than embedding the prompt without it",
        "gateway/src/aira_gateway/api/gemini/mapping.py",
        "        if any(part.inlineData is not None for part in entry.content.parts):",
        "        if False:",
        ATTACHMENTS,
    ),
    # ---- the dispatch chain may not degrade silently (ADR-0012 §3) -------------------------
    Mutation(
        "D1",
        "a candidate that fails a condition is skipped, never used anyway",
        "gateway/src/aira_gateway/pipeline/dispatch.py",
        "            if refusal is not None:\n                skipped.append(Skipped(model, refusal))\n                continue",
        "            if refusal is not None:\n                skipped.append(Skipped(model, refusal))",
        REQUIREMENTS,
    ),
    Mutation(
        "D2",
        "a model outside the permitted regions is refused",
        "gateway/src/aira_gateway/requirements.py",
        "        if described.region not in self._allowed:",
        "        if False:",
        REQUIREMENTS,
    ),
    Mutation(
        "D3",
        "an exhausted chain is a precondition failure, not an upstream outage",
        "gateway/src/aira_gateway/pipeline/dispatch.py",
        "    raise NoCapableModel(skipped)",
        '    raise UpstreamError("No provider available.")',
        REQUIREMENTS,
    ),
    Mutation(
        "D4",
        "an upstream that was tried and failed is still reported as an outage",
        "gateway/src/aira_gateway/pipeline/dispatch.py",
        "    if last_error is not None:\n        raise last_error",
        "    if False:\n        raise last_error",
        f"{REQUIREMENTS} gateway/tests/test_pipeline_dispatch.py",
    ),
    Mutation(
        "D5",
        "a model no provider serves is named in the failure rather than silently passed over",
        "gateway/src/aira_gateway/pipeline/dispatch.py",
        '            skipped.append(Skipped(model, "no provider serves this model"))',
        "            pass",
        REQUIREMENTS,
    ),
    Mutation(
        "D6",
        "the candidates a chain passed over reach the audit trail",
        "gateway/src/aira_gateway/api/gemini/routes.py",
        "                trail.passed_over(dispatched.skipped)",
        "                pass",
        REQUIREMENTS,
    ),
    # ---- Vertex EU and the second dialect (FRD-115, FRD-119) -------------------------------
    Mutation(
        "V1",
        "residency is enforced: a model outside the allowed regions refuses to start",
        "gateway/src/aira_gateway/residency.py",
        "    if region not in allowed:",
        "    if False:",
        VERTEX,
    ),
    Mutation(
        "V2",
        "the EU multi-region has its own host, not a region-prefixed one",
        "gateway/src/aira_gateway/upstreams/vertex/transport.py",
        '_MULTI_REGION_HOSTS = {"eu": "aiplatform.eu.rep.googleapis.com"}',
        "_MULTI_REGION_HOSTS: dict[str, str] = {}",
        VERTEX,
    ),
    Mutation(
        "V3",
        "an ambiguous routing table refuses to start rather than silently picking one",
        "gateway/src/aira_gateway/upstreams/base.py",
        "                if model.name in self._by_model:",
        "                if False:",
        VERTEX,
    ),
    Mutation(
        "V4",
        "the model's reasoning never reaches the caller",
        "gateway/src/aira_gateway/upstreams/vertex/anthropic_mapping.py",
        '_ANSWER_BLOCKS = frozenset({"text"})',
        '_ANSWER_BLOCKS = frozenset({"text", "thinking"})',
        VERTEX,
    ),
    Mutation(
        "V5",
        "several system messages are concatenated rather than reduced to the last",
        "gateway/src/aira_gateway/upstreams/vertex/anthropic_mapping.py",
        '        joined = "\\n\\n".join(system_parts)',
        "        joined = system_parts[-1]",
        VERTEX,
    ),
    Mutation(
        "V6",
        "streamed usage is accumulated across events, not replaced by the last one",
        "gateway/src/aira_gateway/upstreams/vertex/anthropic_mapping.py",
        "            self._prompt += usage.prompt_tokens\n            self._completion += usage.completion_tokens",
        "            self._prompt = usage.prompt_tokens\n            self._completion = usage.completion_tokens",
        VERTEX,
    ),
    Mutation(
        "V7",
        "cache tokens are counted as input rather than dropped from the bill",
        "gateway/src/aira_gateway/upstreams/vertex/anthropic_mapping.py",
        '        prompt_tokens=int(usage.get("input_tokens", 0) or 0) + cached + created,',
        '        prompt_tokens=int(usage.get("input_tokens", 0) or 0),',
        VERTEX,
    ),
    Mutation(
        "V8",
        "an upstream status is preserved so the route can pass 429/503/504 through",
        "gateway/src/aira_gateway/upstreams/vertex/transport.py",
        '            f"Vertex upstream returned {response.status_code}.", response.status_code',
        '            f"Vertex upstream returned {response.status_code}."',
        VERTEX,
    ),
    Mutation(
        "V9",
        "a token is refreshed before it expires, not once it has",
        "libs/src/aira_common/tokens.py",
        "REFRESH_AT = 0.8",
        "REFRESH_AT = 1.0",
        TOKENS,
    ),
    Mutation(
        "V10",
        "concurrent callers produce one fetch, not one each",
        "libs/src/aira_common/tokens.py",
        "        async with self._lock:",
        "        if True:",
        TOKENS,
    ),
    Mutation(
        "V11",
        "a failed refresh keeps serving a token that is still valid",
        "libs/src/aira_common/tokens.py",
        "                if current is not None and current.usable(now):\n                    return current.value",
        "                if False:\n                    return current.value",
        TOKENS,
    ),
    Mutation(
        "V12",
        "a credential never appears in the error that reports it unusable",
        "gateway/src/aira_gateway/upstreams/vertex/auth.py",
        "                f\"Service-account credentials are missing: {', '.join(missing)}.\"",
        '                f"Service-account credentials are missing from {data}."',
        VERTEX,
    ),
    # ---- the model catalog as a runtime authority (FRD-114) --------------------------------
    Mutation(
        "C1",
        "an undeclared model gets the baseline and nothing more — absence is not permission",
        "gateway/src/aira_gateway/catalog.py",
        "        capabilities=capabilities if declared else BASELINE_CAPABILITIES,",
        "        capabilities=capabilities if declared else frozenset(Capability),",
        MODEL_CATALOG,
    ),
    Mutation(
        "C2",
        "a model absent from the catalog still serves the baseline, so nothing regresses",
        "gateway/src/aira_gateway/catalog.py",
        # Disambiguated 2026-08-12. The anchor matched **two** returns and the harness edits the
        # first, so this was reporting on the *un-lookupable name* branch — a different property
        # ("a name no row could hold answers 404 rather than 500") — while the branch it names,
        # `record is None`, went undefended. Anchored on the `if` as well, which only the intended
        # one carries.
        "        if record is None:\n            return ModelDeclaration(name=model)",
        "        if record is None:\n"
        "            return ModelDeclaration(name=model, capabilities=frozenset())",
        MODEL_CATALOG,
    ),
    Mutation(
        "C3",
        "a request above the declared output cap is refused rather than passed upstream",
        "gateway/src/aira_gateway/api/serving.py",
        "    if requested is not None and cap is not None and requested > cap:",
        "    if requested is not None and cap is not None and False:",
        MODEL_CATALOG,
    ),
    Mutation(
        "C4",
        "a model that declares no embedding refuses one before dispatch",
        "gateway/src/aira_gateway/embedding.py",
        "    if not declaration.can(Capability.EMBED):",
        "    if False:",
        f"{MODEL_CATALOG} {EMBEDDING}",
    ),
    Mutation(
        "C5",
        "deprecation warns and does not block, so a retirement can be announced first",
        "gateway/src/aira_gateway/api/serving.py",
        "    if not declaration.deprecated:\n        return {}",
        "    if declaration.deprecated or True:\n        return {}",
        MODEL_CATALOG,
    ),
    Mutation(
        "C6",
        "a thinking budget at or above the output cap is refused where it is written",
        "management/backend/src/aira_management/apps/catalog/validation.py",
        "    if maximum is not None and max_output_tokens is not None and maximum >= max_output_tokens:",
        "    if maximum is not None and max_output_tokens is not None and False:",
        CATALOG_DECLARATION,
    ),
    Mutation(
        "C7",
        "a partial update is validated against what the row already holds",
        "management/backend/src/aira_management/apps/catalog/serializers.py",
        "            field: attrs.get(field, getattr(self.instance, field, empty.get(field)))",
        "            field: attrs.get(field, empty.get(field))",
        CATALOG_DECLARATION,
    ),
    Mutation(
        "C9",
        "a model catalogued without a KIRA id is given one, not left unaddressable",
        "management/backend/src/aira_management/apps/catalog/serializers.py",
        '        if validated_data.get("numeric_id") is None:',
        "        if False:",
        CATALOG,
    ),
    Mutation(
        "C10",
        "an auto-assigned KIRA id climbs past every id already taken",
        "management/backend/src/aira_management/apps/catalog/serializers.py",
        '            highest = Model.objects.aggregate(top=Max("numeric_id"))["top"]',
        "            highest = None",
        CATALOG,
    ),
    Mutation(
        "C11",
        "a KIRA id already in use is refused, and the other model is named",
        "management/backend/src/aira_management/apps/catalog/serializers.py",
        "        other = clash.first()",
        "        other = None",
        CATALOG,
    ),
    Mutation(
        "C8",
        "an older event applies its prices without erasing a declaration",
        "gateway/src/aira_gateway/consumer/apply.py",
        "        if field in payload:\n            fields[field] = payload[field] if payload[field] is not None else default",
        "        fields[field] = payload.get(field) if payload.get(field) is not None else default",
        MODEL_CATALOG,
    ),
    # ---- the audit trail (FRD-122) ---------------------------------------------------------
    Mutation(
        "T1",
        "a refused request still leaves a record — a control with no trace cannot be reviewed",
        "gateway/src/aira_gateway/api/gemini/routes.py",
        "        await _write_refusal(request, trail, exc, status=status, started=started)",
        "        pass",
        "gateway/tests/test_audit_completeness.py",
    ),
    Mutation(
        "T2",
        "the audit never turns a correct refusal into a server error",
        "gateway/src/aira_gateway/api/gemini/routes.py",
        "    try:\n        await _write_refusal(request, trail, exc, status=status, started=started)\n    except Exception:",
        "    if True:\n        await _write_refusal(request, trail, exc, status=status, started=started)\n    if False:",
        "gateway/tests/test_audit_completeness.py",
    ),
    Mutation(
        "T3",
        "the row records what the caller asked for, not only what answered",
        "gateway/src/aira_gateway/audit.py",
        "        return self.model or self.requested_model",
        "        return self.requested_model",
        "gateway/tests/test_audit_completeness.py",
    ),
    Mutation(
        "T4",
        "a fallback answer is marked as a substitution rather than as a direct hit",
        "gateway/src/aira_gateway/audit.py",
        "        if candidate_index > 0:\n            self.selection = fallback_selection(candidate_index)",
        "        if candidate_index > 99:\n            self.selection = fallback_selection(candidate_index)",
        "gateway/tests/test_audit_completeness.py",
    ),
    Mutation(
        "T5",
        "only allow-listed decision fields are persisted, never the classifier's reasoning",
        "gateway/src/aira_gateway/audit.py",
        "        {key: value for key, value in decision.items() if key in SAFE_DECISION_KEYS}",
        "        dict(decision)",
        "gateway/tests/test_audit_completeness.py",
    ),
    Mutation(
        "T6",
        "the calling system is identified by its own credential, not by whoever issued it",
        "gateway/src/aira_gateway/auth/service.py",
        "            credential=record.prefix,",
        "            credential=None,",
        "gateway/tests/test_audit_completeness.py",
    ),
    Mutation(
        "T7",
        "a request handled while a control was degraded records that it was",
        "gateway/src/aira_gateway/persistence/recorder.py",
        "    return None if degradation is None else dict(degradation.features)",
        "    return None",
        "gateway/tests/test_audit_completeness.py",
    ),
    Mutation(
        "T8",
        "the decisions of a blocked pipeline survive the exception that blocked it",
        "gateway/src/aira_gateway/pipeline/engine.py",
        "            decisions=decisions if decisions is not None else [],",
        "            decisions=[],",
        "gateway/tests/test_audit_completeness.py",
    ),
    # ---- reporting (FRD-601) --------------------------------------------------------------
    Mutation(
        "N1",
        "a caller without oversight is scoped to their own use cases, never to everything",
        "gateway/src/aira_gateway/api/reporting.py",
        "    return principal.use_cases",
        "    return None",
        "gateway/tests/test_reporting.py",
    ),
    Mutation(
        # Re-anchored 2026-08-08: the predicate became `is_oversight` when IT Security turned out
        # to be reading an empty console. The property is unchanged — a role that sees everything
        # must not be narrowed to its own memberships — so this is re-anchored, not removed.
        "N2",
        "oversight is what grants the view across use cases, not merely being authenticated",
        "gateway/src/aira_gateway/api/reporting.py",
        "    if principal.is_oversight:\n        return None",
        "    if principal.is_oversight:\n        return principal.use_cases",
        "gateway/tests/test_reporting.py gateway/tests/test_traces.py",
    ),
    Mutation(
        "N3",
        "unpriced traffic is counted apart, never summed into spend as zero",
        "gateway/src/aira_gateway/reporting/service.py",
        "                    (RequestLog.cost_nanos.is_(None))",
        "                    (False)",
        "gateway/tests/test_reporting.py",
    ),
    Mutation(
        "N4",
        "the reporting window is half-open, so a request belongs to exactly one period",
        "gateway/src/aira_gateway/reporting/service.py",
        "RequestLog.created_at >= start, RequestLog.created_at < end",
        "RequestLog.created_at >= start, RequestLog.created_at <= end",
        "gateway/tests/test_reporting.py",
    ),
    Mutation(
        "N5",
        "a failed request is reported rather than filtered out",
        "gateway/src/aira_gateway/reporting/service.py",
        'func.sum(case((RequestLog.status >= 400, 1), else_=0)).label("failed_requests")',
        'func.sum(case((RequestLog.status >= 600, 1), else_=0)).label("failed_requests")',
        "gateway/tests/test_reporting.py",
    ),
    Mutation(
        "N6",
        "a reporting window is bounded, so a mistyped year is an error not a full scan",
        "gateway/src/aira_gateway/api/reporting.py",
        "MAX_WINDOW_DAYS = 366",
        "MAX_WINDOW_DAYS = 3_660_000",
        "gateway/tests/test_reporting.py",
    ),
    # ---- the counter transport -----------------------------------------------------------
    Mutation(
        "M21",
        "the circuit breaker reopens, so a recovered Redis is used again",
        "libs/src/aira_common/counters.py",
        "        self._unavailable_until = 0.0\n        return result",
        "        return result",
        f"{COUNTERS} {RATELIMIT}",
    ),
    Mutation(
        "M22",
        "an unreachable store reports unavailability rather than leaking a driver error",
        "libs/src/aira_common/counters.py",
        "            raise CountersUnavailable(str(exc)) from exc",
        "            raise",
        COUNTERS,
    ),
    # ---- thinking (FRD-111) -----------------------------------------------------------------
    #
    # The expensive knob on a request: budgets reach 32 768 tokens, billed as output. Three of
    # these are about *money* rather than correctness, which is why they are here at all.
    Mutation(
        "T5a",
        "a thinking budget below the model's minimum is refused",
        "gateway/src/aira_gateway/thinking.py",
        "    if minimum is not None and tokens < minimum:",
        "    if minimum is not None and tokens < 0:",
        THINKING,
    ),
    Mutation(
        "T6a",
        "a thinking budget above the model's maximum is refused",
        "gateway/src/aira_gateway/thinking.py",
        "    if maximum is not None and tokens > maximum:",
        "    if maximum is not None and tokens > maximum * 1000:",
        THINKING,
    ),
    Mutation(
        "T7a",
        "a model's declared default thinking is applied when the caller sends none",
        "gateway/src/aira_gateway/thinking.py",
        "    if requested is None:\n        return _default_for(declaration)",
        "    if requested is None:\n        return None",
        THINKING,
    ),
    Mutation(
        "T8a",
        "the pre-dispatch reservation includes the resolved thinking budget",
        "gateway/src/aira_gateway/thinking.py",
        "    return setting.tokens or 0",
        "    return 0",
        THINKING,
    ),
    Mutation(
        "T9",
        "an abstract level is translated by the model's own level table, not by a constant",
        "gateway/src/aira_gateway/thinking.py",
        "        mode=setting.mode, tokens=declaration.thinking_level_tokens(setting.mode) or maximum",
        "        mode=setting.mode, tokens=maximum",
        THINKING,
    ),
    Mutation(
        "T10",
        "a fallback candidate that cannot honour the thinking is skipped, not served",
        "gateway/src/aira_gateway/thinking.py",
        "    if not declaration.can(Capability.THINKING) or setting.mode not in declaration.thinking_modes:",  # noqa: E501
        "    if False:",
        f"{THINKING} {SERVING_OPTIONS}",
    ),
    # ---- structured output (FRD-112) ---------------------------------------------------------
    Mutation(
        "S1a",
        "an unknown schema field is refused rather than dropped",
        "gateway/src/aira_gateway/core/schema.py",
        'model_config = ConfigDict(populate_by_name=True, extra="forbid")',
        'model_config = ConfigDict(populate_by_name=True, extra="ignore")',
        RESPONSE_SCHEMA,
    ),
    Mutation(
        "S2a",
        "the schema's nesting depth is bounded",
        "gateway/src/aira_gateway/core/schema.py",
        "    if depth > bounds.max_depth:",
        "    if depth > bounds.max_depth * 1000:",
        RESPONSE_SCHEMA,
    ),
    Mutation(
        "S3a",
        "the schema's total property count is bounded across the whole tree",
        "gateway/src/aira_gateway/core/schema.py",
        "    if properties > bounds.max_properties:",
        "    if properties > bounds.max_properties * 1000:",
        RESPONSE_SCHEMA,
    ),
    Mutation(
        "S4a",
        "the capability is checked against the model dispatched to, not the one requested",
        "gateway/src/aira_gateway/requirements.py",
        "        if declaration.can(Capability.STRUCTURED_OUTPUT):\n            return None",
        "        if True:\n            return None",
        SERVING_OPTIONS,
    ),
    Mutation(
        "S5a",
        "a schema always travels with the media type that makes the provider honour it",
        "gateway/src/aira_gateway/upstreams/gemini_mapping.py",
        '        generation_config["responseMimeType"] = "application/json"',
        '        generation_config.pop("responseMimeType", None)',
        SERVING_OPTIONS,
    ),
    Mutation(
        "S6a",
        "an incomplete document is refused rather than returned as data",
        "gateway/src/aira_gateway/api/serving.py",
        '    if canonical.response_schema is None or response.finish_reason == "stop":',
        "    if True:",
        SERVING_OPTIONS,
    ),
    Mutation(
        "S7",
        "an Anthropic model that answered in prose has not satisfied the schema",
        "gateway/src/aira_gateway/upstreams/vertex/anthropic_mapping.py",
        "        if document is None:",
        "        if False:",
        VERTEX,
    ),
    # ---- embedding options (FRD-113) ---------------------------------------------------------
    #
    # E1 is the control bypass: a batch admitted as one request turns a limit of 10 per minute
    # into 5 000 texts per minute. Intact on paper, gone in practice.
    Mutation(
        "E1",
        "a batch of n weighs n against the rate limit, not one",
        "gateway/src/aira_gateway/api/serving.py",
        # Re-anchored twice: once when the call was reformatted and the raw `app.state` read
        # became the typed accessor, and again when the second argument became the **person**
        # rather than the subject (`ADR-0019`). `units` is still the whole property, and it is
        # still the argument this replaces.
        "    await rate_limits.check(\n        use_case,\n        caller,\n        units,",
        "    await rate_limits.check(\n        use_case,\n        caller,\n        1,",
        EMBEDDING,
    ),
    Mutation(
        "E2",
        "the bucket debits what the request weighs",
        "gateway/src/aira_gateway/ratelimit/buckets.py",
        "            if tokens < cost and decision.allowed:",
        "            if tokens < 1 and decision.allowed:",
        EMBEDDING,
    ),
    Mutation(
        "E3",
        "a batch is booked against the budget as the many requests it is",
        "gateway/src/aira_gateway/api/serving.py",
        "    return Amounts(tokens=tokens, requests=units, cost_nanos=cost)",
        "    return Amounts(tokens=tokens, requests=1, cost_nanos=cost)",
        EMBEDDING,
    ),
    Mutation(
        "E4",
        "a model that does not declare batch support refuses a list",
        "gateway/src/aira_gateway/embedding.py",
        "    if len(texts) > 1 and not declaration.supports_batch:",
        "    if False:",
        EMBEDDING,
    ),
    Mutation(
        "E5",
        "a task type the model does not declare is refused rather than passed through",
        "gateway/src/aira_gateway/embedding.py",
        "    if normalised not in declared:",
        "    if False:",
        EMBEDDING,
    ),
    Mutation(
        "E6",
        "a compatibility default is applied only where the model declares it",
        "gateway/src/aira_gateway/embedding.py",
        "        return default if default is not None and default in declared else None",
        "        return default",
        EMBEDDING,
    ),
    Mutation(
        "E7",
        "every embedding verb is checked for the embedding capability, not the generation one",
        "gateway/src/aira_gateway/api/serving.py",
        "    if method not in EMBEDDING_METHODS and not declaration.can(Capability.GENERATE):",
        '    if method != "embedContent" and not declaration.can(Capability.GENERATE):',
        SERVING_OPTIONS,
    ),
    Mutation(
        "E8",
        "a batch that cannot fit the bucket at all is refused rather than told to retry forever",
        "gateway/src/aira_gateway/ratelimit/service.py",
        "            if units > bucket.capacity:",
        "            if False:",
        f"{RATELIMIT} {EMBEDDING}",
    ),
    Mutation(
        "B8a",
        "the usage counter is accumulated by the database, not read-modify-written in Python",
        "gateway/src/aira_gateway/budgets/service.py",
        '            "tokens": columns.tokens + tokens,',
        '            "tokens": tokens,',
        f"{BUDGET_SERVICE} {COST}",
    ),
    # ---- the third dialect (FRD-123) ---------------------------------------------------------
    #
    # O1 is a defect that reached a running system: it was correct while Google was the only
    # vendor and wrong the moment a model name contained a colon.
    Mutation(
        "O1a",
        "the verb is split off the *last* colon, so a model name may contain one",
        "gateway/src/aira_gateway/api/gemini/routes.py",
        '    model, separator, method = resource.rpartition(":")',
        '    model, separator, method = resource.partition(":")',
        OPENAI_DIALECT,
    ),
    Mutation(
        "O2a",
        "a streamed request asks for the usage the vendor otherwise never reports",
        "gateway/src/aira_gateway/upstreams/openai/mapping.py",
        '        body["stream_options"] = {"include_usage": True}',
        "        pass",
        OPENAI_DIALECT,
    ),
    Mutation(
        "O3a",
        "usage in a chunk with no choices is read rather than dropped",
        "gateway/src/aira_gateway/upstreams/openai/mapping.py",
        '        return CanonicalChunk(text_delta="", finish_reason=None, usage=usage) if usage else None',  # noqa: E501
        "        return None",
        OPENAI_DIALECT,
    ),
    Mutation(
        "O4",
        "a token budget is refused rather than rounded to an effort level",
        "gateway/src/aira_gateway/upstreams/openai/mapping.py",
        "    if setting.mode is ThinkingMode.LIMITED:",
        "    if False:",
        OPENAI_DIALECT,
    ),
    Mutation(
        "O5",
        "a document is refused rather than sent to a dialect that carries only images",
        "gateway/src/aira_gateway/upstreams/openai/mapping.py",
        '        if not part.media_type.startswith("image/"):',
        "        if False:",
        OPENAI_DIALECT,
    ),
    Mutation(
        "O6",
        "vectors are returned in the order submitted, not the order received",
        "gateway/src/aira_gateway/upstreams/openai/mapping.py",
        '    entries.sort(key=lambda entry: int(entry.get("index", 0)))',
        "    pass",
        OPENAI_DIALECT,
    ),
    Mutation(
        "O7",
        "two servers under one name refuse to start",
        "gateway/src/aira_gateway/upstreams/openai/__init__.py",
        "        if name in seen:",
        "        if False:",
        OPENAI_DIALECT,
    ),
    Mutation(
        "O8",
        "a server declaring no models refuses to start",
        "gateway/src/aira_gateway/upstreams/openai/__init__.py",
        "        if not server.serves_anything:",
        "        if False:",
        OPENAI_DIALECT,
    ),
    # ---- the third platform (FRD-120) ---------------------------------------------------------
    #
    # F1 is the one with money in it: a deployment name has no price, and unpriced traffic is
    # counted apart rather than as zero — so getting it wrong would not fail, the spend figure
    # would quietly stop being complete.
    Mutation(
        "F1a",
        "a response is attributed to the model the caller named, not to the deployment",
        "gateway/src/aira_gateway/upstreams/foundry/routes.py",
        "        return None",
        "        return model",
        f"{FOUNDRY} {OPENAI_DIALECT}",
    ),
    Mutation(
        "F2a",
        "a model with no deployment is named, rather than reaching a 404 that reads as missing",
        "gateway/src/aira_gateway/upstreams/foundry/routes.py",
        "            raise UnknownDeployment(",
        "            return model  # noqa\n        if False:\n            raise UnknownDeployment(",
        FOUNDRY,
    ),
    Mutation(
        "F3a",
        "the Azure credential goes in its own header, not in Authorization",
        "gateway/src/aira_gateway/upstreams/foundry/__init__.py",
        '            return {"api-key": self._azure_key}',
        '            return {"Authorization": f"Bearer {self._azure_key}"}',
        FOUNDRY,
    ),
    Mutation(
        "F4a",
        "an endpoint configured without a credential refuses to start",
        "gateway/src/aira_gateway/upstreams/foundry/__init__.py",
        "    if not settings.foundry_api_key:",
        "    if False:",
        FOUNDRY,
    ),
    Mutation(
        "F5a",
        "deployments in two regions become two adapters, so provenance is not flattened",
        "gateway/src/aira_gateway/upstreams/foundry/__init__.py",
        "        by_region.setdefault(entry.region, []).append(entry)",
        '        by_region.setdefault("", []).append(entry)',
        FOUNDRY,
    ),
    Mutation(
        "F6a",
        "a declared region is checked against the one allow-list every cloud shares",
        "gateway/src/aira_gateway/upstreams/foundry/__init__.py",
        "            check_region(entry.region, allowed)",
        "            pass",
        FOUNDRY,
    ),
    # ---- secrets (FRD-116) ---------------------------------------------------------------------
    #
    # V1 is the whole feature. Falling back to the environment turns a broken secret store into a
    # service that starts, looks healthy, and runs on a stale value — the failure `ADR-0007`
    # established the principle against, extended to every credential.
    Mutation(
        "V1a",
        "a configured Vault that cannot be read stops the process rather than falling back",
        "libs/src/aira_common/secrets.py",
        '            raise VaultUnavailable(\n                f"Vault at {self._config.address} is unreachable ({type(exc).__name__})."\n            ) from exc\n\n        if response.status_code != httpx.codes.OK:',  # noqa: E501
        '            return ""\n\n        if False:',
        SECRETS,
    ),
    Mutation(
        "V2a",
        "a Vault value wins over the environment",
        "libs/src/aira_common/secrets.py",
        '        merged[name if name.startswith(env_prefix) else f"{env_prefix}{name}"] = value',
        "        merged.setdefault(name, value)",
        SECRETS,
    ),
    Mutation(
        "V3a",
        "a key present but spelled differently is still found",
        "libs/src/aira_common/secrets.py",
        "        name = key.strip().upper()",
        "        name = key",
        SECRETS,
    ),
    Mutation(
        "V4a",
        "a null value is absent rather than the string 'None'",
        "libs/src/aira_common/secrets.py",
        "        return {str(key): str(value) for key, value in data.items() if value is not None}",
        "        return {str(key): str(value) for key, value in data.items()}",
        SECRETS,
    ),
    Mutation(
        "V5a",
        "a secret-id file that cannot be read is named rather than falling through",
        "libs/src/aira_common/secrets.py",
        "            raise VaultUnavailable(\n                f\"{SECRET_ID_FILE_ENV} points at '{path}', which cannot be read \"",  # noqa: E501
        '            return "", ""\n        if False:\n            raise VaultUnavailable(\n                f"{SECRET_ID_FILE_ENV} points at \'{path}\', which cannot be read "',  # noqa: E501
        SECRETS,
    ),
    Mutation(
        "V6a",
        "authenticating with no secret-id at all is refused",
        "libs/src/aira_common/secrets.py",
        "        if not secret_id:",
        "        if False:",
        SECRETS,
    ),
    # ---- diagnostics (FRD-117) -----------------------------------------------------------------
    #
    # D1 is the one that keeps a health check from being able to take down a healthy service.
    Mutation(
        "D1a",
        "an unreachable upstream degrades readiness rather than failing it",
        "gateway/src/aira_gateway/routes/health.py",
        # Re-anchored 2026-08-08: the expression moved out of the response literal when `/readyz`
        # learned to answer probes with the verdict only (`ADR-0015`). A mutation whose anchor has
        # moved protects nothing, which is why the harness reports STALE rather than "caught".
        "    degraded = not counters_ok or bool(fallbacks) or bool(probe and probe.degraded)",
        "    degraded = not counters_ok or bool(fallbacks)",
        DIAGNOSTICS,
    ),
    Mutation(
        "D2a",
        "an adapter with no cheap probe is reported unprobed, never as healthy",
        "gateway/src/aira_gateway/diagnostics.py",
        '            return Verdict(name, True, "no probe available; not checked", self._now(), probed=False)',  # noqa: E501
        '            return Verdict(name, True, "reachable", self._now())',
        DIAGNOSTICS,
    ),
    Mutation(
        "D3a",
        "a verdict that has aged out is reported as stale rather than as fine",
        "gateway/src/aira_gateway/diagnostics.py",
        '            "stale": age > stale_after,',
        '            "stale": False,',
        DIAGNOSTICS,
    ),
    Mutation(
        "D4a",
        "a stale verdict counts as degraded, so a dead prober is visible",
        "gateway/src/aira_gateway/diagnostics.py",
        "            not verdict.ok or (verdict.probed and (now - verdict.at) > self.stale_after)",
        "            not verdict.ok",
        DIAGNOSTICS,
    ),
    Mutation(
        "D5a",
        "a wildcard CORS origin with credentials refuses to start",
        "gateway/src/aira_gateway/app.py",
        '    if "*" in origins and settings.cors_allow_credentials:',
        "    if False:",
        DIAGNOSTICS,
    ),
    Mutation(
        "D6a",
        "an upstream that never answers becomes a red verdict rather than hanging the probe",
        "gateway/src/aira_gateway/diagnostics.py",
        "            detail = await asyncio.wait_for(ping(), timeout=self.timeout)",
        "            detail = await ping()",
        DIAGNOSTICS,
    ),
    # ---- export (FRD-602) ----------------------------------------------------------------------
    #
    # E9 is the security one: an export that returned more than the screen would be a governance
    # failure delivered as a file — forwarded, saved, and impossible to recall.
    Mutation(
        "E9",
        "the export is rendered from the scoped report, not from an unscoped second query",
        "gateway/src/aira_gateway/api/reporting.py",
        "    report = await service.report(scope, window_start, window_end)",
        "    report = await service.report(None, window_start, window_end)",
        f"{CSV_EXPORT} gateway/tests/test_reporting.py",
    ),
    Mutation(
        "E10",
        "a format this endpoint does not serve is refused rather than answered in another",
        "gateway/src/aira_gateway/api/reporting.py",
        "    raise GeminiHTTPError(\n        406,",
        '    return "json"\n    raise GeminiHTTPError(\n        406,',
        CSV_EXPORT,
    ),
    Mutation(
        "E11",
        "unpriced traffic keeps its caveat in the file, not only on the screen",
        "gateway/src/aira_gateway/reporting/csv_export.py",
        "    if unpriced:",
        "    if False:",
        CSV_EXPORT,
    ),
    Mutation(
        "E12",
        "the file carries a byte-order mark, so Excel reads it as UTF-8",
        "gateway/src/aira_gateway/reporting/csv_export.py",
        "    return BOM + buffer.getvalue()",
        "    return buffer.getvalue()",
        CSV_EXPORT,
    ),
    Mutation(
        "E13",
        "an unknown breakdown is named rather than quietly rendering one of the others",
        "gateway/src/aira_gateway/reporting/csv_export.py",
        "    if breakdown not in BREAKDOWNS:",
        "    if False:",
        CSV_EXPORT,
    ),
    # ---- what an edge-case sweep against the running API found (FRD-123) ---------------------
    #
    # All four reached a deployed gateway. None was visible to a suite that only sends requests it
    # already believes in.
    Mutation(
        "X1",
        "a request that asks nothing is refused rather than billed for an answer to nothing",
        "gateway/src/aira_gateway/api/serving.py",
        "    if canonical.is_empty:",
        "    if False:",
        f"{EDGE} {KIRA}",
    ),
    Mutation(
        "X2",
        "a non-positive output cap is refused, not silently applied as a slice",
        "gateway/src/aira_gateway/api/serving.py",
        "    if requested is not None and requested <= 0:",
        "    if False:",
        f"{EDGE} {MODEL_CATALOG}",
    ),
    Mutation(
        "X4",
        "a shared control's refusal is rendered in the surface's own vocabulary, not as a 500",
        "gateway/src/aira_gateway/api/kira/routes.py",
        "    elif isinstance(exc, GeminiHTTPError):",
        "    elif False:",
        f"{EDGE} {KIRA}",
    ),
    # -- FRD-124: nothing a request asks for is accepted and thrown away ------------------------
    #
    # The whole family came from one live run: eleven of twelve legitimately-sendable fields were
    # answered 200 and ignored. Each mutation below restores one of those silences.
    Mutation(
        "Y1",
        "a field this gateway does not serve is refused rather than accepted and ignored",
        "gateway/src/aira_gateway/api/gemini/schemas.py",
        "            raise ValueError(f\"'{field}' is not served by this gateway: {reason}\")",
        "            pass",
        NO_SILENT_DROP,
    ),
    Mutation(
        "Y2",
        "a field nobody modelled is refused naming it, rather than silently dropped",
        "gateway/src/aira_gateway/api/gemini/schemas.py",
        '_STRICT = ConfigDict(extra="forbid")',
        '_STRICT = ConfigDict(extra="ignore")',
        NO_SILENT_DROP,
    ),
    Mutation(
        "Y3",
        "a sampling control the caller set reaches the dialect instead of being dropped",
        "gateway/src/aira_gateway/api/gemini/mapping.py",
        "        seed=config.seed if config else None,",
        "        seed=None,",
        NO_SILENT_DROP,
    ),
    Mutation(
        "Y4",
        "a candidate whose dialect cannot express a control is skipped, not served without it",
        "gateway/src/aira_gateway/api/serving.py",
        "        checks.append(SamplingExpressible(registry_of(request), canonical.sampling_requested))",
        "        pass",
        NO_SILENT_DROP,
    ),
    Mutation(
        "Y5",
        "an adapter that declares no sampling support refuses rather than silently allowing all",
        "gateway/src/aira_gateway/requirements.py",
        '        supported: frozenset[str] = getattr(provider, "sampling_controls", frozenset())',
        '        supported: frozenset[str] = getattr(provider, "sampling_controls", self._requested)',
        NO_SILENT_DROP,
    ),
    Mutation(
        "Y6",
        "thinking switched off is sent as off, not as an absent parameter the model reads as its default",
        "gateway/src/aira_gateway/upstreams/openai/mapping.py",
        '    if request.thinking is not None:\n        body["reasoning_effort"]',
        "    if request.thinking is not None and request.thinking.mode is not ThinkingMode.DISABLED:"
        '\n        body["reasoning_effort"]',
        f"{OPENAI_DIALECT} {NO_SILENT_DROP}",
    ),
    Mutation(
        "Y7",
        "a request for several candidates is refused rather than answered with one",
        "gateway/src/aira_gateway/api/gemini/schemas.py",
        "        if self.candidateCount is not None and self.candidateCount != 1:",
        "        if False:",
        NO_SILENT_DROP,
    ),
    Mutation(
        "Y8",
        "the compatibility surface refuses an unknown field rather than ignoring it",
        "gateway/src/aira_gateway/api/kira/schemas.py",
        '_STRICT_ALIASED = ConfigDict(populate_by_name=True, extra="forbid")',
        '_STRICT_ALIASED = ConfigDict(populate_by_name=True, extra="ignore")',
        NO_SILENT_DROP,
    ),
    Mutation(
        "Y9",
        "a request refused on size is audited, not answered 413 and forgotten",
        "gateway/src/aira_gateway/middleware.py",
        # Re-anchored: `_reject` gained the path it is refusing. Anchored on the **declared
        # `Content-Length`** exit rather than the read-past-the-ceiling one, because that is the
        # exit `FRD-122` §12 was found on — a 20 MB body refused before any route, leaving no trace
        # at all.
        "            await record_oversized(scope, self.max_bytes)\n"
        '            await self._reject(send, str(scope.get("path", "")))',
        '            await self._reject(send, str(scope.get("path", "")))',
        HARDENING,
    ),
    Mutation(
        "Y10",
        "the streamed oversize exit records the same row as the declared one",
        "gateway/src/aira_gateway/middleware.py",
        "                    await record_oversized(scope, self.max_bytes)\n                    raise RequestTooLarge",
        "                    raise RequestTooLarge",
        HARDENING,
    ),
    Mutation(
        "Y11",
        "a size refusal records no identity it could not verify",
        "gateway/src/aira_gateway/middleware.py",
        '                subject="",\n                auth_method="",',
        '                subject="unverified",\n                auth_method="unverified",',
        HARDENING,
    ),
    # -- FRD-125: a classifier that did not answer has not said "clean" -------------------------
    Mutation(
        "Z1",
        "a classifier that gave no usable answer is undetermined, never clean",
        "gateway/src/aira_gateway/pipeline/classifiers.py",
        "        return Verdict.UNDETERMINED",
        "        return Verdict.CLEAN",
        CLASSIFIERS,
    ),
    Mutation(
        "Z2",
        "an upstream failure in the classifier is undetermined, never clean",
        "gateway/src/aira_gateway/pipeline/classifiers.py",
        # Re-anchored: `FRD-125b` moved this return into `classify_text`, and the harness reported
        # the property as undefended rather than pretending otherwise — which is the behaviour that
        # makes "a mutation whose anchor moved protects nothing" checkable instead of aspirational.
        "            return Classification(Verdict.UNDETERMINED)",
        "            return Classification(Verdict.CLEAN)",
        CLASSIFIERS,
    ),
    Mutation(
        "Z3",
        "an undetermined verdict blocks a blocking filter rather than passing it through",
        "gateway/src/aira_gateway/pipeline/engine.py",
        "    if verdict is Verdict.UNDETERMINED:\n        return str(config.get",
        "    if False:\n        return str(config.get",
        CLASSIFIERS,
    ),
    Mutation(
        "Z4",
        "a classifier asks for no thinking, so its one-word allowance is not spent reasoning",
        "gateway/src/aira_gateway/pipeline/classifiers.py",
        "    model: str, instruction: str, text: str, thinking: Thinking | None = _OFF\n"
        ") -> CanonicalRequest:",
        "    model: str, instruction: str, text: str, thinking: Thinking | None = None\n"
        ") -> CanonicalRequest:",
        CLASSIFIERS,
    ),
    Mutation(
        "Z5",
        "a filter that ran and passed is recorded, so it is distinguishable from no filter",
        "gateway/src/aira_gateway/pipeline/engine.py",
        # Re-anchored (2026-08-14): `run` and `dry_run` no longer carry a branch each, so the
        # decision is built where the step is evaluated. The property is the one it always was —
        # "the filter ran and passed" must be distinguishable from "no filter was configured".
        '            decision={\n                "step": "injection_filter",\n                "flagged": verdict is not Verdict.CLEAN,',
        '            decision=None if verdict is Verdict.CLEAN else {\n                "step": "injection_filter",\n                "flagged": verdict is not Verdict.CLEAN,',
        CLASSIFIERS,
    ),
    Mutation(
        "Z6",
        "a model call the pipeline made leaves its own audit row",
        "gateway/src/aira_gateway/api/serving.py",
        "        await record_pipeline_calls(request, trail)",
        "        pass",
        ACCOUNTING,
    ),
    Mutation(
        "Z7",
        "a step that blocked still records what deciding to block cost",
        "gateway/src/aira_gateway/api/serving.py",
        "    finally:\n        # **One site**",
        "    else:\n        # **One site**",
        ACCOUNTING,
    ),
    Mutation(
        "Z8",
        "a pipeline call is booked against the budget as tokens and not as a second request",
        "gateway/src/aira_gateway/budgets/service.py",
        # Re-anchored: the call was split over several lines when `username` joined it. `tokens` is
        # still the argument the property is about — a classifier's spend is consumption and is
        # counted; the caller made one request, and booking a second would inflate every request
        # figure and could trip a request limit for traffic nobody sent.
        "        await self.record(\n            budgets,\n            tokens,",
        "        await self.record(\n            budgets,\n            0,",
        f"{ACCOUNTING} gateway/tests/test_budget_service.py",
    ),
    Mutation(
        "Z9",
        "the pipeline's own call never stores the caller's prompt a second time",
        "gateway/src/aira_gateway/api/serving.py",
        "                request_payload=None,\n                response_payload=None,\n                cost_nanos=cost,",
        "                request_payload=trail.body,\n                response_payload=None,\n                cost_nanos=cost,",
        ACCOUNTING,
    ),
    Mutation(
        "Z10",
        "a pipeline call reaches the counter the guard reads, not only the system of record",
        "gateway/src/aira_gateway/budgets/service.py",
        "        if self._ledger is None:\n            return  # degraded",
        "        if True:\n            return  # degraded",
        BUDGET_RESERVATION,
    ),
    Mutation(
        "Z11",
        "an already-exhausted budget refuses before the pipeline can spend anything",
        "gateway/src/aira_gateway/api/serving.py",
        # Re-anchored by `FRD-126`: this step moved out of the surfaces and into the one
        # sequence that owns their order. A mutation whose anchor has moved protects nothing.
        "    await guard_before_work(request, units=units)\n\n    fallbacks",
        "    fallbacks",
        f"{ACCOUNTING} gateway/tests/test_serving_options.py",
    ),
    Mutation(
        "Z12",
        "the early gate weighs a batch as the many requests it is",
        "gateway/src/aira_gateway/api/serving.py",
        # Re-anchored by `FRD-126`: this step moved out of the surfaces and into the one
        # sequence that owns their order. A mutation whose anchor has moved protects nothing.
        "    units = embed.size if embed is not None else 1",
        "    units = 1",
        "gateway/tests/test_serving_options.py",
    ),
    # `Z13` lived here: "the compatibility surface takes the same early gate". It was a distinct
    # property only *because* there were two surfaces each taking the gate for themselves. After
    # `FRD-126` there is one sequence, so its anchor and `Z11`'s are the same line, and two
    # mutations on one line measure one thing twice.
    #
    # Removed rather than kept, as `X3` was: what it really claimed — that this surface goes
    # through the shared sequence at all — is now enforced structurally by
    # `test_surface_layering.py` and mutated by `Z16`. A property guarded by the structure is not
    # a property a line-edit can take away, and pretending otherwise inflates the count.
    Mutation(
        "Z14",
        "reaching a reservation without the early gate fails loudly rather than serving unmetered",
        "gateway/src/aira_gateway/api/serving.py",
        '    if not getattr(request.state, "early_gate_taken", False):',
        "    if False:",
        "gateway/tests/test_ratelimit_routes.py gateway/tests/test_pipeline_accounting.py",
    ),
    Mutation(
        "Z15",
        "the shared sequence resolves thinking after routing, not against the model the caller named",
        "gateway/src/aira_gateway/api/serving.py",
        '                "thinking": resolve_thinking(canonical.thinking, declaration),',
        '                "thinking": canonical.thinking,',
        "gateway/tests/test_serving_options.py gateway/tests/test_kira_surface.py",
    ),
    Mutation(
        "Z16",
        "a surface cannot assemble the pre-dispatch order itself",
        "gateway/src/aira_gateway/api/serving.py",
        "async def prepare_for_dispatch(",
        "async def prepare_for_dispatch_renamed(",
        "gateway/tests/test_surface_layering.py",
    ),
    # The **shield** deliberately has no mutation, and the reason is `FRD-110`'s, restated:
    # closing a generator in-process raises `GeneratorExit` and awaits in a `finally` run normally,
    # while a real socket drop cancels the task. A hermetic test cannot tell those apart — verified
    # here by running this surface's disconnect test against an un-shielded version and watching it
    # pass. A mutation that claims to guard the shield would be claiming a proof nobody has, and a
    # harness that lies about its coverage is worse than one with a gap in it. The integration
    # layer is where that property is checked.
    Mutation(
        "Z17",
        "a request that ends without an answer is still recorded, however it ended",
        "gateway/src/aira_gateway/api/serving.py",
        # Re-anchored by `FRD-128`: the hand-written finisher this pointed at is gone, and the
        # property it named now belongs to every path rather than to one surface's stream.
        "    if not record:\n        return",
        "    if True:\n        return",
        "gateway/tests/test_kira_streaming_disconnect.py gateway/tests/test_kira_surface.py",
    ),
    Mutation(
        "Z18",
        "a request that produced nothing is released, not settled for an answer nobody received",
        "gateway/src/aira_gateway/api/serving.py",
        "    if not state.produced:",
        "    if False:",
        # The selection widened with the anchor, exactly as `K6`'s had to: this was one surface's
        # stream and is now every path's, so naming one surface's tests checks a sixth of it.
        "gateway/tests/test_kira_streaming_disconnect.py gateway/tests/test_ratelimit_routes.py "
        "gateway/tests/test_cancelled_requests.py gateway/tests/test_hardening.py",
    ),
    Mutation(
        "Z19",
        "a caller who goes away while the model answers is still recorded on every path",
        "gateway/src/aira_gateway/api/serving.py",
        # `KeyboardInterrupt` is a real name that a cancellation is *not*, so the cancellation
        # falls through to the clause below and is treated as a refusal somebody else will
        # record — which is the defect: nobody else does.
        "        except asyncio.CancelledError, GeneratorExit:",
        "        except KeyboardInterrupt:",
        "gateway/tests/test_cancelled_requests.py gateway/tests/test_kira_streaming_disconnect.py",
    ),
    Mutation(
        "Z20",
        "an embedding batch settles as the many requests it is, not as one",
        "gateway/src/aira_gateway/api/serving.py",
        "            requests=state.requests,",
        "            requests=1,",
        "gateway/tests/test_ratelimit_routes.py gateway/tests/test_serving_options.py",
    ),
    Mutation(
        "Z21",
        "an upstream 400 is a precondition failure an operator can fix, not an outage",
        "gateway/src/aira_gateway/api/serving.py",
        "    if status_code == 400:\n        return UPSTREAM_REFUSED",
        "    if False:\n        return UPSTREAM_REFUSED",
        "gateway/tests/test_error_handling.py",
    ),
    Mutation(
        "Z22",
        "an upstream credential failure stays masked rather than being handed to the caller",
        "gateway/src/aira_gateway/upstreams/openai/transport.py",
        '        detail = _reason(response) if response.status_code == 400 else ""',
        "        detail = _reason(response)",
        "gateway/tests/test_openai_dialect.py gateway/tests/test_error_handling.py",
    ),
    # ---- the console is told what it may do, and told the truth (FRD-131) ----------------
    Mutation(
        "Z23",
        "what the detail says a caller may do is what the server would let them do",
        "management/backend/src/aira_management/apps/usecases/serializers.py",
        '            "can_manage": may_manage(user, obj),',
        '            "can_manage": True,',
        MGMT_RBAC,
    ),
    Mutation(
        "Z24",
        "seeing every use case is not being in one, so it does not mint a key",
        "management/backend/src/aira_management/apps/usecases/serializers.py",
        '            "is_member": is_member(user, obj),',
        '            "is_member": True,',
        MGMT_RBAC,
    ),
    # ---- anomaly rules (FRD-500, ADR-0014) ----------------------------------------------
    Mutation(
        "N1a",
        "a new rule only alerts until somebody promotes it",
        "management/backend/src/aira_management/apps/anomalies/models.py",
        "action = models.CharField(max_length=16, choices=ACTION_CHOICES, default=RuleAction.ALERT)",
        "action = models.CharField(max_length=16, choices=ACTION_CHOICES, default=RuleAction.BLOCK)",
        ANOMALY_RULES,
    ),
    Mutation(
        "N2a",
        "an action that takes something away must say for how long",
        "management/backend/src/aira_management/apps/anomalies/serializers.py",
        "        if action in (RuleAction.THROTTLE, RuleAction.BLOCK):",
        "        if False:",
        ANOMALY_RULES,
    ),
    Mutation(
        "N3a",
        "a rate rule keeps its sample floor, so one refusal of one is not 100 percent",
        "management/backend/src/aira_management/apps/anomalies/serializers.py",
        '        elif attrs.get("min_sample", 0) < 1:',
        "        elif False:",
        ANOMALY_RULES,
    ),
    Mutation(
        "N4a",
        "only an oversight role may author a rule that acts everywhere",
        "management/backend/src/aira_management/apps/anomalies/views.py",
        '        if not may_author_global(self.request.user):\n            raise PermissionDenied(\n                "Only IT Security or a Global Administrator may author a global rule. "',
        '        if False:\n            raise PermissionDenied(\n                "Only IT Security or a Global Administrator may author a global rule. "',
        ANOMALY_RULES,
    ),
    Mutation(
        "N5a",
        "deleting one use case does not switch off detection for every other",
        "gateway/src/aira_gateway/consumer/apply.py",
        "    await session.execute(delete(AnomalyRuleRead).where(AnomalyRuleRead.use_case == slug))",
        "    await session.execute(delete(AnomalyRuleRead))",
        ANOMALY_CONSUMER,
    ),
    Mutation(
        "N6a",
        "an event with no scope is skipped rather than made global",
        "gateway/src/aira_gateway/consumer/apply.py",
        '    if "use_case" not in payload:\n        return',
        "    if False:\n        return",
        ANOMALY_CONSUMER,
    ),
    # ---- the detection engine (FRD-501) --------------------------------------------------
    Mutation(
        "N7",
        "a rate over too few requests says nothing, rather than 100 percent",
        "gateway/src/aira_gateway/anomalies/evaluator.py",
        "        if total < max(rule.min_sample, 1):",
        "        if False:",
        ANOMALY_ENGINE,
    ),
    Mutation(
        "N8",
        "growth from nothing is not an infinite spike",
        "gateway/src/aira_gateway/anomalies/evaluator.py",
        "        if was <= 0:\n            continue",
        "        if was <= 0:\n            was = 1",
        ANOMALY_ENGINE,
    ),
    Mutation(
        "N9",
        "a request whose size is unknown is not counted as a small one",
        "gateway/src/aira_gateway/anomalies/evaluator.py",
        "        known_only=RequestLog.request_bytes.is_not(None),",
        "        known_only=None,",
        ANOMALY_ENGINE,
    ),
    Mutation(
        "N10",
        "the same finding is not written again inside its own window",
        "gateway/src/aira_gateway/anomalies/service.py",
        # Re-anchored 2026-08-16: the cooldown moved from an in-process dict to the
        # `anomaly_events` table, so it survives a restart and holds across instances (`FRD-127`).
        # The property is unchanged — the same finding is not written twice inside its window.
        "        if await self._fired_recently(session, rule, finding.target_value, now):\n            return None",
        "        if False:\n            return None",
        ANOMALY_ENGINE,
    ),
    Mutation(
        "N10b",
        "the cooldown is shared, so a second evaluator does not write the finding again",
        "gateway/src/aira_gateway/anomalies/service.py",
        # The multi-instance defect (`FRD-127`). Reading the cooldown from a per-process map is
        # exactly what let N instances each fire once while each sat inside its own window.
        "                AnomalyEvent.rule_id == rule.id,",
        "                AnomalyEvent.rule_id == -1,",
        "gateway/tests/test_anomaly_engine.py",
    ),
    Mutation(
        "N10c",
        "which scopes saw traffic is read from the audit rows, not from this process",
        "gateway/src/aira_gateway/anomalies/service.py",
        # Narrowing the lookback to nothing is what a per-process touched set amounted to for an
        # instance that had served none of the traffic: it evaluated, found nothing, and looked
        # exactly like a quiet minute.
        "        since = self._since or (moment - timedelta(seconds=self.interval_seconds))",
        "        since = moment",
        "gateway/tests/test_anomaly_engine.py",
    ),
    Mutation(
        "N11",
        "a rule whose use case saw no traffic is not evaluated",
        "gateway/src/aira_gateway/anomalies/service.py",
        "        return [r for r in rules if r.use_case is None or r.use_case in touched]",
        "        return rules",
        ANOMALY_ENGINE,
    ),
    Mutation(
        "N12",
        "an action this stage cannot take is recorded as not taken",
        "gateway/src/aira_gateway/anomalies/service.py",
        "        if self.suspensions is None or not rule.action_minutes:",
        "        if False:",
        ANOMALY_ENGINE,
    ),
    Mutation(
        "N13",
        "a payload rule needs its byte figure to measure anything",
        "management/backend/src/aira_management/apps/anomalies/serializers.py",
        "        if needs_parameter(kind):",
        "        if False:",
        ANOMALY_RULES,
    ),
    # ---- incident response (FRD-503) -----------------------------------------------------
    Mutation(
        "N14",
        "a suspension that has run out stops refusing people",
        "gateway/src/aira_gateway/anomalies/suspensions.py",
        "    return expires > moment",
        "    return True",
        SUSPENSIONS,
    ),
    # `N15` was here — "a lifted suspension stops refusing people" — and it survived, correctly.
    # The load query already filters `lifted_at IS NULL`, so a lifted row is never in the cache to
    # begin with, and the in-memory check is the second of two guards. A property guarded twice
    # cannot be a mutation, and that is not a reason to remove one of the guards (the `X3`
    # precedent). The behaviour is still asserted by
    # `test_a_lifted_suspension_stops_nobody`.
    Mutation(
        "N16",
        "a suspension scoped to one use case does not reach another",
        "gateway/src/aira_gateway/anomalies/suspensions.py",
        "    if row.use_case is not None and row.use_case != use_case:\n        return False",
        "    if False:\n        return False",
        SUSPENSIONS,
    ),
    Mutation(
        "N17",
        "being stopped on purpose is recorded as such, not as going too fast",
        "gateway/src/aira_gateway/api/serving.py",
        "    if isinstance(exc, Suspended):\n        return Outcome.SUSPENDED",
        "    if isinstance(exc, Suspended):\n        return Outcome.RATE_LIMITED",
        SUSPENSIONS,
    ),
    Mutation(
        "N18",
        "a rule that only alerts takes nothing away",
        "gateway/src/aira_gateway/anomalies/service.py",
        "        if action is RuleAction.ALERT:\n            return RuleAction.ALERT.value",
        "        if False:\n            return RuleAction.ALERT.value",
        SUSPENSIONS,
    ),
    Mutation(
        "N19",
        "only an incident role may stop traffic by hand",
        "gateway/src/aira_gateway/api/incidents.py",
        # Disambiguated 2026-08-12: a second endpoint (`FRD-506`'s reachability check) asks the
        # same predicate, so the bare line matched twice and the harness edits the first. It
        # happened to be the intended one; that it did was luck, and the next endpoint to ask this
        # question would have moved it. Anchored on the refusal `_require_oversight` raises, which
        # only the kill switch has.
        "    if not principal.may_act_on_incidents:\n        raise GeminiHTTPError(\n            403,\n"
        '            "Only IT Security or a Global Administrator may suspend or restore access.",',
        "    if False:\n        raise GeminiHTTPError(\n            403,\n"
        '            "Only IT Security or a Global Administrator may suspend or restore access.",',
        "gateway/tests/test_suspensions.py gateway/tests/test_reporting.py",
    ),
    # ---- what the live round found (FRD-503 §7) -------------------------------------------
    Mutation(
        "N20",
        "a refused request is not counted as unpriced traffic",
        "gateway/src/aira_gateway/reporting/service.py",
        "                    & (\n                        (RequestLog.outcome == Outcome.SERVED)",
        "                    & (\n                        (RequestLog.outcome != Outcome.SERVED)",
        REPORTING,
    ),
    Mutation(
        "N21",
        "a row written before outcomes existed is still counted as unpriced",
        "gateway/src/aira_gateway/reporting/service.py",
        "                        | (RequestLog.outcome.is_(None))",
        "                        | (False)",
        REPORTING,
    ),
    Mutation(
        "N22",
        "stopping traffic takes an incident role, not merely a role that may look",
        "libs/src/aira_common/roles.py",
        "INCIDENT_ROLES: frozenset[Role] = frozenset({Role.GLOBAL_ADMIN, Role.IT_SECURITY})",
        "INCIDENT_ROLES: frozenset[Role] = OVERSIGHT_ROLES",
        "gateway/tests/test_suspensions.py libs/tests/test_roles.py",
    ),
    Mutation(
        "N23",
        "every topic the code publishes to is one something creates",
        "Makefile",
        " aira.anomaly-rules",
        "",
        TOPICS,
    ),
    # ---- the trace overview (FRD-502) -------------------------------------------------------
    Mutation(
        "N24",
        "a trace carries no payload, whatever the retention settings stored",
        "gateway/src/aira_gateway/api/reporting.py",
        'TRACE_FIELDS = (\n    "id",',
        'TRACE_FIELDS = (\n    "request_payload",\n    "response_payload",\n    "id",',
        "gateway/tests/test_traces.py",
    ),
    Mutation(
        "N25",
        "a trace list is scoped to the use cases the caller is in",
        "gateway/src/aira_gateway/api/reporting.py",
        "        stmt = stmt.where(RequestLog.use_case.in_(allowed))\n    if use_case:",
        "        stmt = stmt\n    if use_case:",
        "gateway/tests/test_traces.py",
    ),
    Mutation(
        "N26",
        "paging by (timestamp, id) shows no row twice when two share a moment",
        "gateway/src/aira_gateway/api/reporting.py",
        "                and_(RequestLog.created_at == at, RequestLog.id < row_id),",
        "                and_(RequestLog.created_at == at, RequestLog.id <= row_id),",
        "gateway/tests/test_traces.py",
    ),
    Mutation(
        "N28",
        "an empty answer says whether it means 'nothing happened' or 'you see nothing'",
        "gateway/src/aira_gateway/api/reporting.py",
        'return {"traces": [], "next_cursor": None, "scope": "use_cases", "in_scope": False}',
        'return {"traces": [], "next_cursor": None, "scope": "use_cases", "in_scope": True}',
        "gateway/tests/test_traces.py",
    ),
    Mutation(
        "N29",
        "findings are asked for by use case, not filtered out of a global page",
        "gateway/src/aira_gateway/api/reporting.py",
        "    if use_case:\n        stmt = stmt.where(AnomalyEvent.use_case == use_case)",
        "    if False:\n        stmt = stmt.where(AnomalyEvent.use_case == use_case)",
        "gateway/tests/test_anomaly_engine.py",
    ),
    # ---- what an incident is allowed to ask (FRD-131 FR-7, FRD-502) -------------------------
    Mutation(
        "N40",
        "IT Security's own console is not empty — the wider role decides who sees everything",
        "gateway/src/aira_gateway/api/reporting.py",
        "    if principal.is_oversight:\n        return None",
        "    if principal.is_governance:\n        return None",
        "gateway/tests/test_traces.py gateway/tests/test_reporting.py",
    ),
    Mutation(
        "N41",
        "the calling machine's address is shown only to a role that may act on an incident",
        "gateway/src/aira_gateway/api/reporting.py",
        "    fields = TRACE_FIELDS + (INCIDENT_FIELDS if principal.may_act_on_incidents else ())",
        "    fields = TRACE_FIELDS + INCIDENT_FIELDS",
        "gateway/tests/test_traces.py",
    ),
    Mutation(
        "N42",
        "a filter nobody may use is refused, never quietly ignored",
        "gateway/src/aira_gateway/api/reporting.py",
        "        if not principal.may_act_on_incidents:\n            # Refused rather than ignored.",
        "        if False:\n            # Refused rather than ignored.",
        "gateway/tests/test_traces.py",
    ),
    Mutation(
        "N43",
        "'only my own requests' means the caller's identity, not everybody's",
        "gateway/src/aira_gateway/api/reporting.py",
        "        stmt = stmt.where(RequestLog.subject == principal.subject)",
        "        stmt = stmt",
        "gateway/tests/test_traces.py",
    ),
    Mutation(
        "N44",
        "'only tool turns' excludes the requests where the model asked for nothing",
        "gateway/src/aira_gateway/api/reporting.py",
        "        stmt = stmt.where(RequestLog.tool_calls.is_not(None))",
        "        stmt = stmt",
        "gateway/tests/test_traces.py",
    ),
    Mutation(
        "N45",
        "an id claimed by two models is refused, not resolved by whichever row was read first",
        "gateway/src/aira_gateway/catalog.py",
        "        if len(names) > 1:",
        "        if False:",
        "gateway/tests/test_kira_surface.py",
    ),
    # ---- who may read a stored prompt (FRD-505, ADR-0016) -----------------------------------
    #
    # Added after an audit that broke each branch of `payloads.py` in turn and recorded which
    # parametrised rows noticed. It found `is_oversight` **undefended**: removing it makes an
    # oversight role fall through to `OUT_OF_SCOPE`, which is also a 403, so a matrix checking only
    # the status passed. The matrix now asserts the *sentence*, and these keep it that way.
    #
    # Both directions on purpose. Deleting a branch can only make the code more permissive, so a
    # case that guards against over-restriction cannot go red for any deletion — `N50` is the
    # mutation that refuses too much, and it is the only thing defending "a colleague's request is
    # readable until somebody restricts it".
    Mutation(
        "N46",
        "an incident role reads content; nobody else gets that shortcut",
        "gateway/src/aira_gateway/payloads.py",
        '    if principal.may_act_on_incidents:\n        return "incident"',
        '    if False:\n        return "incident"',
        "gateway/tests/test_payload_access.py",
    ),
    Mutation(
        "N47",
        "an oversight role is told it sees figures, not that the use case is not theirs",
        "gateway/src/aira_gateway/payloads.py",
        "        if principal.is_oversight:\n            return PayloadRefusal.NOT_A_CONTENT_ROLE",
        "        if False:\n            return PayloadRefusal.NOT_A_CONTENT_ROLE",
        "gateway/tests/test_payload_access.py",
    ),
    Mutation(
        "N48",
        "a use case's own administrator reads its content whatever the member restriction says",
        "gateway/src/aira_gateway/payloads.py",
        '    if role == GrantRole.ADMIN.value:\n        return "use_case_admin"',
        '    if False:\n        return "use_case_admin"',
        "gateway/tests/test_payload_access.py",
    ),
    Mutation(
        "N49",
        "a restricted member is refused somebody else's request",
        "gateway/src/aira_gateway/payloads.py",
        "    if restricted and row.subject != principal.subject:",
        "    if False and row.subject != principal.subject:",
        "gateway/tests/test_payload_access.py",
    ),
    Mutation(
        "N50",
        "a member reads a colleague's request until somebody restricts it",
        "gateway/src/aira_gateway/payloads.py",
        "    if restricted and row.subject != principal.subject:",
        "    if row.subject != principal.subject:",
        "gateway/tests/test_payload_access.py",
    ),
    Mutation(
        "N51",
        "storage being off is reported as such, not as an expiry",
        "gateway/src/aira_gateway/payloads.py",
        "        if use_case is not None and not use_case.store_payloads:",
        "        if False:",
        "gateway/tests/test_payload_access.py",
    ),
    Mutation(
        "N52",
        "a payload that a request certainly had is reported as expired, not as never existing",
        "gateway/src/aira_gateway/payloads.py",
        "        if row.status is not None and 200 <= row.status < 300:",
        "        if False:",
        "gateway/tests/test_payload_access.py",
    ),
    Mutation(
        "N53",
        "the route refuses a request outside the caller's scope before any of this is asked",
        "gateway/src/aira_gateway/api/reporting.py",
        "        if row is None or (scope is not None and row.use_case not in scope):",
        "        if row is None:",
        "gateway/tests/test_payload_access.py",
    ),
    Mutation(
        "N54",
        "a read of stored content is recorded",
        "gateway/src/aira_gateway/api/reporting.py",
        "        session.add(\n            PayloadAccess(",
        "        _unused = (\n            PayloadAccess(",
        "gateway/tests/test_payload_access.py",
    ),
    # ---- prompt-cache accounting (FRD-133 stage A) -------------------------------------------
    #
    # Measured first: 99.1 % of an assistant turn is repeated content and 93.3 % of that use case's
    # tokens are input, so this is where its bill is. `C1` and `C2` are the two directions the
    # pricing can be wrong in, and only one of them is safe — under-billing a write is the failure
    # a cost control must not have. `C3` is the invariant everything else in the system rests on.
    Mutation(
        "U1",
        "a cache read is charged at its own rate, not at full input price",
        "gateway/src/aira_gateway/pricing.py",
        "                usage.uncached_input_tokens,",
        "                usage.prompt_tokens,",
        "gateway/tests/test_prompt_cache_accounting.py",
    ),
    Mutation(
        "U2",
        "a cache write costs more than ordinary input, and is billed so",
        "gateway/src/aira_gateway/pricing.py",
        "            + cost_nanos(usage.cache_write_tokens, price.cache_write_rate)",
        "            + 0",
        "gateway/tests/test_prompt_cache_accounting.py",
    ),
    Mutation(
        "U3",
        "cached tokens are a subset of the input total, never an addition to it",
        "gateway/src/aira_gateway/core/canonical.py",
        "        return max(0, self.prompt_tokens - self.cached_input_tokens - self.cache_write_tokens)",
        "        return self.prompt_tokens",
        "gateway/tests/test_prompt_cache_accounting.py",
    ),
    Mutation(
        "U4",
        "the dialects read the cache counts their provider actually sends",
        "gateway/src/aira_gateway/upstreams/openai/mapping.py",
        'cached = int((details or {}).get("cached_tokens", 0) or 0) if isinstance(details, dict) else 0',
        "cached = 0",
        "gateway/tests/test_prompt_cache_accounting.py",
    ),
    # Re-anchored the day the lifetime became configurable: the marker stopped being a constant
    # and became a function of the request. A mutation whose anchor has moved protects nothing —
    # and this one was reported STALE by the harness rather than silently passing, which is the
    # whole reason `N2`'s lesson was built into it.
    Mutation(
        "U5",
        "the cache marker lands on the stable prefix when a use case asks for it",
        "gateway/src/aira_gateway/upstreams/vertex/anthropic_mapping.py",
        '            body["tools"][-1]["cache_control"] = _cache_control(request)',
        "            pass",
        "gateway/tests/test_prompt_cache_accounting.py",
    ),
    # ---- the console's two settings, and the journey they make (FRD-133 stage C) --------------
    #
    # The three below are all one property seen at three hops: a checkbox travels through an
    # event, a read-model row and a post-routing lookup before it can change a single byte on the
    # wire, and **nothing fails** when it is dropped — a request served uncached is
    # indistinguishable from one that was never asked to cache. `FRD-124`'s lesson, which is that
    # a requirement tested only where it is implemented leaves the wiring to it undefended.
    Mutation(
        "U7",
        "the expensive lifetime is only ever sent when it was asked for",
        "gateway/src/aira_gateway/upstreams/vertex/anthropic_mapping.py",
        '    return {**_EPHEMERAL, "ttl": "1h"} if request.cache_ttl == "1h" else dict(_EPHEMERAL)',
        '    return {**_EPHEMERAL, "ttl": "1h"}',
        "gateway/tests/test_prompt_cache_accounting.py",
    ),
    Mutation(
        "U8",
        "the lifetime the console chose is the one that reaches the provider",
        "gateway/src/aira_gateway/api/serving.py",
        '    chosen = getattr(record, "prompt_cache_ttl", "5m") if record is not None else "5m"',
        '    chosen = "5m"',
        "gateway/tests/test_prompt_cache_accounting.py",
    ),
    Mutation(
        "U9",
        "a use case that did not opt in is never marked, however cheap it would be",
        "gateway/src/aira_gateway/api/serving.py",
        "    return bool(record is not None and record.prompt_caching_enabled)",
        "    return True",
        "gateway/tests/test_prompt_cache_accounting.py",
    ),
    Mutation(
        "U6",
        "a request that did not opt in is byte-identical to what it was before caching existed",
        "gateway/src/aira_gateway/upstreams/vertex/anthropic_mapping.py",
        "            if request.cache_prefix\n            else joined",
        "            if True\n            else joined",
        "gateway/tests/test_prompt_cache_accounting.py",
    ),
    # ---- the security round (2026-08-09) -----------------------------------------------------
    #
    # Four properties from a full read of the code. Each is a *refusal* or a *parse*, which is the
    # kind of thing that keeps working when it stops working — nothing fails, and the guarantee is
    # simply gone. `W1` is the one that had a comment claiming it: the model segment was documented
    # as encoded and was not.
    Mutation(
        "W1",
        "a model name is one path segment and cannot walk up the URL",
        "gateway/src/aira_gateway/upstreams/vertex/transport.py",
        'segment = quote(model, safe="@")',
        'segment = httpx.URL(path=f"/{model}").path.lstrip("/")',
        "gateway/tests/test_vertex.py",
    ),
    Mutation(
        "W2",
        "the forwarded address is read from the right, so a caller cannot choose it",
        "gateway/src/aira_gateway/persistence/recorder.py",
        "                return chain[-hops][:64]",
        "                return chain[0][:64]",
        "gateway/tests/test_persistence_recorder.py gateway/tests/test_auth_attempt_bound.py",
    ),
    Mutation(
        "W3",
        "a plaintext identity provider stops a deployment",
        "libs/src/aira_common/transport_security.py",
        '    if parts.scheme.lower() != "http":\n        return False',
        "    if True:\n        return False",
        "libs/tests/test_transport_security.py gateway/tests/test_deployment_safety.py",
    ),
    Mutation(
        "W4",
        "an unauthenticated event bus stops a deployment",
        "gateway/src/aira_gateway/security.py",
        # Re-anchored 2026-08-15: each check now also asks whether a demo waives it
        # (`WAIVED_BY_A_DEMO`), because the demo used to waive all of them at once.
        "        and settings.kafka_bootstrap_servers.strip()\n"
        "        and settings.kafka_security().is_plaintext",
        "        and False",
        "gateway/tests/test_deployment_safety.py",
    ),
    Mutation(
        "W5",
        "a pattern that could hang a worker is not compiled, wherever it came from",
        "gateway/src/aira_gateway/pipeline/classifiers.py",
        "            if is_catastrophic(pattern):",
        "            if False:",
        "gateway/tests/test_pipeline_classifiers.py libs/tests/test_patterns.py",
    ),
    # ---- a role is held through a group, and only through a group (ADR-0017) ----------------
    #
    # The change with the most consequence in the system: three predicates decide oversight,
    # governance and incident authority, and all three read what these mutations guard. `R1` is
    # the guarantee itself — a realm role must confer nothing — and it is stated as the thing that
    # must *not* work, because reading the code only shows the claim is unused, which is not the
    # same as showing it cannot grant.
    Mutation(
        "R20",
        "a realm role on the token confers nothing; only a configured group does",
        "management/backend/src/aira_management/rbac.py",
        "    held = roles_from_groups(_token_groups(claims), role_groups())",
        '    held = set((claims.get("realm_access") or {}).get("roles", []))',
        "management/backend/tests/test_rbac.py",
    ),
    Mutation(
        "R21",
        "a role the caller no longer holds is removed, not merely never added",
        "management/backend/src/aira_management/rbac.py",
        # Re-anchored 2026-08-15, and **moved off the line G2 now also guards**. Adding and removing
        # became one function (`_reconcile`) when both syncs stopped writing unconditionally, so
        # this and G2 would otherwise be two claims about one `if drop:` — the redundancy this
        # harness's own notes call a defect in the making rather than a second defence.
        #
        # What is still this mutation's own is the **roles'** side of the comparison: read nothing
        # back and every role looks new, so nothing is ever dropped — for roles only, while the
        # Keycloak mirror groups G2 covers keep working.
        '    current = set(user.groups.filter(name__in=known).values_list("name", flat=True))',
        "    current = set()",
        "management/backend/tests/test_rbac.py",
    ),
    Mutation(
        "R22",
        "the group match is exact, so a neighbouring path confers nothing",
        "libs/src/aira_common/roles.py",
        "if role in mapping and any(path in held for path in mapping[role])",
        "if role in mapping and any(h.startswith(path) for path in mapping[role] for h in held)",
        "libs/tests/test_roles.py",
    ),
    Mutation(
        "R24",
        "an unknown role in the mapping refuses instead of granting nothing quietly",
        "libs/src/aira_common/roles.py",
        "            raise RoleMappingError(\n"
        "                f\"'{name.strip()}' is not an AIRA role. Expected: {allowed}.\"\n"
        "            ) from exc",
        "            continue",
        "libs/tests/test_roles.py",
    ),
    Mutation(
        "R25",
        "a deployment with no global-admin group refuses to start",
        "management/backend/src/aira_management/config/security.py",
        "    if Role.GLOBAL_ADMIN not in _role_groups(settings):",
        "    if False:",
        "management/backend/tests/test_hardening.py",
    ),
    Mutation(
        "R26",
        "/me reports the roles the server enforces, not the token's claim",
        "management/backend/src/aira_management/apps/api/views.py",
        '                "roles": [str(role) for role in ALL_ROLES if str(role) in held],',
        '                "roles": (claims.get("realm_access") or {}).get("roles", []),',
        "management/backend/tests/test_api.py",
    ),
    Mutation(
        "R27",
        "creating a use case is a Global Administrator's act",
        "management/backend/src/aira_management/apps/usecases/views.py",
        "            return [IsAuthenticated(), IsGlobalAdmin()]",
        "            return [IsAuthenticated()]",
        "management/backend/tests/test_usecases.py",
    ),
    # ---- one use case's own consumption (FRD-603) --------------------------------------------
    #
    # The endpoint could always report one use case; what it could not do was let a caller *ask*
    # for one without letting them ask for somebody else's. `N55` is the whole security property
    # of this parameter — written as `scope = (use_case,)` it reads as a narrowing and is a
    # widening. `N56` guards the distinction the console is built on: an empty report because
    # nothing happened, and an empty report because the use case is not this caller's, are two
    # facts, and a screen told only "empty" reports the second as the first.
    Mutation(
        "N55",
        "a use-case filter narrows what a caller may see and can never widen it",
        "gateway/src/aira_gateway/api/reporting.py",
        "        if scope is None or use_case in scope:\n            scope = (use_case,)",
        "        if True:\n            scope = (use_case,)",
        "gateway/tests/test_reporting.py",
    ),
    Mutation(
        "N56",
        "an empty report says whether it was allowed to be full",
        "gateway/src/aira_gateway/api/reporting.py",
        "            scope, in_scope = (), False",
        "            scope, in_scope = (), True",
        "gateway/tests/test_reporting.py",
    ),
    # ---- access by group (FRD-209) ----------------------------------------------------------
    Mutation(
        "N30",
        "a caller granted both ways gets the stronger role, not whichever row was read first",
        "libs/src/aira_common/access.py",
        "        best = max(best, ROLE_ORDER.index(GrantRole(role)))",
        "        best = ROLE_ORDER.index(GrantRole(role))",
        "libs/tests/test_access.py",
    ),
    Mutation(
        "N31",
        "a role this version has never heard of is not assumed to be powerful",
        "libs/src/aira_common/access.py",
        "        except ValueError:\n            continue",
        "        except ValueError:\n            best = len(ROLE_ORDER) - 1",
        "libs/tests/test_access.py",
    ),
    Mutation(
        "N32",
        "a group grant reaches only the groups the token actually carries",
        "libs/src/aira_common/access.py",
        "        if path in held:",
        "        if True:",
        "libs/tests/test_access.py",
    ),
    Mutation(
        "N33",
        "membership that cannot be evaluated is refused, not admitted",
        "gateway/src/aira_gateway/auth/grants.py",
        "            self._grants = ()",
        "            pass",
        "gateway/tests/test_group_grants.py",
    ),
    Mutation(
        "N34",
        "a Keycloak group that shares a role's name does not hand out the role",
        "management/backend/src/aira_management/rbac.py",
        'KEYCLOAK_GROUP_PREFIX = "kc:"',
        'KEYCLOAK_GROUP_PREFIX = ""',
        "management/backend/tests/test_group_grants.py",
    ),
    Mutation(
        "N35",
        "leaving a group in Keycloak takes the access away on the next token",
        "management/backend/src/aira_management/rbac.py",
        # Re-anchored 2026-08-15 onto the **groups'** side of the comparison, for the reason R21
        # records: adding and removing are one function now, so the removal line itself is G2's.
        # Reading nothing back makes every mirror group look new and none of them stale, which is
        # this property's own defect and leaves the roles R21 covers alone.
        "    current = set(\n"
        "        user.groups.filter(name__startswith=KEYCLOAK_GROUP_PREFIX)"
        '.values_list("name", flat=True)\n'
        "    )",
        "    current = set()",
        "management/backend/tests/test_group_grants.py",
    ),
    Mutation(
        "N36",
        "lowering a grant from admin to user actually lowers it",
        "management/backend/src/aira_management/apps/usecases/views.py",
        "            _revoke(group, usecase)\n            _grant(group, usecase, role)",
        "            _grant(group, usecase, role)",
        "management/backend/tests/test_group_grants.py",
    ),
    Mutation(
        "N37",
        "a group grant makes somebody a member, which is what issuing a key needs",
        "management/backend/src/aira_management/apps/usecases/access.py",
        "    return UseCaseGroupGrant.objects.filter(\n        use_case=usecase, group_path__in=held_group_paths(user)\n    ).exists()",
        "    return False",
        "management/backend/tests/test_group_grants.py",
    ),
    Mutation(
        "N38",
        "every event the code emits has a topic to travel on",
        "management/backend/src/aira_management/apps/outbox/subscriber.py",
        '    "use_case_group.granted": MEMBERSHIP_TOPIC,',
        "",
        "management/backend/tests/test_outbox_routing.py",
    ),
    Mutation(
        "N39",
        "two grants on one use case do not share a compaction key",
        "management/backend/src/aira_management/apps/outbox/subscriber.py",
        # Re-anchored: the `if` for grants became `_ALSO_IDENTIFIED_BY`, a table, when the same
        # defect turned out to be true of memberships three lines above it — "a table rather than a
        # second `if`, because the third one would have been forgotten too". The mutation now
        # empties the table, which is the same defect for every entry rather than for one of them.
        '_ALSO_IDENTIFIED_BY = {\n    "membership.upserted": "username",',
        "_ALSO_IDENTIFIED_BY: dict[str, str] = {}\n"
        "_UNUSED = {\n"
        '    "membership.upserted": "username",',
        "management/backend/tests/test_outbox_routing.py",
    ),
    # -- `FRD-209` §2.1's third route, which was specified, replicated to the gateway, and read by
    #    neither plane. A default argument is a silent one: `resolve()` has taken `direct` since the
    #    vocabulary was written, and both callers passed two arguments.
    Mutation(
        "N57",
        "a grant naming a person reaches the gateway, not only a grant naming their group",
        "gateway/src/aira_gateway/auth/grants.py",
        "        return resolve(held, grants, direct)",
        "        return resolve(held, grants)",
        "gateway/tests/test_group_grants.py",
    ),
    Mutation(
        "N58",
        "a caller with no groups still has their name looked up",
        "gateway/src/aira_gateway/auth/dependencies.py",
        "    if not principal.groups and not principal.username:",
        "    if not principal.groups:",
        "gateway/tests/test_group_grants.py",
    ),
    Mutation(
        "N59",
        "the console's answer about calling agrees with the gateway's",
        "management/backend/src/aira_management/apps/usecases/access.py",
        "    return queryset.filter(slug__in=list(resolve(held, grants, direct)))",
        "    return queryset.filter(slug__in=list(resolve(held, grants)))",
        "management/backend/tests/test_group_grants.py management/backend/tests/test_usecases.py",
    ),
    Mutation(
        "N27",
        "a page says there is more only when there is",
        "gateway/src/aira_gateway/api/reporting.py",
        "    stmt = stmt.order_by(RequestLog.created_at.desc(), RequestLog.id.desc()).limit(limit + 1)",
        "    stmt = stmt.order_by(RequestLog.created_at.desc(), RequestLog.id.desc()).limit(limit)",
        "gateway/tests/test_traces.py",
    ),
    # ---- the security round (2026-08-08) --------------------------------------------------
    #
    # Every one of these defends a fix for a finding that a green suite, 271 mutation properties
    # and four verification layers had all missed.
    Mutation(
        "H1",
        "a selector never grants access — an empty membership list means nothing, not anything",
        "gateway/src/aira_gateway/auth/dependencies.py",
        '    if principal.method == "oidc" and use_case not in principal.use_cases:',
        '    if principal.method == "oidc" and principal.use_cases and use_case not in principal.use_cases:',  # noqa: E501
        SELECTOR,
    ),
    Mutation(
        "H2",
        "the KIRA surface asks the shared rule rather than keeping its own",
        "gateway/src/aira_gateway/api/kira/attribution.py",
        # Re-anchored: `header` became `selector` when this surface started reading the `/uc/<slug>`
        # prefix as well — it had read only the header, so the prefix worked on the Gemini surface
        # and was invisible here. A rename; the property is untouched.
        "        refusal = use_case_refusal(principal, selector)",
        "        refusal = None",
        SELECTOR,
    ),
    Mutation(
        "H3",
        "open routes refuse to start outside local development",
        "gateway/src/aira_gateway/security.py",
        "    if not settings.auth_required:",
        "    if False:",
        DEPLOYMENT_SAFETY,
    ),
    Mutation(
        "H4",
        "the published development database password refuses to start",
        "gateway/src/aira_gateway/security.py",
        # Re-anchored 2026-08-15: each check now also asks whether a demo waives it.
        "        and settings.postgres_password == DEV_POSTGRES_PASSWORD",
        "        and False",
        DEPLOYMENT_SAFETY,
    ),
    Mutation(
        "H5",
        "OIDC without a named audience refuses to start",
        "gateway/src/aira_gateway/security.py",
        # Re-anchored 2026-08-15 with H4.
        "        and settings.oidc_enabled\n        and not settings.oidc_audience.strip()",
        "        and False",
        DEPLOYMENT_SAFETY,
    ),
    Mutation(
        "H6",
        "a laptop still starts with the convenience defaults",
        "gateway/src/aira_gateway/security.py",
        # Re-anchored 2026-08-15, and **narrowed on purpose**. It read "a laptop, *and a declared
        # demo*" and pointed at `if is_local(settings): return []` — the blanket that let one
        # environment variable switch off every check below, including authentication. A demo is a
        # deployment; what it waives is `WAIVED_BY_A_DEMO`, and H6b guards that it is a list rather
        # than a `return []` again.
        "    if is_local_environment(settings):\n        return []",
        "    if False:\n        return []",
        DEPLOYMENT_SAFETY,
    ),
    Mutation(
        "H6b",
        "a declared demo waives what a demo needs, and never authentication",
        "gateway/src/aira_gateway/security.py",
        "    waived = WAIVED_BY_A_DEMO if settings.demo_mode else frozenset[str]()",
        "    if settings.demo_mode:\n        return []\n    waived = frozenset[str]()",
        DEPLOYMENT_SAFETY,
    ),
    Mutation(
        "H7",
        "a credential in the request line does not reach the access log",
        "libs/src/aira_common/observability.py",
        '    return f"{path}?{redact_query_string(query)}"',
        "    return value",
        ACCESS_LOGS,
    ),
    Mutation(
        "H8",
        "the redaction is attached to the loggers the web server actually writes through",
        "libs/src/aira_common/logging.py",
        "    install_access_log_redaction()",
        "    pass",
        ACCESS_LOGS,
    ),
    Mutation(
        "H9",
        "a token with no expiry, issued-at or subject is refused",
        "libs/src/aira_common/oidc.py",
        '                    "require": ["exp", "iat", "sub"],',
        '                    "require": [],',
        AUTH,
    ),
    Mutation(
        "H10",
        "a persistent authentication prober is asked to wait",
        "gateway/src/aira_gateway/auth/dependencies.py",
        "        await record_failed_authentication(request)",
        "        pass",
        AUTH_BOUND,
    ),
    Mutation(
        "H11",
        "the bound counts refusals, so success never fills the bucket",
        "gateway/src/aira_gateway/auth/attempts.py",
        "    if decision.allowed:\n        return",
        "    if True:\n        return",
        AUTH_BOUND,
    ),
    Mutation(
        "H12",
        "an expired API key stops working on its own",
        "gateway/src/aira_gateway/auth/service.py",
        "        if record.expires_at is not None and self._aware(record.expires_at) <= datetime.now(UTC):",  # noqa: E501
        "        if False:",
        KEY_EXPIRY,
    ),
    Mutation(
        "H13",
        "the expiry Management decided survives the wire to the gateway",
        "management/backend/src/aira_management/apps/usecases/views.py",
        '                    "expires_at": expires_at.isoformat(),',
        '                    "expires_at": None,',
        KEY_EXPIRY,
    ),
    Mutation(
        "H14",
        "every response carries the headers that stop a browser sniffing it",
        "gateway/src/aira_gateway/app.py",
        "    app.add_middleware(SecurityHeadersMiddleware)",
        "    pass",
        SECURITY_HEADERS,
    ),
    Mutation(
        "H15",
        "a credential pasted into a prompt does not reach the stored row",
        "gateway/src/aira_gateway/persistence/writer.py",
        "                return self._redactor.redact(stripped)",
        "                return stripped",
        REDACTION,
    ),
    Mutation(
        "H16",
        "an unusable redaction pattern stops the gateway rather than matching nothing",
        "gateway/src/aira_gateway/persistence/redaction.py",
        '                raise RedactionMisconfigured(\n                    f"Redaction pattern {pattern!r} is not a valid regular expression: {exc}"\n                ) from exc',  # noqa: E501
        "                continue",
        REDACTION,
    ),
    Mutation(
        "H17",
        "a deployment's own pattern is added to the built-ins, never substituted for them",
        "gateway/src/aira_gateway/persistence/redaction.py",
        "    return PatternRedactor(BUILTIN_PATTERNS + extra)",
        "    return PatternRedactor(extra or BUILTIN_PATTERNS)",
        REDACTION,
    ),
    Mutation(
        "H18",
        "a key issued without asking still gets the configured lifetime",
        "management/backend/src/aira_management/apps/apikeys/serializers.py",
        '            attrs["expires_in_days"] = settings.api_key_default_days',
        '            attrs["expires_in_days"] = None',
        KEY_EXPIRY,
    ),
    Mutation(
        "H19",
        "a lifetime past the configured maximum is refused rather than granted",
        "management/backend/src/aira_management/apps/apikeys/serializers.py",
        "        if days > settings.api_key_max_days:",
        "        if False:",
        KEY_EXPIRY,
    ),
    Mutation(
        "H20",
        "the break-glass key minted by hand is bounded too",
        "gateway/src/aira_gateway/auth/service.py",
        "        days = expires_in_days if expires_in_days is not None else _default_key_days()",
        "        days = expires_in_days if expires_in_days is not None else 0",
        KEY_EXPIRY,
    ),
    # ---- tool calling across the dialects (`FRD-131`, 2026-08-08) --------------------------
    Mutation(
        "T20",
        "a use case that has not enabled tool calling cannot declare functions",
        "gateway/src/aira_gateway/api/serving.py",
        "    if record is None or not record.tools_enabled:",
        "    if False:",
        TOOLS,
    ),
    Mutation(
        "T21",
        "a model that does not declare tool calling is skipped rather than answering in prose",
        "gateway/src/aira_gateway/requirements.py",
        "        if declaration.can(Capability.TOOLS):\n            return None",
        "        if True:\n            return None",
        TOOLS,
    ),
    Mutation(
        "T22",
        "a streamed tool call is reassembled from its fragments, never emitted in pieces",
        "gateway/src/aira_gateway/upstreams/openai/mapping.py",
        '                entry["arguments"] += str(function["arguments"])',
        '                entry["arguments"] = str(function["arguments"])',
        TOOLS,
    ),
    Mutation(
        "T23",
        "the audit row records what the model asked to have run",
        "gateway/src/aira_gateway/api/serving.py",
        "        tool_calls=tool_summary(trail),",
        "        tool_calls=None,",
        TOOLS,
    ),
    Mutation(
        "T24",
        "the streamed exit records its tool calls too, not only the buffered one",
        "gateway/src/aira_gateway/api/gemini/routes.py",
        "                        streamed_calls.extend(call.name for call in chunk.tool_calls)",
        "                        pass",
        TOOLS,
    ),
    Mutation(
        "T25",
        "a streamed tool call reaches the client, not only the audit row",
        "gateway/src/aira_gateway/api/gemini/routes.py",
        "        for call in chunk.tool_calls\n    )",
        "        for call in ()\n    )",
        TOOLS,
    ),
    Mutation(
        "T26",
        "arguments never reach the metadata column",
        "gateway/src/aira_gateway/audit.py",
        '    return {"declared": trail.tools_declared, "called": list(trail.tool_calls)}',
        '    return {"declared": trail.tools_declared, "called": list(trail.tool_calls), '
        '"raw": str(trail.body)}',
        TOOLS,
    ),
    # `T27` **deleted** on 2026-08-08, not re-anchored. It defended "the structured-output tool is
    # not reported as a caller's tool call" — a property that existed only because that tool
    # existed. The provider gained `output_config`, the forced tool went, and with it the thing
    # this mutation protected. The harness reported it STALE within the hour, which is the second
    # time in one evening that it caught a rule outliving its mechanism (`T28` was the first).
    # A mutation whose subject is gone cannot fail, and one that cannot fail is a green light
    # about nothing.
    Mutation(
        "T28",
        "a schema this dialect cannot express skips the candidate rather than losing a constraint",
        "gateway/src/aira_gateway/requirements.py",
        "        refusal: str | None = check(self._schema)\n        return refusal",
        "        return None",
        "gateway/tests/test_tool_calling.py gateway/tests/test_vertex.py",
    ),
    # `T28` was re-anchored on 2026-08-08 rather than kept: it defended `ToolsAndSchemaTogether`,
    # which no longer exists — Anthropic gained a schema parameter and the conflict it guarded
    # against went with the mechanism that caused it. A mutation defending a deleted rule is worse
    # than no mutation: it reports green about nothing.
    Mutation(
        "T29",
        "prose is not a document, whatever the provider guarantees",
        "gateway/src/aira_gateway/upstreams/vertex/anthropic_mapping.py",
        "    try:\n        json.loads(text)\n    except ValueError:\n        return None\n    return text",
        "    return text",
        "gateway/tests/test_vertex.py",
    ),
    Mutation(
        "Q1",
        "a standing is the latest run, never a total across every run",
        "management/backend/src/aira_management/apps/smoketests/views.py",
        # Re-anchored twice. 2026-08-09: the battery axis went away, so the ordering was over the
        # model alone. 2026-08-16: `ADR-0020` made a run about a **use case**, so that is the axis
        # a standing is per. The property has never changed — summing every run makes an old,
        # since-corrected result drag the current one down forever.
        'order_by("use_case", "-started_at")',
        'order_by("use_case", "started_at")',
        "management/backend/tests/test_smoketests.py",
    ),
    Mutation(
        "Q1b",
        "a run may only be entered at a model the use case has been released",
        "management/backend/src/aira_management/apps/smoketests/views.py",
        # Re-anchored 2026-08-16. The property was "a run enters where the *pipeline* says"; the
        # owner removed `start_model` because pinning one model on a pipeline undoes the point of
        # releasing several to a use case. The caller picks again — and the bound is what stops
        # the picking from producing a run full of 403s, which is the half worth guarding.
        "        if chosen not in released:",
        "        if False:",
        "management/backend/tests/test_smoketests.py",
    ),
    Mutation(
        "Q1c",
        "a run may only be started in a use case this caller may run, asked per object",
        "management/backend/src/aira_management/apps/smoketests/views.py",
        "        use_case = may_run_tests_queryset(\n"
        "            self.request.user, UseCase.objects.filter(slug=slug)\n"
        "        ).first()",
        "        use_case = UseCase.objects.filter(slug=slug).first()",
        "management/backend/tests/test_smoketests.py",
    ),
    Mutation(
        "Q1d",
        "a use case with nothing released is refused by name rather than run against nothing",
        "management/backend/src/aira_management/apps/smoketests/views.py",
        '        if why_not:\n            raise ValidationError({"use_case": [why_not]})',
        '        if False:\n            raise ValidationError({"use_case": [why_not]})',
        "management/backend/tests/test_smoketests.py",
    ),
    Mutation(
        "Q1f",
        "running the catalogue takes administration of a use case, not membership of it",
        "management/backend/src/aira_management/apps/usecases/access.py",
        "    return get_objects_for_user(user, MANAGE, klass=reachable)",
        "    return reachable",
        "management/backend/tests/test_smoketests.py",
    ),
    Mutation(
        "Q1g",
        "an installation role is not a bypass of the gateway's acceptance",
        "management/backend/src/aira_management/apps/usecases/access.py",
        "    reachable = may_call_queryset(user, queryset)",
        "    reachable = queryset",
        "management/backend/tests/test_smoketests.py",
    ),
    Mutation(
        "Q2",
        "a run nobody has read is not a run that passed",
        "management/backend/src/aira_management/apps/smoketests/views.py",
        "counts[result.verdict] = counts.get(result.verdict, 0) + 1",
        "counts[result.verdict] = counts.get(result.verdict, 0)",
        "management/backend/tests/test_smoketests.py",
    ),
    Mutation(
        "Q5",
        "attribution offers what the gateway accepts, which grants a global admin no blanket",
        "management/backend/src/aira_management/apps/usecases/access.py",
        '    if not getattr(user, "is_authenticated", False):\n        return queryset.none()\n    held = held_group_paths(user)',
        "    if has_role(user, Role.GLOBAL_ADMIN):\n        return queryset\n    held = held_group_paths(user)",
        "management/backend/tests/test_usecases.py",
    ),
    Mutation(
        "Q6",
        "the seeded smoke-test use case is announced to the gateway, not only written locally",
        "management/backend/src/aira_management/apps/seed/contributions/test_catalogue.py",
        '        events.emit("usecase.upserted", _snapshot(use_case))',
        "        pass",
        "management/backend/tests/test_smoketests.py",
    ),
    Mutation(
        "Q3",
        "the seed corrects a renamed question in place instead of adding a second one",
        "management/backend/src/aira_management/apps/seed/contributions/test_catalogue.py",
        "                position=position,\n                retired=False,",
        "                topic=topic,\n                retired=False,",
        "management/backend/tests/test_smoketests.py",
    ),
    Mutation(
        "Q4",
        "a retired question is not asked, so a model is judged against the current standard",
        "management/backend/src/aira_management/apps/smoketests/views.py",
        "for case in TestCase.objects.filter(retired=False)",
        "for case in TestCase.objects.all()",
        "management/backend/tests/test_smoketests.py",
    ),
    Mutation(
        "T30",
        "the provider's required schema tightenings are added, not assumed",
        "gateway/src/aira_gateway/upstreams/vertex/anthropic_mapping.py",
        '        out["additionalProperties"] = False',
        "        pass",
        "gateway/tests/test_vertex.py",
    ),
    # ---- asking the vendor what it offers (`FRD-507` stage C, 2026-08-10) --------------------
    #
    # Six properties, and four of them are about a **silence**. The import fills a form somebody is
    # about to save, so every value that arrives without having been said becomes a declaration
    # nobody made — `FRD-114` FR-7 at the one moment it is hardest to notice, because a half-full
    # form looks like a working feature either way.
    Mutation(
        "I1",
        "only whoever may declare a model may ask a vendor what it offers",
        "gateway/src/aira_gateway/api/providers.py",
        "    if not may_catalogue(principal.roles):",
        "    if False:",
        PROVIDER_OFFERINGS,
    ),
    Mutation(
        "I2",
        "a vendor that said nothing about a verb has not said no",
        "gateway/src/aira_gateway/upstreams/gemini.py",
        "        can_generate=None if listed is None else bool(listed & _GENERATE_METHODS),",
        "        can_generate=bool(listed and listed & _GENERATE_METHODS),",
        PROVIDER_OFFERINGS,
    ),
    Mutation(
        "I3",
        "the listing is read to its last page, so no model is silently missing from the picker",
        "gateway/src/aira_gateway/upstreams/gemini.py",
        '            page_token = str(data.get("nextPageToken") or "")',
        '            page_token = ""',
        PROVIDER_OFFERINGS,
    ),
    Mutation(
        "I4",
        "a platform that cannot be asked says so rather than answering for one of two dialects",
        "gateway/src/aira_gateway/api/providers.py",
        "    if len(upstreams) > 1 or not can_enumerate(first):",
        "    if not can_enumerate(first):",
        PROVIDER_OFFERINGS,
    ),
    Mutation(
        "I5",
        "an adapter serving no configured model is still probed rather than passed over",
        "gateway/src/aira_gateway/diagnostics.py",
        "        return list(self.registry.each())",
        "        return [self.registry.provider_for(m.name) for m in self.registry.models()]",
        DIAGNOSTICS,
    ),
    Mutation(
        "I6",
        "cataloguing is enough to serve a model only where the name is the whole addressing",
        "gateway/src/aira_gateway/upstreams/openai/adapter.py",
        '        return self._provider if self._routes.names_models() else ""',
        "        return self._provider",
        FOUNDRY,
    ),
    # ---- which models a use case may call (`FRD-308`, 2026-08-11) -----------------------------
    #
    # The step this replaces was measured before it was replaced: it refused a model the caller
    # named and let both a routing rule and a fallback chain reach one it did not. So the first
    # two here are the holes, and they are checked where every other dispatch condition is.
    Mutation(
        "J1",
        "a model nobody released to this use case is refused, at every hop",
        "gateway/src/aira_gateway/requirements.py",
        "        if model in self._released:\n            return None",
        "        if True:\n            return None",
        RELEASE,
    ),
    Mutation(
        "J2",
        "an empty release is an answer, and the answer is no",
        "gateway/src/aira_gateway/requirements.py",
        "        if self._released is None:\n            return None",
        "        if not self._released:\n            return None",
        RELEASE,
    ),
    Mutation(
        "J3",
        "a use case no event has described is not treated as releasing nothing",
        "gateway/src/aira_gateway/api/serving.py",
        "    return None if released is None else [str(name) for name in released]",
        "    return [str(name) for name in (released or [])]",
        RELEASE,
    ),
    Mutation(
        "J4",
        "an event that says nothing about the release leaves the read-model alone",
        "gateway/src/aira_gateway/consumer/apply.py",
        "    if not isinstance(released, list):\n        return None",
        "    if not isinstance(released, list):\n        return []",
        "gateway/tests/test_consumer_apply.py",
    ),
    Mutation(
        "J5",
        "only an approved model can be released to a use case",
        "management/backend/src/aira_management/apps/usecases/serializers.py",
        "        unapproved = sorted(model.name for model in models if not model.approved)",
        "        unapproved: list[str] = []",
        "management/backend/tests/test_usecases.py",
    ),
    Mutation(
        "J6",
        "the release travels to the gateway, or it enforces yesterday's decision",
        "management/backend/src/aira_management/apps/usecases/views.py",
        '        "allowed_models": sorted(usecase.allowed_models.values_list("name", flat=True)),',
        '        "allowed_models": [],',
        "management/backend/tests/test_usecases.py",
    ),
    Mutation(
        "J7",
        "the migration carries an allow-list somebody chose, rather than dropping it",
        "management/backend/src/aira_management/apps/usecases/migrations/"
        "0010_release_models_from_allow_check.py",
        "        usecase.allowed_models.add(*Model.objects.filter(name__in=set(names)))",
        "        pass",
        "management/backend/tests/test_release_migration.py",
    ),
    # ---- the pipeline may only name models the use case may call (2026-08-11) ----------------
    #
    # `J8` is the one with a measurement behind it: the dry run posted any model name in a body and
    # the gateway **called it** — no use case, no release, no budget, no audit row.
    Mutation(
        "J8",
        "a dry run cannot call a model the use case was never released",
        "gateway/src/aira_gateway/api/pipeline.py",
        "        if withheld:",
        "        if False:",
        DRYRUN,
    ),
    Mutation(
        "J9",
        "every place a pipeline can name a model is checked, not just the first",
        "gateway/src/aira_gateway/api/pipeline.py",
        '    named.extend(str(name) for name in pipeline.get("fallback_models") or [] if name)',
        "    pass",
        DRYRUN,
    ),
    Mutation(
        "J10",
        "naming somebody else's use case does not borrow their release",
        "gateway/src/aira_gateway/api/pipeline.py",
        "    if refusal is not None:",
        "    if False:",
        DRYRUN,
    ),
    Mutation(
        "J11",
        "a saved pipeline cannot name a model the use case may not call",
        "management/backend/src/aira_management/apps/pipelines/serializers.py",
        "        if withheld:",
        "        if False:",
        "management/backend/tests/test_pipelines.py",
    ),
    # ---- every model call belongs to somebody (2026-08-11) -----------------------------------
    Mutation(
        "J12",
        "a dry run records and bills the model calls its steps made",
        "gateway/src/aira_gateway/api/pipeline.py",
        "        await record_pipeline_calls(request, trail)",
        "        pass",
        DRYRUN,
    ),
    Mutation(
        "J13",
        "an authenticated caller belonging to no use case is refused, not served unattributed",
        "gateway/src/aira_gateway/auth/dependencies.py",
        "    if not request.app.state.settings.require_use_case:\n        return False",
        "    if True:\n        return False",
        "gateway/tests/test_audit_completeness.py",
    ),
    Mutation(
        "J14",
        "the break-glass key keeps its exemption, so an outage is survivable",
        "gateway/src/aira_gateway/auth/dependencies.py",
        '    return not (principal.method == "api_key" and not principal.use_cases)',
        "    return True",
        "gateway/tests/test_audit_completeness.py",
    ),
    Mutation(
        "J15",
        "unattributed traffic cannot be switched back on outside local",
        "gateway/src/aira_gateway/security.py",
        "    if not settings.require_use_case:",
        "    if False:",
        DEPLOYMENT_SAFETY,
    ),
    Mutation(
        "J16",
        "a pipeline step reaches a model the catalog knows and configuration does not",
        "gateway/src/aira_gateway/pipeline/engine.py",
        "        return self._registry.provider_for(model, (await declaration_of(model)).provider)",
        "        return None",
        "gateway/tests/test_pipeline_engine.py",
    ),
    Mutation(
        "J17",
        "a router that could not be asked says so instead of reading as 'nothing matched'",
        "gateway/src/aira_gateway/pipeline/engine.py",
        # Re-anchored (2026-08-14) when the two step loops became one evaluation. Unchanged
        # property, and the refactor that moved it also found that the *dry run* answered this
        # case differently from `run` — which is what a second hand-written copy always ends up
        # doing.
        '                decision={"step": "model_route", "action": "not_asked", "why": "classifier_failed"},',
        '                decision={"step": "model_route", "action": "unchanged"},',
        "gateway/tests/test_pipeline_engine.py",
    ),
    Mutation(
        "J18",
        "reading the model list needs no use case; only spending does",
        "gateway/src/aira_gateway/auth/dependencies.py",
        "    if request.method in SPENDS_NOTHING:\n        # **A reading is not a model call.**",
        "    if False:\n        # **A reading is not a model call.**",
        "gateway/tests/test_audit_completeness.py",
    ),
    # ---- what a classifier tells a model about thinking (measured 2026-08-11) ----------------
    Mutation(
        "J19",
        "a model that cannot be told not to think is sent no thinking directive at all",
        "gateway/src/aira_gateway/thinking.py",
        "    if ThinkingMode.DISABLED not in declaration.thinking_modes:\n        return None",
        "    if False:\n        return None",
        CLASSIFIERS,
    ),
    Mutation(
        "J20",
        "an explicit off is still sent where the model can honour it",
        "gateway/src/aira_gateway/thinking.py",
        "    return resolve(Thinking(mode=ThinkingMode.DISABLED), declaration)",
        "    return None",
        CLASSIFIERS,
    ),
    Mutation(
        "J21",
        "asserting an off to a model that declares no thinking is not a thing we do",
        "gateway/src/aira_gateway/thinking.py",
        '        # never going to think and no parameter is needed" — which is exactly the case this branch\n        # is about. Asserting an off for a model that declares no thinking is a claim about the\n        # provider\'s API, and `FRD-124`\'s "off has to be said out loud" is about a model that\n        # **can** think: there, silence means the default wins. Here there is no default to beat.\n        return None',
        "        return Thinking(mode=ThinkingMode.DISABLED, tokens=0)",
        "gateway/tests/test_thinking.py",
    ),
    Mutation(
        "J22",
        "a model that will think gets room for the thinking as well as the word",
        "gateway/src/aira_gateway/pipeline/classifiers.py",
        "            else THINKING_CLASSIFIER_OUTPUT_TOKENS",
        "            else CLASSIFIER_OUTPUT_TOKENS",
        CLASSIFIERS,
    ),
    # -- 2026-08-11 quality/security review: three defects, each a rule the code claimed and did
    #    not have. Each mutation below was observed to fail before its fix was written.
    Mutation(
        "QA1",
        "a request with no chain still meets the dispatch conditions",
        "gateway/src/aira_gateway/api/serving.py",
        # **Re-anchored 2026-08-11**, within the hour of being written: the helper was generalised
        # from streams to every verb without a chain, so `canonical.model` became `model`. The
        # harness reported STALE rather than green, which is `N2`'s lesson working as built — a
        # mutation whose anchor has moved defends nothing and says so.
        "    refusal = await permits(model)",
        "    refusal = None",
        STREAM_CONDITIONS,
    ),
    Mutation(
        "QA2",
        "a streamed request is answered by the model routing chose, not the one it named",
        "gateway/src/aira_gateway/api/gemini/routes.py",
        "    provider = await resolve_direct_target(request, canonical.model, canonical)",
        "    provider = registry_of(request).provider_for(canonical.model)  # pre-routing lookup",
        STREAM_CONDITIONS,
    ),
    Mutation(
        "QA7",
        "an embedding meets the same conditions — the third instance of the :embedContent bypass",
        "gateway/src/aira_gateway/api/gemini/routes.py",
        "    provider = await resolve_direct_target(request, str(embed_request.model))",
        "    provider = registry_of(request).provider_for(str(embed_request.model))",
        STREAM_CONDITIONS,
    ),
    Mutation(
        "QA8",
        "the KIRA surface's embedding meets them too, from the same function",
        "gateway/src/aira_gateway/api/kira/routes.py",
        "            provider = await resolve_direct_target(request, model)",
        "            provider = registry_of(request).provider_for(model)\n"
        "            assert provider is not None",
        "gateway/tests/test_kira_surface.py "
        "gateway/tests/test_every_dispatch_applies_the_conditions.py",
    ),
    Mutation(
        "QA10",
        "a refusal raised before a KIRA route still answers in the KIRA envelope",
        "gateway/src/aira_gateway/app.py",
        # Re-anchored: the handler learned to separate "nothing was presented" from "what was
        # presented was rejected" for a 401, so the code is chosen above the return rather than in
        # it. The branch this switches off is the same one, and it is the one the property is
        # about — `_kira(request)` is what decides whose envelope a refusal goes out in.
        "        if _kira(request):\n            code = kira_code_for_status(exc.code)",
        "        if False:\n            code = kira_code_for_status(exc.code)",
        KIRA_ENVELOPE,
    ),
    Mutation(
        "QA11",
        "the body ceiling answers each surface in its own error language",
        "gateway/src/aira_gateway/middleware.py",
        "        body = self._KIRA_TOO_LARGE if path.startswith(KIRA_PREFIX) else self._GEMINI_TOO_LARGE",
        "        body = self._GEMINI_TOO_LARGE",
        KIRA_ENVELOPE,
    ),
    Mutation(
        "QA12",
        "a list of texts is one embedding, as the predecessor answers it",
        "gateway/src/aira_gateway/api/kira/mapping.py",
        # Re-anchored (2026-08-14): the blank-entry refusal moved into this mapper, so the join
        # is now its last two lines rather than its first. The property is unchanged — a list is
        # **one** embedding, and a mutation that returns one vector per element is the reading
        # `FRD-113` §11 assumed and the contract disproved.
        '        model=model, texts=["".join(entries)], task_type=request.task_type',
        "        model=model, texts=entries, task_type=request.task_type",
        KIRA_COMPAT,
    ),
    Mutation(
        "QA13",
        "the stream streams — updates carry the answer as it arrives, not after it",
        "gateway/src/aira_gateway/api/kira/routes.py",
        # Re-anchored 2026-08-15: the delta a caller receives now passes through
        # `StreamedNotice.lead`, which puts `FRD-309`'s notice in front of the first piece of text.
        # The property is unchanged — an `update` carries the answer as it is produced.
        '                    yield f"data: {json.dumps(update_event(led))}\\n\\n"',
        "                    pass  # the answer arrives only in the terminal event",
        KIRA_COMPAT,
    ),
    Mutation(
        "QA14",
        "a health check that can fail — the upstreams are in its verdict",
        "gateway/src/aira_gateway/api/kira/routes.py",
        # Re-anchored: the checks report the predecessor's `"Healthy"`/`"Unhealthy"` strings rather
        # than a boolean, so the verdict is read from `status`. The property is unchanged: a health
        # check whose answer does not depend on the upstreams is a health check that cannot fail.
        '    healthy = all(check.status == "Healthy" for check in checks)',
        "    healthy = True",
        KIRA_COMPAT,
    ),
    Mutation(
        "QA9",
        "the structural guard reaches inside a closure, where the hole actually was",
        "gateway/tests/test_every_dispatch_applies_the_conditions.py",
        "            if isinstance(child, Function):\n                chain = [child, *enclosing]",
        "            if isinstance(child, Function) and not enclosing:\n"
        "                chain = [child, *enclosing]",
        "gateway/tests/test_every_dispatch_applies_the_conditions.py",
    ),
    Mutation(
        "QA3",
        "a throttling suspension arrives at the limiter as a bucket it can read",
        "gateway/src/aira_gateway/api/serving.py",
        "        extra=[per_minute(t.key, t.limit_rpm, label=t.label) for t in throttles],",
        "        extra=throttles,  # type: ignore[arg-type]",
        THROTTLE_WIRE,
    ),
    Mutation(
        "QA4",
        "a rate of nothing per minute cannot reach a bucket and divide by zero",
        "gateway/src/aira_gateway/ratelimit/buckets.py",
        "    rate = max(MINIMUM_RPM, limit_rpm)",
        "    rate = limit_rpm",
        THROTTLE_WIRE,
    ),
    Mutation(
        "QA5",
        "a body refused on its size still carries the security headers",
        "gateway/src/aira_gateway/app.py",
        # The original ordering, restored verbatim — the mutation is the *order*, not the absence
        # of the middleware. Removing it altogether would fail the served case too and report a
        # property nobody had doubted; this one leaves the 200 intact and takes the 413 bare,
        # which is precisely what was measured.
        "    app.add_middleware(UseCasePathMiddleware)\n"
        "    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)\n"
        "    app.add_middleware(SecurityHeadersMiddleware)",
        "    app.add_middleware(SecurityHeadersMiddleware)\n"
        "    app.add_middleware(UseCasePathMiddleware)\n"
        "    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)",
        ERROR_HEADERS,
    ),
    Mutation(
        "QA6",
        "an unhandled error carries them too — the one response no middleware can reach",
        "gateway/src/aira_gateway/app.py",
        "        for name, value in SecurityHeadersMiddleware.HEADERS:",
        "        for name, value in ():",
        ERROR_HEADERS,
    ),
    Mutation(
        "QA15",
        "a stream hands each piece over as it is produced, not the lot at the end",
        "gateway/src/aira_gateway/api/kira/routes.py",
        # Re-anchored 2026-08-15 with QA13, for the same reason.
        "                    parts.append(led)\n"
        '                    yield f"data: {json.dumps(update_event(led))}\\n\\n"',
        "                    parts.append(led)\n"
        "                for piece in parts:\n"
        '                    yield f"data: {json.dumps(update_event(piece))}\\n\\n"',
        REALLY_STREAMS,
    ),
    Mutation(
        "QA16",
        "a refusal from a known caller is recorded on the compatibility surface too",
        "gateway/src/aira_gateway/api/kira/routes.py",
        # Re-anchored 2026-08-12, and the anchor was **ambiguous** before it was stale: the same
        # two lines stood in all three dispatching routes, and the harness edits the first match —
        # so this reported a property about `/chat` while `/streaming-chat` and `/embed` were
        # untouched. The three copies are now one function, which makes the anchor unique by
        # construction rather than by a longer string somebody has to keep unique by hand.
        "    resolve_attribution(request, principal)\n    body = await _json(request)",
        "    body = await _json(request)\n    resolve_attribution(request, principal)",
        REFUSAL_PARITY,
    ),
    Mutation(
        "QA17",
        "no spelling is accepted beyond the two the predecessor actually uses",
        "gateway/src/aira_gateway/api/kira/schemas.py",
        "    conversation_history: list[ConversationContent] | None = None",
        "    conversation_history: list[ConversationContent] | None = Field(\n"
        '        default=None, alias="conversationHistory"\n    )',
        FIELD_SPELLINGS,
    ),
    Mutation(
        "QA18",
        "a refusal a route raises is rendered in this surface's envelope, not as a 500",
        "gateway/src/aira_gateway/app.py",
        "    @app.exception_handler(KiraError)",
        "    @app.exception_handler(_NeverRaised)",
        KIRA_ENVELOPE,
    ),
    Mutation(
        "QA19",
        "health answers the predecessor's shape rather than one we invented",
        "gateway/src/aira_gateway/api/kira/routes.py",
        '            status="Healthy" if healthy else "Unhealthy",',
        '            status="Unhealthy" if healthy else "Healthy",',
        WIRE_CONTRACT,
    ),
    Mutation(
        "QA20",
        "a rejected credential is not reported as an absent one",
        "gateway/src/aira_gateway/api/kira/errors.py",
        "    return INVALID_TOKEN if credential_presented else NOT_AUTHENTICATED",
        "    return NOT_AUTHENTICATED",
        WIRE_CONTRACT,
    ),
    Mutation(
        "QA21",
        "the predecessor's newline between two text parts of one message",
        "gateway/src/aira_gateway/api/kira/mapping.py",
        'TEXT_PART_SEPARATOR = "\\n"',
        'TEXT_PART_SEPARATOR = ""',
        WIRE_CONTRACT,
    ),
    Mutation(
        "QA22",
        "the model an SDK writes into an embedding entry is accepted",
        "gateway/src/aira_gateway/api/gemini/schemas.py",
        "    model: str | None = None\n    taskType: str | None = None",
        "    taskType: str | None = None",
        GOOGLE_SDK,
    ),
    Mutation(
        "QA23",
        "an unfinished chunk carries no finish reason at all",
        "gateway/src/aira_gateway/api/gemini/routes.py",
        "if chunk.finish_reason else None",
        'if chunk.finish_reason else ""',
        GOOGLE_SDK,
    ),
    Mutation(
        "QA24",
        "a non-string text part is refused, never converted into a prompt",
        "gateway/src/aira_gateway/api/kira/schemas.py",
        '            if has_text and not isinstance(part["text"], str):',
        '            if has_text and not isinstance(part["text"], str | int | float | bool | type(None) | dict | list):',
        WIRE_CONTRACT,
    ),
    Mutation(
        "QA25",
        "the spelling the google-genai client actually puts on the wire for a thinking budget",
        "gateway/src/aira_gateway/api/gemini/schemas.py",
        '    thinkingBudget: int | None = Field(default=None, alias="thinking_budget")',
        "    thinkingBudget: int | None = None",
        GOOGLE_SDK,
    ),
    Mutation(
        "QA26",
        "asking for the model's reasoning is refused rather than answered without it",
        "gateway/src/aira_gateway/api/gemini/schemas.py",
        "        if self.includeThoughts:",
        "        if False:",
        GOOGLE_SDK,
    ),
    Mutation(
        "QA27",
        "two entities of one kind never share a compaction key",
        "management/backend/src/aira_management/apps/outbox/subscriber.py",
        '    "membership.upserted": "username",',
        "",
        COMPACTION_KEYS,
    ),
    Mutation(
        "QA28",
        "one config event that cannot be applied does not stop the others",
        "gateway/src/aira_gateway/consumer/worker.py",
        "    except Exception as exc:  # noqa: BLE001 — see the docstring: never take the consumer down",
        "    except ZeroDivisionError as exc:",
        CONSUMER_SURVIVES,
    ),
    Mutation(
        "QA29",
        "a detection round that fails gives its window back",
        "gateway/src/aira_gateway/anomalies/service.py",
        # Re-anchored 2026-08-16. The window was a set taken away at the top of `tick` and merged
        # back on failure; it is a watermark now, so keeping it means *not advancing* it — the
        # mutation moves it before the round instead of after (`FRD-127`).
        "            written = await self._evaluate(session, touched, moment)\n"
        "            if written:\n"
        "                await session.commit()\n"
        "        self._since = moment",
        "            self._since = moment\n"
        "            written = await self._evaluate(session, touched, moment)\n"
        "            if written:\n"
        "                await session.commit()",
        DETECTION_WINDOW,
    ),
    Mutation(
        "QA30",
        "a revocation that arrives over Kafka records when it happened",
        "gateway/src/aira_gateway/consumer/apply.py",
        "        if not active and record.revoked_at is None:\n"
        "            record.revoked_at = datetime.now(UTC)",
        "",
        REVOCATION_TIME,
    ),
    Mutation(
        "P10",
        "a rewrite that cannot be trusted blocks instead of being applied",
        "gateway/src/aira_gateway/pipeline/classifiers.py",
        "        if len(rewritten) < len(text.strip()) * self.MIN_KEPT:",
        "        if False:",
        PII,
    ),
    Mutation(
        "P11",
        "the request that goes upstream is the rewritten one",
        "gateway/src/aira_gateway/pipeline/engine.py",
        "            request=_with_user_text(request, result.text) if changed else None,",
        "            request=None,",
        PII,
    ),
    Mutation(
        "P12",
        "the **stored** request is the rewritten one too",
        "gateway/src/aira_gateway/api/serving.py",
        "        if needle not in text:\n            return None",
        "        if needle not in text:\n            return body",
        PII,
    ),
    Mutation(
        "P13",
        "a redactor that failed refuses rather than passing the original through",
        "gateway/src/aira_gateway/pipeline/engine.py",
        '            block_reason=None if allows else f"Personal data could not be removed: {why}.",',
        "            block_reason=None,",
        PII,
    ),
    Mutation(
        "P14",
        "a notice is never put in front of an answer a client parses",
        "gateway/src/aira_gateway/api/serving.py",
        # Re-anchored 2026-08-15, and the property it claimed was **not the one the code had**.
        # The old anchor tested `not response.text.strip() or response.tool_calls`, and a
        # schema-constrained answer is neither of those — it is a non-empty JSON document with no
        # tool call, so the notice went in front of it and the document stopped parsing. The
        # mutation passed because the two cases it *could* reach were guarded; the case the
        # property is named after had no check at all. It needs a fact about the request, which is
        # why `structured` is now a parameter.
        "    if structured:\n"
        '        return "the answer is a document the caller parses, and a sentence would '
        'invalidate it"\n',
        "",
        NOTICE,
    ),
    Mutation(
        "P14b",
        "a streamed answer is led by the notice, and only the first piece of text is",
        "gateway/src/aira_gateway/api/serving.py",
        '        led = "\\n\\n".join([*self._notices, text_delta])\n        self._notices = ()',
        '        led = "\\n\\n".join([*self._notices, text_delta])',
        NOTICE,
    ),
    Mutation(
        "P14c",
        "the compatibility surface applies the notice too, rather than only Google's",
        "gateway/src/aira_gateway/api/kira/routes.py",
        "            answer = annotate(canonical, dispatched.response, prepared, trail)",
        "            answer = dispatched.response",
        f"{NOTICE} gateway/tests/test_notice_reaches_every_exit.py",
    ),
    Mutation(
        "RV1",
        "a query parameter widens the list, and never which object a route resolves",
        "management/backend/src/aira_management/apps/usecases/views.py",
        '        if getattr(self, "action", None) != "list":\n            return False',
        "        if False:\n            return False",
        "management/backend/tests/test_may_call_never_widens_a_detail_route.py",
    ),
    Mutation(
        "RV2",
        "a findings restriction is written over the findings, not over unrelated rows",
        "gateway/src/aira_gateway/api/reporting.py",
        "    return or_(AnomalyEvent.use_case.notin_(restricted), or_(*own))",
        "    return or_(RequestLog.use_case.notin_(restricted), or_(*own))",
        "gateway/tests/test_scoped_reads_stay_on_their_table.py",
    ),
    Mutation(
        "RV3",
        "membership is read in the alphabet it was written in — the username, not the subject",
        "gateway/src/aira_gateway/payloads.py",
        "def _member_key(principal: Principal) -> str | None:\n    return principal.person",
        "def _member_key(principal: Principal) -> str | None:\n    return principal.subject",
        "gateway/tests/test_payload_access.py",
    ),
    Mutation(
        "RV4",
        "IT Security is not refused a per-use-case figure",
        "gateway/src/aira_gateway/api/usage.py",
        "    if not principal.is_oversight:",
        "    if not principal.is_governance:",
        "gateway/tests/test_oversight_reads_every_figure.py",
    ),
    Mutation(
        "RV5",
        "IT Security is not refused the predecessor's usage report either",
        "gateway/src/aira_gateway/api/kira/routes.py",
        "    if not principal.is_oversight:",
        "    if not principal.is_governance:",
        "gateway/tests/test_oversight_reads_every_figure.py",
    ),
    Mutation(
        "RV6",
        "an unexpected failure answers in the surface's own envelope",
        "gateway/src/aira_gateway/app.py",
        "        if _kira(request):\n"
        "            response = kira_error_response(500, kira_code_for_status(500), "
        '"Internal server error.")',
        "        if False:\n"
        "            response = kira_error_response(500, kira_code_for_status(500), "
        '"Internal server error.")',
        "gateway/tests/test_kira_envelope_everywhere.py",
    ),
    Mutation(
        "RV7",
        "the deployment's topology is shown to an operator, not to any credential",
        "gateway/src/aira_gateway/routes/health.py",
        "    return principal is not None and _is_operator(principal)",
        "    return principal is not None",
        "gateway/tests/test_readyz_diagnosis_is_for_operators.py",
    ),
    Mutation(
        "RV8",
        "a break-glass key is still an operator's credential",
        "gateway/src/aira_gateway/routes/health.py",
        '    return principal.method == "api_key" and not principal.use_cases',
        "    return False",
        "gateway/tests/test_readyz_diagnosis_is_for_operators.py",
    ),
    Mutation(
        "RV9",
        "one request reads a model's declaration once, however many controls ask",
        "gateway/src/aira_gateway/api/serving.py",
        "        memoised = source.per_request()\n        request.state.catalog = memoised",
        "        memoised = source.per_request()",
        "gateway/tests/test_a_request_costs_a_bounded_number_of_reads.py",
    ),
    Mutation(
        "RV10",
        "a budget counter always carries an expiry, including the one that refused",
        "gateway/src/aira_gateway/budgets/ledger.py",
        "redis.call('EXPIRE', key, ARGV[10])\n\nlocal current = redis.call('HMGET'",
        "\nlocal current = redis.call('HMGET'",
        "gateway/tests/test_budget_reservation.py",
    ),
    Mutation(
        "RV11",
        "operator-authored pipeline text is bounded by the gateway too, not only by Management",
        "gateway/src/aira_gateway/pipeline/config.py",
        "    bounded = {key: _clipped(value, where=key, step=step) for key, value in config.items()}",
        "    bounded = dict(config)",
        "gateway/tests/test_pipeline_store.py gateway/tests/test_pipeline_bounds.py",
    ),
    Mutation(
        "P15",
        "a routing notice is only given where a category actually matched",
        "gateway/src/aira_gateway/pipeline/engine.py",
        "            if template and category\n            else None",
        "            if template\n            else None",
        PII,
    ),
    Mutation(
        "QA31",
        "a pipeline's own model call is filed under the surface that caused it",
        "gateway/src/aira_gateway/api/serving.py",
        "                api=trail.api,",
        '                api="gemini",',
        SURFACE_PARITY,
    ),
    Mutation(
        "QA32",
        "a refused request still records the functions it offered",
        "gateway/src/aira_gateway/api/gemini/routes.py",
        "        tool_calls=tool_summary(trail),",
        "",
        SURFACE_PARITY,
    ),
    Mutation(
        "QA33",
        "both surfaces read a thinking mode string the same way",
        "gateway/src/aira_gateway/thinking.py",
        "        return ThinkingMode(raw.strip().lower())",
        "        return ThinkingMode(raw)",
        MODE_PARSE,
    ),
    # -- the dry run explains itself. The trace used to be a badge and a word, which says what
    #    happened and never why — and for the three LLM-backed steps the why is a model's own
    #    answer that nothing carried. These are what make the screen an explanation.
    Mutation(
        "QA34",
        "the dry run shows what the step's model actually replied",
        "gateway/src/aira_gateway/pipeline/engine.py",
        '        **({"output": _shown(reply)} if reply.strip() else {}),',
        "",
        CLASSIFIERS,
    ),
    Mutation(
        "QA35",
        "a redaction is shown as what it did to the caller's sentence, not as a badge",
        "gateway/src/aira_gateway/pipeline/engine.py",
        '                "before": _shown(original),',
        '                "before": "",',
        CLASSIFIERS,
    ),
    # -- the closed vocabulary, restated four times in a console that cannot import it. Three of
    #    the copies offered a kind that does not exist and omitted one that does; the fourth was a
    #    test asserting completeness against a list with the same two errors.
    # -- reporting and retention, audited with the same question as the three rounds before it.
    # -- `FRD-606`: what one person consumed, across both credentials.
    Mutation(
        "QA48",
        "a classifier call is counted by the budget and never by the rate-limit bucket",
        "gateway/src/aira_gateway/api/serving.py",
        "        units,",
        "        units + 1,",
        "gateway/tests/test_pipeline_accounting.py gateway/tests/test_ratelimit_routes.py",
    ),
    # -- `ADR-0019`: one human, one allowance, whichever credential.
    Mutation(
        "QA49",
        "an allowance is counted against the person, not the credential",
        "gateway/src/aira_gateway/scopes.py",
        "    return username or subject",
        "    return subject",
        "gateway/tests/test_one_person_one_allowance.py",
    ),
    Mutation(
        "QA50",
        "a credential that names nobody keys on its own subject, never on nothing",
        "gateway/src/aira_gateway/scopes.py",
        "    return username or subject",
        "    return username",
        "gateway/tests/test_one_person_one_allowance.py",
    ),
    Mutation(
        "QA51",
        "the budget the gate checks is the one the reservation is made against",
        "gateway/src/aira_gateway/api/serving.py",
        '    caller = getattr(attribution, "person", None)\n\n    expected = await estimate(',
        '    caller = getattr(attribution, "subject", None)\n\n    expected = await estimate(',
        "gateway/tests/test_one_person_one_allowance.py",
    ),
    Mutation(
        "QA46",
        "one person using two credentials is one figure, not two rows nothing joins",
        "gateway/src/aira_gateway/reporting/service.py",
        "    _PERSON = func.coalesce(RequestLog.username, RequestLog.subject)",
        "    _PERSON = RequestLog.subject",
        "gateway/tests/test_reporting.py",
    ),
    Mutation(
        "QA47",
        "the name is written beside the subject, or nothing can join a person to themselves",
        "gateway/src/aira_gateway/persistence/service.py",
        "            username=username,",
        "",
        "gateway/tests/test_persistence_service.py gateway/tests/test_persistence_recorder.py",
    ),
    Mutation(
        "QA43",
        "a payload whose use case no longer exists is still swept",
        "gateway/src/aira_gateway/retention.py",
        "                unknown=set(periods),",
        "",
        "gateway/tests/test_retention.py",
    ),
    Mutation(
        "QA44",
        "the orphan sweep does not shorten a use case's own retention",
        "gateway/src/aira_gateway/retention.py",
        "            criterion = criterion | RequestLog.use_case.not_in(unknown)",
        "            criterion = criterion | RequestLog.use_case.is_not(None)",
        "gateway/tests/test_retention.py",
    ),
    Mutation(
        "QA45",
        "a pipeline step's model call spends the request allowance",
        "gateway/src/aira_gateway/budgets/service.py",
        # **Re-aimed, not re-anchored.** It used to guard the opposite rule — that a step's call is
        # *not* a request — on the reporting side, because the budgets already booked zero and the
        # report counted rows. The owner reversed the rule on 2026-08-15 and both sides now count,
        # so the property worth defending is the booking itself: `0` is the value this silently
        # goes back to if the reversal is ever half-undone.
        "            requests=1,\n            subject=subject,",
        "            requests=0,\n            subject=subject,",
        "gateway/tests/test_budget_service.py gateway/tests/test_pipeline_accounting.py",
    ),
    Mutation(
        "QA41",
        "the console offers exactly the rule kinds that exist",
        "management/frontend/src/app/features/security/rule-form.ts",
        "  { value: 'blocked_prompt_rate', label: 'Too many prompts are being blocked by the pipeline' },",  # noqa: E501
        "  { value: 'token_spike', label: 'Token use jumped against the previous window' },",
        "tools/tests/test_the_console_speaks_the_closed_vocabulary.py",
    ),
    Mutation(
        "QA42",
        "every rule kind the vocabulary defines is one the engine measures",
        "gateway/src/aira_gateway/anomalies/evaluator.py",
        "    RuleKind.BLOCKED_PROMPT_RATE: frozenset({Outcome.BLOCKED_BY_PIPELINE.value}),",
        "",
        "gateway/tests/test_every_rule_kind_is_measured.py",
    ),
    Mutation(
        "QA40",
        "an endpoint that spends without dispatching still takes the controls that stop spending",
        "gateway/src/aira_gateway/api/pipeline.py",
        "        await guard_before_work(request)",
        "        pass",
        "gateway/tests/test_pipeline_dryrun.py gateway/tests/test_every_spender_takes_the_gate.py",
    ),
    Mutation(
        "QA37",
        "the dry run stops where production stops unless it was asked not to",
        "gateway/src/aira_gateway/pipeline/engine.py",
        "                if not past_blocks:\n                    break",
        "                pass",
        "gateway/tests/test_pipeline_engine.py gateway/tests/test_pipeline_dryrun.py",
    ),
    Mutation(
        "QA38",
        "a step that only ran past a block is marked as the simulation it is",
        "gateway/src/aira_gateway/pipeline/engine.py",
        "            trace.append(TraceEntry(evaluation.type, evaluation.action, evaluation.detail, blocked))",  # noqa: E501
        "            trace.append(TraceEntry(evaluation.type, evaluation.action, evaluation.detail, False))",  # noqa: E501
        "gateway/tests/test_pipeline_engine.py gateway/tests/test_pipeline_dryrun.py",
    ),
    Mutation(
        "QA39",
        "the console's keep-going option reaches the engine",
        "gateway/src/aira_gateway/api/pipeline.py",
        "            past_blocks=payload.past_blocks,",
        "",
        "gateway/tests/test_pipeline_dryrun.py",
    ),
    Mutation(
        "QA36",
        "a router's answer names the model that gave it, not the one routed to",
        "gateway/src/aira_gateway/pipeline/engine.py",
        "                        routing.reply,\n                        model,",
        "                        routing.reply,",
        CLASSIFIERS,
    ),
]


def _recover() -> None:
    """Put back whatever a previous run was holding when it died."""
    if not JOURNAL.exists():
        return
    entry = json.loads(JOURNAL.read_text())
    path = ROOT / entry["path"]
    if path.read_text() != entry["original"]:
        path.write_text(entry["original"])
        print(f"Recovered {entry['path']} from an interrupted run.", flush=True)
    JOURNAL.unlink()


def _pytest(selection: str) -> bool:
    """True if the suite passed."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *selection.split(), "-x", "-q", "--no-cov"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _refuse_duplicate_ids() -> None:
    """An id must name exactly one property.

    On 2026-08-07 a live round found **38 ids naming more than one**, which makes every report
    ambiguous: "N3 survived" says nothing when two unrelated properties answer to `N3`, and
    `--only=N3` silently runs both. The duplicates were renamed then and this check was recorded as
    the fix — it was never written, and the very next addition (2026-08-09) collided with `S1` and
    `S2` and the harness reported four confident results for two requested properties.

    A check that only exists in a commit message is the thing it was meant to prevent.
    """
    seen: dict[str, str] = {}
    clashes = []
    for mutation in MUTATIONS:
        if mutation.ident in seen:
            clashes.append(
                f"  {mutation.ident}: {seen[mutation.ident]!r} and {mutation.property_defended!r}"
            )
        seen[mutation.ident] = mutation.property_defended
    if clashes:
        print("Two properties share an id, so no result about it can be read:")
        print("\n".join(clashes))
        raise SystemExit(2)


def _refuse_ambiguous_anchors() -> None:
    """An anchor must name exactly one place.

    This module's own docstring has stated the rule since it was written, and nothing checked it —
    so on 2026-08-12 three anchors matched two or three places each. `path.write_text(...replace(
    old, new, 1))` edits the **first** match, which means an ambiguous anchor quietly reports on
    whichever copy comes first in the file. `C2` named the model catalog's "no such row" branch and
    was editing the "un-lookupable name" branch three lines above it: a different property, a
    different test, and a confident `caught`.

    That is worse than the stale anchors found the same day. A stale one at least prints `STALE`;
    this one prints a result that reads exactly like a working guard.

    Refused before the run rather than reported after it, like the duplicate-id check above and for
    the same reason: no result about an ambiguous anchor can be read, so producing one is worse
    than producing none. `tools/tests/test_mutation_anchors.py` asks the same question in
    milliseconds, so this is the backstop rather than the working check — the whole-run harness is
    not what anybody runs before pushing.
    """
    ambiguous = []
    for mutation in MUTATIONS:
        count = (ROOT / mutation.path).read_text().count(mutation.old)
        if count > 1:
            ambiguous.append(f"  {mutation.ident}: matches {count} places in {mutation.path}")
    if ambiguous:
        print("These anchors name more than one place, and only the first would be edited:")
        print("\n".join(ambiguous))
        print("Widen the anchor, or remove the duplication in the source it is pointing at.")
        raise SystemExit(2)


def main() -> int:
    _refuse_duplicate_ids()
    _refuse_ambiguous_anchors()
    _recover()
    # `--only A1,B2` runs a subset. Added 2026-08-08 after a five-mutation change cost a full
    # 306-property run: the whole set is what CI checks, and the whole set is not what somebody
    # iterating on one property needs. The baseline check narrows with it, or the subset run pays
    # for suites it never touches.
    wanted: set[str] | None = None
    for argument in sys.argv[1:]:
        if argument.startswith("--only="):
            wanted = {ident.strip() for ident in argument.removeprefix("--only=").split(",")}
        elif argument == "--only":
            index = sys.argv.index(argument)
            wanted = {ident.strip() for ident in sys.argv[index + 1].split(",")}
        elif argument.startswith("-"):
            print(f"Unknown option {argument!r}. The only one is --only=A1,B2.")
            return 2

    chosen = [m for m in MUTATIONS if wanted is None or m.ident in wanted]
    if wanted is not None:
        missing = wanted - {m.ident for m in chosen}
        if missing:
            # Refused rather than silently skipped: a typo that runs nothing looks exactly like a
            # subset that passes.
            print(f"No such mutation: {', '.join(sorted(missing))}")
            return 2
        print(f"Running {len(chosen)} of {len(MUTATIONS)} properties.", flush=True)

    selections = sorted({mutation.tests for mutation in chosen})
    print("Checking the baseline is green before trusting any result…", flush=True)
    for selection in selections:
        if not _pytest(selection):
            print(f"BASELINE RED for '{selection}'. Fix the suite first — with a red baseline")
            print("every mutation looks 'caught' and this tool tells you nothing.")
            return 2

    survivors: list[Mutation] = []

    for mutation in chosen:
        path = ROOT / mutation.path
        original = path.read_text()
        if mutation.old not in original:
            print(f"{mutation.ident:<4} STALE     anchor gone from {mutation.path}", flush=True)
            survivors.append(mutation)
            continue
        JOURNAL.write_text(json.dumps({"path": mutation.path, "original": original}))
        try:
            path.write_text(original.replace(mutation.old, mutation.new, 1))
            unnoticed = _pytest(mutation.tests)
        finally:
            path.write_text(original)
            JOURNAL.unlink(missing_ok=True)
        assert path.read_text() == original, f"failed to restore {mutation.path}"

        status = "SURVIVED" if unnoticed else "caught"
        print(f"{mutation.ident:<4} {status:<9} {mutation.property_defended}", flush=True)
        if unnoticed:
            survivors.append(mutation)

    print()
    if survivors:
        # A stale anchor and a surviving mutation are different problems with the same consequence,
        # and saying "no test would notice losing this" about a stale one sends the reader looking
        # for a missing test that is probably right there. Walked into on 2026-08-09.
        stale = [m for m in survivors if m.old not in (ROOT / m.path).read_text()]
        survived = [m for m in survivors if m not in stale]
        print(f"{len(survivors)} of {len(chosen)} properties are unguarded:")
        for mutation in survived:
            print(f"  {mutation.ident}  SURVIVED  {mutation.property_defended}")
        for mutation in stale:
            print(f"  {mutation.ident}  STALE     {mutation.property_defended}")
        if survived:
            print("\nA survivor is a property no test would notice losing. Add the test.")
        if stale:
            print("\nA stale anchor points at code that has moved. Re-anchor it, or remove it if")
            print("the rule it guarded is gone — a mutation defending deleted code is worse than")
            print("none, because it reports green about nothing.")
        return 1
    print(f"All {len(chosen)} properties are defended by at least one test.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
