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
- Keep the test selection **wide enough**. A too-narrow selection reports a false gap: M25 was
  first reported as surviving only because the test that catches it lives in another file.
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
        "                    record.unpriced_requests += 1",
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
        "            self._state[request.key] = (tokens - 1 if decision.allowed else tokens, now)",
        "            self._state[request.key] = (tokens - 1, now)",
        RATELIMIT,
    ),
    Mutation(
        "M2",
        "every applicable scope is checked, not just the first",
        "gateway/src/aira_gateway/ratelimit/service.py",
        "        decision = await self._bucket.take(buckets)",
        "        decision = await self._bucket.take(buckets[:1])",
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
        "            return await self._local.take(requests)",
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
        "        reservation = await _enforce_pre_dispatch(",
        "        reservation = Reservation() if embed_request else await _enforce_pre_dispatch(",
        RATELIMIT_ROUTES,
    ),
    Mutation(
        "M24",
        "the reservation uses the caller's own output bound where it gave one",
        "gateway/src/aira_gateway/api/gemini/routes.py",
        "    tokens = max_output_tokens or settings.budget_estimate_output_tokens",
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
