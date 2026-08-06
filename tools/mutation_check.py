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
- **Some properties cannot live here, and saying so is part of being honest.** "A client dropping
  a real socket still leaves its request settled and logged" is one: closing a generator in-process
  raises `GeneratorExit` and a bare `await` in a `finally` runs fine, so no hermetic test can tell
  the shielded version from the unshielded one. It is guarded by `tests/integration/
  test_request_path.py` instead. A mutation that survives here would be a false claim, and a
  harness that makes one is worse than no harness.
- Keep the test selection **wide enough**. A too-narrow selection reports a false gap: M25 was
  first reported as surviving only because the test that catches it lives in another file, and
  T10/E8 repeated the mistake later. When a mutation survives, check *which files run* before
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
MGMT_HARDENING = "management/backend/tests/test_hardening.py"
MGMT_SETTINGS = (
    "management/backend/tests/test_settings.py management/backend/tests/test_hardening.py"
)
MONEY = "libs/tests/test_money.py"
COST = "gateway/tests/test_cost_budgets.py"
CATALOG = "management/backend/tests/test_catalog.py"
PIPELINE = "gateway/tests/test_pipeline_engine.py gateway/tests/test_pipeline_routes.py"
RETENTION = "gateway/tests/test_retention.py gateway/tests/test_store_payloads.py"
MODEL_CATALOG = "gateway/tests/test_model_catalog.py"
VERTEX = "gateway/tests/test_vertex.py"
REQUIREMENTS = "gateway/tests/test_dispatch_requirements.py"
ATTACHMENTS = "gateway/tests/test_attachments.py"
KIRA = "gateway/tests/test_kira_surface.py"
TOKENS = "libs/tests/test_tokens.py"
CATALOG_DECLARATION = "management/backend/tests/test_catalog_declaration.py"
OPENAI_DIALECT = "gateway/tests/test_openai_dialect.py"
THINKING = "gateway/tests/test_thinking.py gateway/tests/test_serving_options.py"
RESPONSE_SCHEMA = "gateway/tests/test_response_schema.py gateway/tests/test_serving_options.py"
EMBEDDING = "gateway/tests/test_embedding_options.py gateway/tests/test_serving_options.py"
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
        '                options={"verify_aud": self._audience is not None},',
        '                options={"verify_aud": False},',
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
        "a governance role sees every use case, a normal user only their own",
        "management/backend/src/aira_management/rbac.py",
        "    if has_governance_role(user):",
        "    if True:",
        MGMT_RBAC,
    ),
    Mutation(
        "G2",
        "a role removed from the token is removed in Django",
        "management/backend/src/aira_management/rbac.py",
        "            user.groups.remove(group)",
        "            pass",
        MGMT_RBAC,
    ),
    Mutation(
        "G3",
        "editing a use case needs the change permission, not mere visibility",
        "management/backend/src/aira_management/apps/usecases/views.py",
        "        return has_role(user, Role.GLOBAL_ADMIN) or user.has_perm(_CHANGE, usecase)",
        "        return has_role(user, Role.GLOBAL_ADMIN) or user.has_perm(_VIEW, usecase)",
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
        "adding a membership grants the permission it promises",
        "management/backend/src/aira_management/apps/usecases/views.py",
        "    assign_perm(_VIEW, user, usecase)",
        "    pass",
        MGMT_RBAC,
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
        "                    record.unpriced_requests += requests",
        "                    record.cost_nanos += 0",
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
        "        return [IsAuthenticated(), IsGlobalAdmin()]",
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
        'if action == "block":',
        'if action == "no_such_action":',
        PIPELINE,
    ),
    Mutation(
        "P2",
        "an injection filter set to flag does not refuse the request",
        "gateway/src/aira_gateway/pipeline/engine.py",
        'if action == "block":\n',
        "if True:\n",
        PIPELINE,
    ),
    Mutation(
        "P3",
        "the allow-check refuses a model that is not on the list",
        "gateway/src/aira_gateway/pipeline/engine.py",
        "return bool(allowed) and request.model not in allowed",
        "return bool(allowed) and request.model in allowed",
        PIPELINE,
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
        "    if _NESTED_QUANTIFIER.search(pattern):",
        "    if _NESTED_QUANTIFIER.search(pattern) and False:",
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
        "                session, None, now - timedelta(days=self._default_retention_days)",
        "                session, None, now - timedelta(days=self._default_retention_days * 1000)",
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
        "        if not self._enforce or not use_case:\n            return",
        "        if True:\n            return",
        f"{RATELIMIT} {RATELIMIT_ROUTES}",
    ),
    Mutation(
        "M4",
        "an unset burst means the per-minute figure, not zero",
        "gateway/src/aira_gateway/ratelimit/service.py",
        "    return record.burst if record.burst and record.burst > 0 else record.limit_rpm",
        "    return record.burst",
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
        "            await self._check_only(session, budgets, now)",
        "            pass",
        f"{BUDGET_RESERVATION} {BUDGET_SERVICE}",
    ),
    Mutation(
        "M23",
        "every verb passes the pre-dispatch controls, not only the generate ones",
        "gateway/src/aira_gateway/api/gemini/routes.py",
        "    reservation = await enforce_pre_dispatch(",
        "    reservation = Reservation() if embed_request else await enforce_pre_dispatch(",
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
        "        self._stopping = True\n        await self._queue.join()",
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
        "S1",
        "a member-scoped row binds only its own subject",
        "gateway/src/aira_gateway/scopes.py",
        "        if scope == MEMBER and caller and subject == caller:",
        "        if scope == MEMBER and caller:",
        "gateway/tests/test_scopes.py gateway/tests/test_budget_service.py gateway/tests/test_ratelimit.py",
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
        "GOVERNANCE_ROLES: frozenset[Role] = frozenset({Role.GLOBAL_ADMIN, Role.IT_STEUERUNG, Role.USE_CASE_ADMIN})",
        "libs/tests/test_roles.py management/backend/tests/test_rbac.py",
    ),
    Mutation(
        "O2",
        "a malformed roles claim yields no oversight rather than an error",
        "gateway/src/aira_gateway/auth/attribution.py",
        "    if not isinstance(access, dict):\n        return ()",
        "    if not isinstance(access, dict):\n        raise ValueError(access)",
        "gateway/tests/test_attribution.py",
    ),
    Mutation(
        "O3",
        "the roles a token carries reach the principal",
        "gateway/src/aira_gateway/auth/oidc.py",
        "            roles=realm_roles(claims),",
        "            roles=(),",
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
        "gateway/src/aira_gateway/api/kira/routes.py",
        "        reservation = await enforce_pre_dispatch(\n            request,\n            model=canonical.model,\n            max_output_tokens=canonical.max_output_tokens,\n            attachments=[part.media_type for part in canonical.attachments],\n            extra_tokens=reserved_tokens(canonical.thinking),\n        )\n        async with request.app.state.budgets.hold(reservation):",
        "        reservation = Reservation()\n        async with request.app.state.budgets.hold(reservation):",
        KIRA,
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
        '        body["system"] = "\\n\\n".join(system_parts)',
        '        body["system"] = system_parts[-1]',
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
        "            return ModelDeclaration(name=model)",
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
        "N2",
        "oversight is what grants the view across use cases, not merely being authenticated",
        "gateway/src/aira_gateway/api/reporting.py",
        "    if principal.is_governance:\n        return None",
        "    if principal.is_governance:\n        return principal.use_cases",
        "gateway/tests/test_reporting.py",
    ),
    Mutation(
        "N3",
        "unpriced traffic is counted apart, never summed into spend as zero",
        "gateway/src/aira_gateway/reporting/service.py",
        'func.sum(case((RequestLog.cost_nanos.is_(None), 1), else_=0)).label("unpriced_requests")',
        'func.sum(case((RequestLog.cost_nanos.is_(None), 0), else_=0)).label("unpriced_requests")',
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
        "T5",
        "a thinking budget below the model's minimum is refused",
        "gateway/src/aira_gateway/thinking.py",
        "    if minimum is not None and tokens < minimum:",
        "    if minimum is not None and tokens < 0:",
        THINKING,
    ),
    Mutation(
        "T6",
        "a thinking budget above the model's maximum is refused",
        "gateway/src/aira_gateway/thinking.py",
        "    if maximum is not None and tokens > maximum:",
        "    if maximum is not None and tokens > maximum * 1000:",
        THINKING,
    ),
    Mutation(
        "T7",
        "a model's declared default thinking is applied when the caller sends none",
        "gateway/src/aira_gateway/thinking.py",
        "    if requested is None:\n        return _default_for(declaration)",
        "    if requested is None:\n        return None",
        THINKING,
    ),
    Mutation(
        "T8",
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
        "S1",
        "an unknown schema field is refused rather than dropped",
        "gateway/src/aira_gateway/core/schema.py",
        'model_config = ConfigDict(populate_by_name=True, extra="forbid")',
        'model_config = ConfigDict(populate_by_name=True, extra="ignore")',
        RESPONSE_SCHEMA,
    ),
    Mutation(
        "S2",
        "the schema's nesting depth is bounded",
        "gateway/src/aira_gateway/core/schema.py",
        "    if depth > bounds.max_depth:",
        "    if depth > bounds.max_depth * 1000:",
        RESPONSE_SCHEMA,
    ),
    Mutation(
        "S3",
        "the schema's total property count is bounded across the whole tree",
        "gateway/src/aira_gateway/core/schema.py",
        "    if properties > bounds.max_properties:",
        "    if properties > bounds.max_properties * 1000:",
        RESPONSE_SCHEMA,
    ),
    Mutation(
        "S4",
        "the capability is checked against the model dispatched to, not the one requested",
        "gateway/src/aira_gateway/requirements.py",
        "        if declaration.can(Capability.STRUCTURED_OUTPUT):\n            return None",
        "        if True:\n            return None",
        SERVING_OPTIONS,
    ),
    Mutation(
        "S5",
        "a schema always travels with the media type that makes the provider honour it",
        "gateway/src/aira_gateway/upstreams/gemini_mapping.py",
        '        generation_config["responseMimeType"] = "application/json"',
        '        generation_config.pop("responseMimeType", None)',
        SERVING_OPTIONS,
    ),
    Mutation(
        "S6",
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
        "    await request.app.state.rate_limits.check(use_case, subject, units)",
        "    await request.app.state.rate_limits.check(use_case, subject, 1)",
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
        "B8",
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
        "O1",
        "the verb is split off the *last* colon, so a model name may contain one",
        "gateway/src/aira_gateway/api/gemini/routes.py",
        '    model, separator, method = resource.rpartition(":")',
        '    model, separator, method = resource.partition(":")',
        OPENAI_DIALECT,
    ),
    Mutation(
        "O2",
        "a streamed request asks for the usage the vendor otherwise never reports",
        "gateway/src/aira_gateway/upstreams/openai/mapping.py",
        '        body["stream_options"] = {"include_usage": True}',
        "        pass",
        OPENAI_DIALECT,
    ),
    Mutation(
        "O3",
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


def main() -> int:
    _recover()
    selections = sorted({mutation.tests for mutation in MUTATIONS})
    print("Checking the baseline is green before trusting any result…", flush=True)
    for selection in selections:
        if not _pytest(selection):
            print(f"BASELINE RED for '{selection}'. Fix the suite first — with a red baseline")
            print("every mutation looks 'caught' and this tool tells you nothing.")
            return 2

    survivors: list[Mutation] = []
    for mutation in MUTATIONS:
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
        print(f"{len(survivors)} of {len(MUTATIONS)} properties are undefended:")
        for mutation in survivors:
            print(f"  {mutation.ident}  {mutation.property_defended}")
        print("\nEach one is a property no test would notice losing. Add the test.")
        return 1
    print(f"All {len(MUTATIONS)} properties are defended by at least one test.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
