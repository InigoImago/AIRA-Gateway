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

MUTATIONS = [
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
        "            self.degraded = True\n            return await self._local.take(requests)",
        "            self.degraded = True\n            return ALLOWED",
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
