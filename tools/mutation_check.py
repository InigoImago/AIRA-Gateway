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

import argparse
import contextlib
import enum
import hashlib
import json
import os
import pathlib
import re
import signal
import subprocess
import sys
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parent.parent

# A killed run cannot restore anything from its own `finally`, and a source file left mutated is
# a booby trap for whoever runs the suite next — it looks like a real defect and wastes the day.
# So the original is written to disk *before* the edit and removed only once it is back.
JOURNAL = ROOT / ".mutation-journal.json"

#: How long one run of one test selection may take, in seconds, before it is stopped and reported.
#: A mutant can make a test wait for ever, and a harness that waits with it reports nothing at all.
#: Generous, because a console spec is built before it runs; `--timeout` overrides it.
TIMEOUT_SECONDS = 900.0

#: A selection entry that the console's own runner, `ng test` (Vitest), runs rather than pytest.
SPEC_SUFFIX = ".spec.ts"
FRONTEND = ROOT / "management" / "frontend"
#: What `ng test` prints when the application did not build; such a run reaches no test.
NG_BUILD_FAILED = "Application bundle generation failed"
#: Vitest's summary line, which only a run that executed test files prints.
NG_TESTS_RAN = re.compile(r"^\s*Test Files\s", re.MULTILINE)
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
#: pytest's exit codes for a failing test and for a test module that failed to load. 3, 4 and 5 —
#: an internal error, a usage error, nothing collected — are runs that asked the tests nothing.
PYTEST_FAILED = frozenset({1, 2})


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
IDENTITY = "management/backend/tests/test_an_identity_is_set_once.py"
CONSOLE_ROLES = "tools/tests/test_the_console_agrees_about_who_may_do_what.py"
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
NESTING = (
    "gateway/tests/test_a_body_cannot_be_nested_out_of_the_audit_trail.py "
    "libs/tests/test_nesting.py"
)
MGMT_SWEEP = "management/backend/tests/test_a_callers_value_is_never_a_server_error.py"
PATTERNS = "libs/tests/test_patterns.py"
ACCOUNTABLE = "gateway/tests/test_a_number_cannot_switch_the_budget_off.py"
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
EMBED_PII = "gateway/tests/test_a_redactor_reaches_an_embedding.py"
CSV_EXPORT = "gateway/tests/test_csv_export.py"
SERIES = "gateway/tests/test_usage_series.py gateway/tests/test_csv_export.py"
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
KIRA_TOLERANCE = "gateway/tests/test_kira_field_spellings.py gateway/tests/test_no_silent_drop.py"
SURFACE_PARITY = (
    "gateway/tests/test_pipeline_accounting.py "
    "gateway/tests/test_every_surface_records_alike.py "
    "gateway/tests/test_tool_calling.py"
)
MODE_PARSE = "gateway/tests/test_thinking.py gateway/tests/test_kira_surface.py"

#: The readers of a caller's identity, all of them. Wide on purpose: the whole lesson of
#: `FRD-613` is that these properties are held by *different* files from the ones the code sits
#: beside — the suspension rule fails in the sweep, not in `test_suspensions.py`.
IDENTITY_SWEEP = (
    "gateway/tests/test_one_identity_across_credentials.py "
    "gateway/tests/test_the_identity_boundary_takes_any_value.py "
    "gateway/tests/test_own_requests_are_the_persons.py "
    "gateway/tests/test_payload_access.py"
)
#: The bus is two processes and a table, so the property lives in three files at once.
TRACE_BUS = (
    "gateway/tests/test_a_trace_crosses_the_bus.py "
    "management/backend/tests/test_outbox.py libs/tests/test_observability.py "
    "libs/tests/test_kafka.py"
)
ACCESS_LIFECYCLE = (
    "management/backend/tests/test_access_ends_completely.py "
    "management/backend/tests/test_apikeys.py"
)

SERVING_OPTIONS = (
    "gateway/tests/test_serving_options.py gateway/tests/test_kira_surface.py "
    "gateway/tests/test_vertex.py gateway/tests/test_gemini_upstream.py"
)

MUTATIONS = [
    Mutation(
        "UI-21",
        "a line break inside a stored prompt is shown as a line break, not as \\n",
        "management/frontend/src/app/core/ui/readable-json.ts",
        "    character === 'n' ? '\\n' : character === 't' ? '\\t' : escape,",
        "    escape,",
        "management/frontend/src/app/features/use-cases/traces-tab.spec.ts",
    ),
    # ---- the platform tables: the essentials in columns, the rest behind a click ----------------
    Mutation(
        "UI-19",
        "a content read's request, ground, roles and access open under the row, not all at once",
        "management/frontend/src/app/features/platform/content-reads-page.html",
        "            @if (openRow() === row.id) {\n",
        "            @if (true) {\n",
        "management/frontend/src/app/features/platform/content-reads-page.spec.ts",
    ),
    Mutation(
        "UI-20",
        "what a role change did opens under its row, one row at a time",
        "management/frontend/src/app/features/platform/role-changes.html",
        "            @if (openRow() === change.id) {\n",
        "            @if (true) {\n",
        "management/frontend/src/app/features/platform/roles-page.spec.ts",
    ),
    # ---- strict data mode, the console's polish (FRD-627) --------------------------------------
    Mutation(
        "UI-1",
        "the prompt-and-response button is offered to a reader who may read any content",
        "management/frontend/src/app/features/use-cases/traces-tab.ts",
        "    if (can(me, 'payload.read_any')) return true;\n",
        "    if (false) return true;\n",
        "management/frontend/src/app/features/use-cases/traces-tab.spec.ts",
    ),
    Mutation(
        "UI-2",
        "no content button where the use case leaves its content to oversight, strict mode included",
        "management/frontend/src/app/features/use-cases/traces-tab.ts",
        "    return mode !== 'not-stored' && mode !== 'oversight';\n",
        "    return mode !== 'not-stored';\n",
        "management/frontend/src/app/features/use-cases/traces-tab.spec.ts",
    ),
    Mutation(
        "UI-3",
        "no content button where the use case stores no content",
        "management/frontend/src/app/features/use-cases/traces-tab.ts",
        "    return mode !== 'not-stored' && mode !== 'oversight';\n",
        "    return mode !== 'oversight';\n",
        "management/frontend/src/app/features/use-cases/traces-tab.spec.ts",
    ),
    Mutation(
        "UI-4",
        "the content button and its note render only for a reader who can read",
        "management/frontend/src/app/features/use-cases/traces-tab.html",
        "} @else if (mayReadContent()) {",
        "} @else {",
        "management/frontend/src/app/features/use-cases/traces-tab.spec.ts",
    ),
    Mutation(
        "UI-5",
        "an opened request's details are rendered from the server's recorded answer, not the list",
        "management/frontend/src/app/features/use-cases/traces-tab.ts",
        "        if (body.trace.id === row.id && this.openRow() === row.id) this.detail.set(body.trace);\n",
        "        if (body.trace.id === row.id && this.openRow() === row.id) this.detail.set(row);\n",
        "management/frontend/src/app/features/use-cases/traces-tab.spec.ts",
    ),
    Mutation(
        "UI-6",
        "the details of one request are never placed under another's row",
        "management/frontend/src/app/features/use-cases/traces-tab.ts",
        "        if (body.trace.id === row.id && this.openRow() === row.id) this.detail.set(body.trace);\n",
        "        if (this.openRow() === row.id) this.detail.set(body.trace);\n",
        "management/frontend/src/app/features/use-cases/traces-tab.spec.ts",
    ),
    Mutation(
        "UI-7",
        "a refused opening of a request's details reaches the page banner",
        "management/frontend/src/app/features/use-cases/traces-tab.ts",
        "        this.feedback.fail(response, 'Could not open the details of this request.');\n",
        "        void response;\n",
        "management/frontend/src/app/features/use-cases/traces-tab.spec.ts",
    ),
    Mutation(
        "UI-8",
        "the row toggle is labelled as opening the details, not as reading the content",
        "management/frontend/src/app/features/use-cases/traces-tab.html",
        "openRow() === row.id ? 'Hide the details' : 'Show the details'",
        "openRow() === row.id ? 'Hide the details' : 'Show the prompt and response'",
        "management/frontend/src/app/features/use-cases/traces-tab.spec.ts",
    ),
    Mutation(
        "UI-9",
        "the key filter waits for /me, so a strict-mode reader is not offered a refused filter",
        "management/frontend/src/app/features/use-cases/traces-tab.ts",
        "    return me !== null && !peopleWithheld(me);\n",
        "    return !peopleWithheld(me);\n",
        "management/frontend/src/app/features/use-cases/traces-tab.spec.ts",
    ),
    Mutation(
        "UI-10",
        "what each person used is shown only to a reader who manages every use case",
        "management/frontend/src/app/features/use-cases/use-case-detail.ts",
        "computed(() => this.managesAll() && !this.peopleLimited())",
        "computed(() => !this.peopleLimited())",
        "management/frontend/src/app/features/use-cases/use-case-detail.spec.ts",
    ),
    Mutation(
        "UI-11",
        "what each person used is not shown when the gateway narrowed the report",
        "management/frontend/src/app/features/use-cases/use-case-detail.ts",
        "computed(() => this.managesAll() && !this.peopleLimited())",
        "computed(() => this.managesAll())",
        "management/frontend/src/app/features/use-cases/use-case-detail.spec.ts",
    ),
    Mutation(
        "UI-12",
        "the member breakdown is not offered to a reader narrowed to their own row",
        "management/frontend/src/app/features/reporting/reporting-page.html",
        "            @if (!peopleLimited()) {\n",
        "            @if (true) {\n",
        "management/frontend/src/app/features/reporting/reporting-page.spec.ts",
    ),
    Mutation(
        "UI-13",
        "a member breakdown left on screen is replaced when a narrowed report arrives",
        "management/frontend/src/app/features/reporting/reporting-page.ts",
        "        if (report.people_limited === true && this.exportBreakdown() === 'member') {\n",
        "        if (false) {\n",
        "management/frontend/src/app/features/reporting/reporting-page.spec.ts",
    ),
    Mutation(
        "UI-14",
        "the content-read log asks the server for the chosen kind",
        "management/frontend/src/app/features/platform/content-reads-page.ts",
        "      kind: this.kind(),\n",
        "      kind: '' as const,\n",
        "management/frontend/src/app/features/platform/content-reads-page.spec.ts",
    ),
    Mutation(
        "UI-15",
        "an entry from before kinds were recorded reads as a content read",
        "management/frontend/src/app/features/platform/content-reads-page.ts",
        "    return KIND_LABELS[row.kind ?? 'content'];\n",
        "    return KIND_LABELS[row.kind ?? 'metadata'];\n",
        "management/frontend/src/app/features/platform/content-reads-page.spec.ts",
    ),
    Mutation(
        "UI-16",
        "the kind filter reaches the gateway as a query parameter",
        "management/frontend/src/app/core/api/clients/incidents.ts",
        "      if (options.kind) params['kind'] = options.kind;\n",
        "\n",
        "management/frontend/src/app/core/api/use-case.service.spec.ts",
    ),
    Mutation(
        "UI-17",
        "the strict-mode explanation is a callout, not a footnote-sized hint",
        "management/frontend/src/app/features/use-cases/data-protection-panel.html",
        '<p class="callout" style="margin: 0" data-testid="strict-data-modes">',
        '<p class="field__hint" style="margin: 0" data-testid="strict-data-modes">',
        "management/frontend/src/app/features/use-cases/data-protection-panel.spec.ts",
    ),
    Mutation(
        "UI-18",
        "each content-mode radio sits in a checkline, where the radio reset aligns it",
        "management/frontend/src/app/features/use-cases/data-protection-panel.html",
        '<label class="checkline content-modes__option">',
        '<label class="content-modes__option">',
        "management/frontend/src/app/features/use-cases/data-protection-panel.spec.ts",
    ),
    # ---- strict data mode, the gateway (FRD-627) ----------------------------------------------
    Mutation(
        "SDG-20",
        "opening a request's details is recorded as such, not as reading its content",
        "gateway/src/aira_gateway/api/reporting/traces.py",
        "        session.add(_access(principal, row, ground=ground, kind=ACCESS_METADATA))\n",
        "        session.add(_access(principal, row, ground=ground, kind=ACCESS_CONTENT))\n",
        "gateway/tests/test_strict_data_mode.py",
    ),
    Mutation(
        "SDG-21",
        "a request's details are scoped as the list is, and out of scope leave no record",
        "gateway/src/aira_gateway/api/reporting/traces.py",
        "            or (scope is not None and row.use_case not in scope)\n",
        "",
        "gateway/tests/test_strict_data_mode.py",
    ),
    Mutation(
        "SDG-22",
        "a request's details withhold another person as the list does",
        "gateway/src/aira_gateway/api/reporting/traces.py",
        '    return JSONResponse(\n        {"trace": _listed(listed, principal, withholding=withholding), "recorded": True}\n    )\n',
        '    return JSONResponse(\n        {"trace": _listed(listed, principal, withholding=False), "recorded": True}\n    )\n',
        "gateway/tests/test_strict_data_mode.py",
    ),
    Mutation(
        "SDG-23",
        "the access log filters by what was opened",
        "gateway/src/aira_gateway/api/incidents/content_reads.py",
        "        conditions.append(PayloadAccess.kind == kind)\n",
        "        pass\n",
        "gateway/tests/test_strict_data_mode.py",
    ),
    Mutation(
        "SC-1",
        "switching the showcase's mode replaces its line rather than adding a second",
        "tools/showcase_mode.py",
        "            lines[index] = wanted\n",
        "            lines.append(wanted)\n",
        "tools/tests/test_showcase_mode.py",
    ),
    Mutation(
        "SC-2",
        "a .env rendered from a configuration file is refused, not edited",
        "tools/showcase_mode.py",
        "    if RENDERED_STAMP in text:\n        raise Refused(",
        "    if False:\n        raise Refused(",
        "tools/tests/test_showcase_mode.py",
    ),
    Mutation(
        "SC-3",
        "the showcase's pseudonym key is drawn once and then kept",
        "tools/showcase_mode.py",
        "        if separator and name.strip() == KEY_NAME and value.strip():\n            return False\n",
        "        pass\n",
        "tools/tests/test_showcase_mode.py",
    ),
    Mutation(
        "SD-10",
        "a key whose owner is withheld says another member, never a blank owner",
        "management/frontend/src/app/features/use-cases/api-keys-panel.html",
        "              @if (key.owner_withheld) {\n",
        "              @if (false) {\n",
        "management/frontend/src/app/features/use-cases/api-keys-panel.spec.ts",
    ),
    Mutation(
        "SDG-16",
        "a key's owner is withheld from everybody but the owner and the incident roles",
        "management/backend/src/aira_management/apps/apikeys/serializers.py",
        '        return name not in {key.owner.get_username(), key.issued_by or ""}\n',
        "        return False\n",
        "management/backend/tests/test_apikeys.py",
    ),
    Mutation(
        "SDG-17",
        "a request span names its caller by pseudonym under strict data mode",
        "gateway/src/aira_gateway/auth/attribution.py",
        '        "aira.subject": pseudonym(attribution.subject),\n',
        '        "aira.subject": attribution.subject,\n',
        "gateway/tests/test_strict_data_mode.py",
    ),
    Mutation(
        "SDG-18",
        "every log line leaves with a pseudonym, not a name, under strict data mode",
        "libs/src/aira_common/logging.py",
        "        pseudonymise_event,\n",
        "",
        "libs/tests/test_pseudonym.py",
    ),
    Mutation(
        "SDG-19",
        "a pseudonym is keyed, so a directory cannot be hashed back to names",
        "libs/src/aira_common/pseudonym.py",
        '    _key = secret.encode("utf-8") if secret else secrets.token_bytes(32)\n',
        '    _key = b"fixed"\n',
        "libs/tests/test_pseudonym.py",
    ),
    Mutation(
        "SDG-15",
        "a gateway too old to know the mode still reads every use case as restricted",
        "management/backend/src/aira_management/apps/usecases/views/payloads.py",
        '        "restrict_members_to_own_requests": get_settings().strict_data_mode\n',
        '        "restrict_members_to_own_requests": False\n',
        "management/backend/tests/test_usecases.py",
    ),
    Mutation(
        "SDG-13",
        "an anonymous Grafana visitor is a Viewer, who cannot query any span ad hoc",
        "deploy/compose/docker-compose.yml",
        '      GF_AUTH_ANONYMOUS_ORG_ROLE: "Viewer"\n',
        '      GF_AUTH_ANONYMOUS_ORG_ROLE: "Admin"\n',
        "tools/tests/test_grafana_names_no_caller.py",
    ),
    Mutation(
        "SDG-14",
        "the shipped dashboard selects no caller",
        "deploy/compose/grafana/dashboards/aira-overview.json",
        "span.aira.status, span.aira.total_tokens)",
        "span.aira.status, span.aira.total_tokens, span.aira.subject)",
        "tools/tests/test_grafana_names_no_caller.py",
    ),
    Mutation(
        "SDG-1",
        "under strict data mode every use case is oversight, whatever it stored",
        "gateway/src/aira_gateway/payloads.py",
        "    return PayloadVisibility.OVERSIGHT_ONLY.value if strict_data else stored\n",
        "    return stored\n",
        "gateway/tests/test_strict_data_mode.py",
    ),
    Mutation(
        "SDG-2",
        "another person's request row is listed without its name and key",
        "gateway/src/aira_gateway/payloads.py",
        "    return not sees_people(principal) and not is_own_request(principal, row)\n",
        "    return False\n",
        "gateway/tests/test_strict_data_mode.py",
    ),
    Mutation(
        "SDG-3",
        "reading every figure is no exemption from the split by person",
        "gateway/src/aira_gateway/api/reporting/spend.py",
        "        return None if sees_people(principal) else _own_name(principal)\n",
        "        return None\n",
        "gateway/tests/test_strict_data_mode.py",
    ),
    Mutation(
        "SDG-4",
        "no split by person carries a latency",
        "gateway/src/aira_gateway/api/reporting/spend.py",
        "    if strict_data:\n        _without_latency_per_person(report)\n",
        "    if False:\n        _without_latency_per_person(report)\n",
        "gateway/tests/test_strict_data_mode.py",
    ),
    Mutation(
        "SDG-5",
        "the member CSV has no latency columns",
        "gateway/src/aira_gateway/api/reporting/spend.py",
        "        latency=not (strict_data and breakdown in PERSON_BREAKDOWNS),\n",
        "        latency=True,\n",
        "gateway/tests/test_strict_data_mode.py",
    ),
    Mutation(
        "SDG-6",
        "KIRA usage reports each use case and no person",
        "gateway/src/aira_gateway/api/kira/info_routes.py",
        '    per = "by_use_case" if request.app.state.settings.strict_data_mode else "by_member"\n',
        '    per = "by_member"\n',
        "gateway/tests/test_strict_data_mode.py",
    ),
    Mutation(
        "SDG-7",
        "filtering requests by person or key is refused to whoever does not see people",
        "gateway/src/aira_gateway/api/reporting/traces.py",
        "    if withholding and (subject or credential):\n",
        "    if False:\n",
        "gateway/tests/test_strict_data_mode.py",
    ),
    Mutation(
        "SDG-8",
        "a finding names nobody but the person it is about",
        "gateway/src/aira_gateway/api/reporting/anomalies.py",
        "    withheld = withheld and row.target in PERSON_TARGETS\n",
        "    withheld = False\n",
        "gateway/tests/test_strict_data_mode.py",
    ),
    Mutation(
        "SDG-9",
        "a stop on a person names them to the incident roles only",
        "gateway/src/aira_gateway/api/incidents/suspensions.py",
        "    withholding = settings_of(request).strict_data_mode and not sees_people(principal)\n",
        "    withholding = False\n",
        "gateway/tests/test_strict_data_mode.py",
    ),
    Mutation(
        "SDG-10",
        "no stored role holds what sees a person under strict data mode",
        "libs/src/aira_common/permissions.py",
        "    return GRANTABLE - STRICT_DATA_RESERVED if strict_data else GRANTABLE\n",
        "    return GRANTABLE\n",
        "gateway/tests/test_strict_data_mode.py",
    ),
    Mutation(
        "SDG-11",
        "the register states the visibility that decides, not the one stored",
        "gateway/src/aira_gateway/reporting/register.py",
        "                row.payload_visibility, strict_data=self._strict_data\n",
        "                row.payload_visibility, strict_data=False\n",
        "gateway/tests/test_strict_data_mode.py",
    ),
    Mutation(
        "SDG-12",
        "the gateway's role resolver applies the installation's data mode",
        "gateway/src/aira_gateway/app.py",
        "        sessionmaker, settings.parsed_role_groups(), strict_data=settings.strict_data_mode\n",
        "        sessionmaker, settings.parsed_role_groups(), strict_data=False\n",
        "gateway/tests/test_strict_data_mode.py",
    ),
    # ---- tolerating a caller without losing what they sent (FRD-107/124, 2026-08-18) -----
    Mutation(
        "TC1",
        "a near-miss spelling is still refused, not quietly ignored",
        "gateway/src/aira_gateway/api/kira/schemas.py",
        "            if match and str(sent) != match:",
        "            if False:",
        KIRA_TOLERANCE,
    ),
    Mutation(
        "TC2",
        "a field this surface does not model is named on the response",
        "gateway/src/aira_gateway/api/kira/headers.py",
        "        request.state.unmodelled = names",
        "        pass",
        KIRA_TOLERANCE,
    ),
    Mutation(
        "TC3",
        "the header reaches every exit, not only the one somebody remembered",
        "gateway/src/aira_gateway/api/kira/headers.py",
        "        **({UNMODELLED_HEADER: unmodelled_header(unmodelled)} if unmodelled else {}),",
        "        # header intentionally dropped",
        KIRA_TOLERANCE,
    ),
    Mutation(
        "TC4",
        "a strict client's additionalProperties is forwarded, not refused",
        "gateway/src/aira_gateway/core/schema.py",
        '    additional_properties: bool | None = Field(default=None, alias="additionalProperties")',
        "    # field intentionally removed from the vocabulary",
        "gateway/tests/test_response_schema.py gateway/tests/test_kira_surface.py",
    ),
    Mutation(
        "TC7",
        "a lost audit row is said to be lost on every surface, not only the first one",
        "gateway/src/aira_gateway/api/serving/accounting.py",
        '            "audit_refusal_not_recorded",',
        '            "audit_refusal_quietly_dropped",',
        "gateway/tests/test_surface_layering.py",
    ),
    Mutation(
        "TC8",
        "the console composes its paths from one statement of the prefixes",
        "management/frontend/src/app/core/api/prefixes.ts",
        "export const API = PREFIXES.management;",
        'export const API = "/api";',
        "tools/tests/test_the_console_addresses_one_set_of_prefixes.py",
    ),
    Mutation(
        "TC9",
        "moving a published port moves everything that talks to it",
        "tools/stack_addresses.py",
        "        for source in (os.environ, from_file):",
        "        for source in (from_file,):",
        "tools/tests/test_one_owner_for_the_stack_addresses.py",
    ),
    Mutation(
        "TC45",
        "cataloguing a Vertex model is enough to serve it — the publisher chooses the dialect",
        "gateway/src/aira_gateway/upstreams/base.py",
        "        return self._by_provider.get((provider, publisher)) if publisher else None",
        "        return None",
        "gateway/tests/test_vertex.py",
    ),
    Mutation(
        "TC46",
        "a catalogued model is addressed from its declaration rather than guessed at",
        "gateway/src/aira_gateway/upstreams/vertex/regions.py",
        "    regions = _declared_regions(addressing)",
        "    regions = ()",
        "gateway/tests/test_vertex.py",
    ),
    Mutation(
        "TC47",
        "an address the operator got wrong is a refusal naming it, not a server error",
        "gateway/src/aira_gateway/pipeline/dispatch.py",
        # Widened to the comment below it when `stream_with_fallback` gained the same `except`:
        # an anchor that matches twice is one the harness refuses, and rightly — it would mutate
        # whichever came first and report about the other.
        "        except (AmbiguousModel, RegionNotAllowed) as exc:\n            # A candidate",
        "        except NotImplementedError as exc:\n            # A candidate",
        "gateway/tests/test_no_silent_drop.py",
    ),
    Mutation(
        "TC44",
        "a caller is told what thinking cost, which is most of the bill",
        "gateway/src/aira_gateway/api/gemini/mapping.py",
        "            thoughtsTokenCount=response.usage.reasoning_tokens or None,",
        "            thoughtsTokenCount=None,",
        "gateway/tests/test_reasoning.py gateway/tests/test_gemini_routes.py",
    ),
    Mutation(
        "TC42",
        "a mode a dialect cannot express is refused, never silently omitted",
        "gateway/src/aira_gateway/upstreams/vertex/anthropic_mapping/request.py",
        "    if budget is None:",
        "    if False:",
        "gateway/tests/test_no_silent_drop.py",
    ),
    Mutation(
        "TC39",
        "a thinking level list of the old table shape is refused rather than read as empty",
        "management/backend/src/aira_management/apps/catalog/validation.py",
        "    if not isinstance(levels, list) or not all(isinstance(level, str) for level in levels):",
        "    if False:",
        "management/backend/tests/test_catalog_declaration.py",
    ),
    Mutation(
        "TC40",
        "a thinking mode declared as a vendor level word is refused",
        "management/backend/src/aira_management/apps/catalog/validation.py",
        "    clashes = sorted(set(words) & {str(member) for member in ThinkingMode})",
        "    clashes = []",
        "management/backend/tests/test_catalog_declaration.py",
    ),
    Mutation(
        "TC41",
        "a thinking block that offers neither a mode nor a level is refused",
        "management/backend/src/aira_management/apps/catalog/validation.py",
        '    if not modes and not block.get("levels"):',
        "    if False:",
        "management/backend/tests/test_catalog_declaration.py",
    ),
    Mutation(
        "TC5",
        "a level word reaches this dialect as the caller said it, never approximated",
        "gateway/src/aira_gateway/upstreams/openai/mapping/request.py",
        "    return setting.mode",
        '    return "low"',
        "gateway/tests/test_openai_dialect.py",
    ),
    Mutation(
        "SD1",
        "deleting a use case retires it and never removes the row",
        "management/backend/src/aira_management/apps/usecases/views/lifecycle.py",
        "            .update(deleted_at=moment, deleted_by=by, updated_at=moment)",
        "            .delete()[0]",
        "management/backend/tests/test_soft_delete.py",
    ),
    Mutation(
        "SD2",
        "only a Global Administrator may purge a retired use case",
        "management/backend/src/aira_management/apps/usecases/views/retirement.py",
        "        if not may(request.user, Permission.USECASE_PURGE):",
        "        if False:",
        "management/backend/tests/test_soft_delete.py",
    ),
    Mutation(
        "SD3",
        "a use case must already be retired before it can be purged",
        "management/backend/src/aira_management/apps/usecases/views/retirement.py",
        "        if usecase is None or usecase.deleted_at is None:",
        "        if usecase is None:",
        "management/backend/tests/test_soft_delete.py",
    ),
    Mutation(
        "SD4",
        "a purge waits out the decision period rather than following the deletion",
        "management/backend/src/aira_management/apps/usecases/views/retirement.py",
        "        if waited < timedelta(days=PURGE_AFTER_DAYS):",
        "        if False:",
        "management/backend/tests/test_soft_delete.py",
    ),
    Mutation(
        "SD5",
        "a retired use case is absent from every route that reaches one",
        "management/backend/src/aira_management/apps/usecases/models.py",
        "        return self.filter(deleted_at__isnull=True)",
        "        return self.all()",
        "management/backend/tests/test_soft_delete.py",
    ),
    Mutation(
        "SD6",
        "retiring keeps the gateway row so the use case's own retention period survives",
        "gateway/src/aira_gateway/consumer/apply.py",
        "        update(UseCaseRead).where(UseCaseRead.slug == slug).values(deleted_at=datetime.now(UTC))",  # noqa: E501
        "        delete(UseCaseRead).where(UseCaseRead.slug == slug)",
        "gateway/tests/test_consumer_apply.py gateway/tests/test_retention.py",
    ),
    Mutation(
        "SD7",
        "a retired use case serves nothing, even to a token whose group still names it",
        "gateway/src/aira_gateway/api/serving/controls.py",
        "    if record.deleted_at is not None:",
        "    if False:",
        "gateway/tests/test_a_retired_use_case_serves_nothing.py",
    ),
    Mutation(
        "SD8",
        "the refusal of a use case unknown here or retired runs before anything is spent",
        "gateway/src/aira_gateway/api/serving/prepare.py",
        "    await refuse_unless_live(request, use_case_of(request))\n    if canonical is not None:",
        "    if canonical is not None:",
        "gateway/tests/test_a_retired_use_case_serves_nothing.py",
    ),
    Mutation(
        "TC50",
        "a reachability check answers about the provenance in the form, not the stored row",
        "gateway/src/aira_gateway/api/incidents/diagnostics.py",
        "    asked_provider = provider or declaration.provider",
        "    asked_provider = declaration.provider",
        "gateway/tests/test_model_check.py",
    ),
    Mutation(
        "V22",
        "a failure about the place moves to the next region",
        "gateway/src/aira_gateway/upstreams/vertex/regions.py",
        "            if exc.status_code not in REGION_FAILOVER_STATUSES:",
        "            if True:",
        "gateway/tests/test_region_failover.py",
    ),
    Mutation(
        "V23",
        "a failure about the request is not retried in another region",
        "gateway/src/aira_gateway/upstreams/vertex/regions.py",
        "REGION_FAILOVER_STATUSES = frozenset({404, 408, 429, 500, 502, 503, 504})",
        "REGION_FAILOVER_STATUSES = frozenset({400, 401, 403, 404, 408, 422, 429, 500, 502, 503, 504})",  # noqa: E501
        "gateway/tests/test_region_failover.py",
    ),
    Mutation(
        "V24",
        "a region the residency policy forbids is stepped over, not fatal",
        "gateway/src/aira_gateway/upstreams/vertex/regions.py",
        "        except RegionNotAllowed as exc:\n            last = exc",
        "        except RegionNotAllowed:\n            raise",
        "gateway/tests/test_region_failover.py",
    ),
    Mutation(
        "V25",
        "the answer carries the region that produced it, for the audit row",
        "gateway/src/aira_gateway/upstreams/vertex/adapters.py",
        '            return answer.model_copy(update={"served_region": region})\n\n        return await _across_regions(self._targets_for(request.model, request.addressing), attempt)\n\n    def _stream_attempt(self, request: CanonicalRequest) -> StreamAttempt:\n        body = canonical_to_gemini_request(request)',  # noqa: E501
        "            return answer\n\n        return await _across_regions(self._targets_for(request.model, request.addressing), attempt)\n\n    def _stream_attempt(self, request: CanonicalRequest) -> StreamAttempt:\n        body = canonical_to_gemini_request(request)",  # noqa: E501
        "gateway/tests/test_region_failover.py",
    ),
    Mutation(
        "V26",
        "the audit row prefers the region that answered over the configured one",
        "gateway/src/aira_gateway/api/serving/context.py",
        "        return (described.provider, described.publisher, served_region or described.region)",
        "        return (described.provider, described.publisher, described.region)",
        "gateway/tests/test_region_failover.py",
    ),
    Mutation(
        "V27",
        "every declared region is checked, not only the first",
        "gateway/src/aira_gateway/api/incidents/model_check.py",
        '    for asked_region in asked_regions or [""]:',
        '    for asked_region in (asked_regions or [""])[:1]:',
        "gateway/tests/test_model_check.py",
    ),
    Mutation(
        "D20",
        "a check that spends money leaves an audit row",
        "gateway/src/aira_gateway/api/incidents/thinking_check.py",
        '            await _record_diagnostic(\n                request,\n                principal,\n                operation=f"models:checkThinking:{word}",',  # noqa: E501
        '            if False:\n                await _record_diagnostic(\n                request,\n                principal,\n                operation=f"models:checkThinking:{word}",',  # noqa: E501
        "gateway/tests/test_model_check.py",
    ),
    Mutation(
        "D21",
        "a diagnostic is not counted as served traffic",
        "gateway/src/aira_gateway/api/incidents/diagnostics.py",
        "            outcome=Outcome.DIAGNOSTIC,",
        "            outcome=Outcome.SERVED,",
        "gateway/tests/test_model_check.py",
    ),
    Mutation(
        "D22",
        "a diagnostic's row carries the usage the answer reported",
        "gateway/src/aira_gateway/api/incidents/thinking_check.py",
        '    usage = getattr(answer, "usage", None)',
        "    usage = None",
        "gateway/tests/test_model_check.py",
    ),
    Mutation(
        "V21",
        "the gateway publishes where this installation permits processing",
        "gateway/src/aira_gateway/api/providers.py",
        '            "allowedRegions": sorted(parse_allowed(settings.allowed_regions)),',
        '            "allowedRegions": [],',
        "gateway/tests/test_provider_offerings.py",
    ),
    Mutation(
        "V20",
        "the global Vertex endpoint has no region prefix",
        "gateway/src/aira_gateway/upstreams/vertex/transport.py",
        '    "global": "aiplatform.googleapis.com",',
        '    "global": "global-aiplatform.googleapis.com",',
        "gateway/tests/test_vertex.py",
    ),
    Mutation(
        "TC43",
        "every dialect declares which thinking modes it can express",
        "gateway/src/aira_gateway/upstreams/openai/adapter.py",
        "    thinking_modes = frozenset(ThinkingMode) - {ThinkingMode.LIMITED, ThinkingMode.AUTO}",  # noqa: E501
        "    thinking_modes = frozenset(ThinkingMode)",
        "gateway/tests/test_no_silent_drop.py",
    ),
    Mutation(
        "TC38",
        "a model with a floor above zero cannot also offer 'disabled'",
        "management/backend/src/aira_management/apps/catalog/validation.py",
        "    if ThinkingMode.DISABLED in modes and minimum:",
        "    if False:",
        "management/backend/tests/test_catalog_declaration.py",
    ),
    Mutation(
        "TC34",
        "an attachment is stripped by its shape, so a second surface's spelling cannot slip past",
        "gateway/src/aira_gateway/attachments.py",
        "    if _inline_datum(payload):",
        "    if False:",
        "gateway/tests/test_attachments.py",
    ),
    Mutation(
        "TC35",
        "both spellings of the media-type key are recognised",
        "gateway/src/aira_gateway/attachments.py",
        '_MIME_KEYS = ("mimeType", "mime_type")',
        '_MIME_KEYS = ("mimeType",)',
        "gateway/tests/test_attachments.py",
    ),
    Mutation(
        "TC32",
        "a counted repetition of a repeating group is refused, not compiled onto the request path",
        "libs/src/aira_common/patterns.py",
        "            if (int(av[0]), int(av[1])) != (1, 1):\n                return REPEATS_A_REPEAT",
        "            if False:\n                return REPEATS_A_REPEAT",
        "libs/tests/test_patterns.py",
    ),
    Mutation(
        "TC33",
        "the detector sees a group inside a group, which a regex cannot",
        "libs/src/aira_common/patterns.py",
        "            found = _inside_a_repeat(av[3], in_assertion=in_assertion)",
        "            found = None",
        "libs/tests/test_patterns.py",
    ),
    Mutation(
        "TC29",
        "a number JSON cannot represent is refused, so a caller cannot go unrecorded",
        "gateway/src/aira_gateway/api/serving/body.py",
        '        json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")',
        '        json.dumps(body, ensure_ascii=False).encode("utf-8")',
        "gateway/tests/test_edge_cases.py",
    ),
    Mutation(
        "TC30",
        "an unrepresentable value costs the value and not the audit row",
        "gateway/src/aira_gateway/persistence/sanitize.py",
        '        return f"<unrepresentable: {value}>"',
        "        return value",
        "gateway/tests/test_log_writer.py",
    ),
    Mutation(
        "TC31",
        "a model id larger than the column is a caller error, not a server error",
        "gateway/src/aira_gateway/api/kira/schemas.py",
        "ModelId = Annotated[int, Field(ge=1, le=2_147_483_647)]",
        "ModelId = Annotated[int, Field(ge=1)]",
        "gateway/tests/test_kira_surface.py gateway/tests/test_edge_cases.py",
    ),
    Mutation(
        "TC27",
        "a caller's text that cannot be encoded is refused, not turned into a server error",
        "gateway/src/aira_gateway/api/serving/body.py",
        '        json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")',
        "        pass",
        "gateway/tests/test_edge_cases.py",
    ),
    Mutation(
        "TC28",
        "every surface performs that check rather than the first one that thought of it",
        "gateway/src/aira_gateway/api/gemini/routes.py",
        "        ensure_body_is_encodable(body)",
        "        pass",
        "gateway/tests/test_surface_layering.py gateway/tests/test_edge_cases.py",
    ),
    Mutation(
        "TC26",
        "a budget's period boundary is UTC, and the console says which clock it follows",
        "gateway/src/aira_gateway/budgets/keys.py",
        # Widened when `period_ends` gained the same line: `period_key` is the one this guards,
        # and an anchor matching twice is one the harness refuses rather than guess.
        "    moment = now.astimezone(UTC)\n    return moment.strftime",
        "    moment = now\n    return moment.strftime",
        "gateway/tests/test_budget_service.py",
    ),
    Mutation(
        "TC24",
        "moving the console's port moves the login with it",
        "deploy/compose/keycloak/realms/aira-realm.json",
        '"http://localhost:${AIRA_CONSOLE_PORT:4200}/*",',
        '"http://localhost:4200/*",',
        "tools/tests/test_the_realm_follows_the_console_port.py",
    ),
    Mutation(
        "TC25",
        "Keycloak is handed the port Compose actually publishes the console on",
        "deploy/compose/docker-compose.yml",
        "      AIRA_CONSOLE_PORT: ${AIRA_CONSOLE_PORT:-${AIRA_PUBLISH_FRONTEND_PORT:-${AIRA_FRONTEND_PORT:-4200}}}",
        "      AIRA_CONSOLE_PORT: 4200",
        "tools/tests/test_the_realm_follows_the_console_port.py",
    ),
    Mutation(
        "TC21",
        "both Kafka clients authenticate, or neither half of the bus is protected",
        "libs/src/aira_common/kafka.py",
        '        if protocol in {"SASL_PLAINTEXT", "SASL_SSL"}:',
        "        if False:",
        "libs/tests/test_kafka.py",
    ),
    Mutation(
        "TC22",
        "an SSL protocol really builds a TLS context",
        "libs/src/aira_common/kafka.py",
        '        if protocol in {"SSL", "SASL_SSL"}:',
        "        if False:",
        "libs/tests/test_kafka.py",
    ),
    Mutation(
        "TC23",
        "a plaintext broker is recognised as one however it is spelled",
        "libs/src/aira_common/kafka.py",
        '        return self.protocol.strip().upper() in {"", "PLAINTEXT"}',
        '        return self.protocol in {"", "PLAINTEXT"}',
        "libs/tests/test_kafka.py gateway/tests/test_hardening.py",
    ),
    Mutation(
        "TC18",
        "the bound on failed authentications actually refuses",
        "management/backend/src/aira_management/apps/api/attempts.py",
        "        return len(self._recent(request, now or time.time())) >= self._limit",
        "        return False",
        "management/backend/tests/test_the_api_is_rate_limited.py",
    ),
    Mutation(
        "TC19",
        "a working credential never fills the failure bucket",
        "management/backend/src/aira_management/apps/api/attempts.py",
        "        history.insert(0, moment)",
        "        history.insert(0, moment)\n        history.insert(0, moment)",
        "management/backend/tests/test_the_api_is_rate_limited.py",
    ),
    Mutation(
        "TC20",
        "an unreadable rate switches the bound off rather than guessing at one",
        "management/backend/src/aira_management/apps/api/attempts.py",
        "        return self._limit > 0",
        "        return True",
        "management/backend/tests/test_the_api_is_rate_limited.py",
    ),
    Mutation(
        "TC17",
        "the migration graph has exactly one head, found before a deployment finds it",
        "gateway/migrations/versions/0039_drop_superseded_use_case_created_at_index.py",
        'down_revision = "0038_include_reasoning"',
        'down_revision = "0037_reasoning_tokens"',
        "gateway/tests/test_the_migration_graph_is_a_line.py",
    ),
    Mutation(
        "TC16",
        "residency is enforced in local too, not only in a deployment",
        "gateway/src/aira_gateway/upstreams/gemini.py",
        "    check_region(GENERATIVE_LANGUAGE_REGION, allowed)",
        "    if settings.environment != 'local':\n        check_region(GENERATIVE_LANGUAGE_REGION, allowed)",
        "gateway/tests/test_gemini_upstream.py",
    ),
    Mutation(
        "TC14",
        "an event one plane publishes is one the other applies",
        "gateway/src/aira_gateway/consumer/apply.py",
        '    "ratelimit.upserted": _upsert_rate_limit,',
        '    "ratelimit.upserted_": _upsert_rate_limit,',
        "tools/tests/test_the_two_planes_agree_on_the_event_vocabulary.py",
    ),
    Mutation(
        "TC15",
        "a config event the gateway cannot apply is said out loud, not dropped",
        "gateway/src/aira_gateway/consumer/apply.py",
        '                "config_event_not_applied",',
        '                "config_event_seen",',
        "tools/tests/test_the_two_planes_agree_on_the_event_vocabulary.py",
    ),
    Mutation(
        "TC13",
        "the schema bounds a test measures are the bounds production applies",
        "gateway/src/aira_gateway/core/schema.py",
        "    max_depth: int = 8",
        "    max_depth: int = 12",
        "gateway/tests/test_response_schema.py",
    ),
    Mutation(
        "TC10",
        "the address owner follows the whole chain Compose consults, not only its outer name",
        "tools/stack_addresses.py",
        "            value = source.get(name)",
        "            value = source.get(names[0]) if name == names[0] else None",
        "tools/tests/test_one_owner_for_the_stack_addresses.py",
    ),
    Mutation(
        "TC11",
        "the configuration reference names every setting the services read",
        "docs/CONFIGURATION.md",
        "| `AIRA_KAFKA_SASL_MECHANISM`    | —           |",
        "| `AIRA_KAFKA_SASL_MECHANISMS`   | —           |",
        "tools/tests/test_configuration_reference_names_every_setting.py",
    ),
    Mutation(
        "TC12",
        "a documented make target is one that exists",
        "docs/features/FRD-123-local-models-for-verification.md",
        "`make verify-up` to bring up a real",
        "`make verify-local` to bring up a real",
        "tools/tests/test_documented_counts.py",
    ),
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
        # Two lines, because `FRD-617` gave `issuer=self._issuer,` a second home at the same
        # indentation — the `oidc_jwks_unavailable` line — and the harness edits the first match.
        "                algorithms=self._algorithms,\n                issuer=self._issuer,",
        "                algorithms=self._algorithms,\n                # issuer dropped",
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
        '        principal.method == "api_key" and principal.use_cases and use_case != principal.use_cases[0]\n',
        '        principal.method == "api_key" and principal.use_cases and False\n',
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
        '_SLUG = re.compile(r"^[a-z0-9-]{1,64}\\Z")',
        '_SLUG = re.compile(r"^.*\\Z")',
        f"{HARDENING} {ATTRIBUTION}",
    ),
    Mutation(
        "A9",
        "a credential in the query string never reaches a span",
        "libs/src/aira_common/observability/redaction.py",
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
        "    if Permission.USECASE_READ_ALL in granted or Permission.USECASE_MANAGE_ALL in granted:",
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
        "    return not usecase.is_deleted and _holds(user, CHANGE, usecase)",
        "    return not usecase.is_deleted and _holds(user, VIEW, usecase)",
        MGMT_RBAC,
    ),
    Mutation(
        "G4",
        "removing a membership revokes the permissions it granted",
        "management/backend/src/aira_management/apps/usecases/views/members.py",
        "            _revoke(user, usecase)\n            # **The name as it is stored",
        "            # **The name as it is stored",
        MGMT_RBAC,
    ),
    Mutation(
        "G5",
        "granting access grants the permission it promises, to a person or a group",
        "management/backend/src/aira_management/apps/usecases/views/grants.py",
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
        "management/backend/src/aira_management/apps/usecases/views/api_keys.py",
        "        if not self._is_member(usecase):",
        "        if False:",
        MGMT_HARDENING,
    ),
    Mutation(
        "G7",
        # **Re-anchored 2026-08-30** (`FRD-613`). It guarded the narrower half of a rule that has
        # since been replaced outright: `_provision_user` used to adopt any *unbound* account whose
        # username matched, and this mutation removed the "unbound" half. The whole "by name" half
        # is gone now — an account is claimable only where somebody **invited** it — so the
        # property to defend is that the invitation is required, not that a binding is checked.
        # **And re-written 2026-08-31, because the re-anchoring above produced an edit that could
        # not fail.** `.filter(user__username__in=[preferred, preferred])` is the *same query* as
        # `.filter(user__username=preferred)`, so this reported `SURVIVED` about a property
        # `test_an_uninvited_account_is_claimed_by_nobody` has defended since the day it was
        # written. `LESSONS.md` §7 names that as the more expensive direction of *a guard that
        # cannot fail*: it sends the next reader to write a test that already exists, and it says
        # the account-takeover fix is undefended when it is not. The edit below reintroduces the
        # defect itself — an invitation manufactured for **any** account carrying the name — which
        # is what the sentence beside it claims to be guarding.
        "an account nobody invited is claimed by nobody, however its name matches",
        "management/backend/src/aira_management/apps/api/authentication.py",
        """                invited = (
                    PendingIdentity.objects.select_related("user")
                    .filter(user__username=preferred)
                    .first()
                )""",
        """                _any = user_model.objects.filter(username=preferred).first()
                invited = PendingIdentity.objects.create(user=_any) if _any else None""",
        f"{MGMT_HARDENING} management/backend/tests/test_an_identity_is_claimed_once.py",
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
        "    if record is None:\n        session.add(entity(**key, **fields))",
        "    if True:\n        session.add(entity(**key, **fields))",
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
        "gateway/src/aira_gateway/budgets/store.py",
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
        "gateway/src/aira_gateway/budgets/keys.py",
        'return moment.strftime("%Y-%m-%d") if period == "day" else moment.strftime("%Y-%m")',
        'return moment.strftime("%Y-%m")',
        "gateway/tests/test_budget_service.py",
    ),
    # B6 ("a member-scoped budget binds only that member") is superseded by S1: the rule now
    # lives in one place for budgets and rate limits alike, so it has one mutation rather than
    # two that could drift apart.
    Mutation(
        "B7",
        "a disabled budget does not bind",
        "gateway/src/aira_gateway/budgets/service.py",
        # Re-anchored with `FRD-610`: the query now reads `use_case or ""`, so that a request
        # naming none finds the installation's rows. Re-anchored again when a lifted budget came
        # to be **found and counted** (`store.applicable`): what keeps it from binding is now the
        # guard's split, so the property moved there with its clause.
        "            budgets = [budget for budget in binding if budget.enabled]",
        "            budgets = binding",
        "gateway/tests/test_budget_service.py gateway/tests/test_a_lifted_budget_still_counts.py",
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
        "management/backend/src/aira_management/apps/usecases/views/payloads.py",
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
        "        return [IsAuthenticated(), requires(Permission.CATALOG_WRITE)()]",
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
        "gateway/src/aira_gateway/pipeline/engine/steps.py",
        # Re-anchored by `FRD-125`: the decision moved into `_blocks` when the verdict became
        # three-valued, and a mutation whose anchor has moved protects nothing.
        "    if verdict is Verdict.INJECTION:\n        return True",
        "    if verdict is Verdict.INJECTION:\n        return False",
        f"{PIPELINE} {CLASSIFIERS}",
    ),
    Mutation(
        "P2",
        "an injection filter set to flag does not refuse the request",
        "gateway/src/aira_gateway/pipeline/engine/steps.py",
        '    if config.get("action") == FilterAction.FLAG:\n        return False',
        "    if False:\n        return False",
        "gateway/tests/test_pipeline_engine.py gateway/tests/test_pipeline_routes.py gateway/tests/test_pipeline_classifiers.py",
    ),
    Mutation(
        "P4",
        "the fallback chain tries the requested model first",
        "gateway/src/aira_gateway/pipeline/dispatch.py",
        "    names = [request.model, *[model for model in fallback_models if model != request.model]]",
        "    names = [*[model for model in fallback_models if model != request.model], request.model]",
        "gateway/tests/test_pipeline_dispatch.py"
        " gateway/tests/test_a_stream_falls_back_until_a_chunk_is_out.py",
    ),
    Mutation(
        "P5",
        "a regex with a nested quantifier is refused at authoring time (ReDoS)",
        "management/backend/src/aira_management/apps/pipelines/serializers.py",
        "    reason = catastrophic_reason(pattern)\n    if reason is not None:",
        "    reason = catastrophic_reason(pattern)\n    if reason is None:",
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
        "libs/src/aira_common/config.py",
        "    log_retention_days: int = 0",
        "    log_retention_days: int = 30",
        f"{RETENTION} gateway/tests/test_config.py",
    ),
    Mutation(
        "R5",
        "an absent payload is SQL NULL, not the JSON value null",
        "gateway/src/aira_gateway/db/models/owned.py",
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
        "            left = tokens - cost if allowed else tokens",
        "            left = tokens - cost",
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
        "            if use_case and self._enforce\n",
        "            if False\n",
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
        '    await _upsert(session, RateLimitRead, fields, id=payload["id"])',
        '        "enabled": False,\n'
        "    }\n"
        '    await _upsert(session, RateLimitRead, fields, id=payload["id"])',
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
        "            await self._check_only(session, budgets, now, subject)\n        return Reservation(\n",
        "        return Reservation(\n",
        "gateway/tests/test_budget_reservation.py gateway/tests/test_budget_service.py",
    ),
    Mutation(
        "M23",
        "every verb passes the pre-dispatch controls, not only the generate ones",
        "gateway/src/aira_gateway/api/serving/prepare.py",
        # Re-anchored by `FRD-126`: this step moved out of the surfaces and into the one
        # sequence that owns their order. A mutation whose anchor has moved protects nothing.
        "    reservation = await enforce_pre_dispatch(",
        "    reservation = Reservation() if embed is not None else await enforce_pre_dispatch(",
        RATELIMIT_ROUTES,
    ),
    Mutation(
        "M24",
        "the reservation uses the caller's own output bound where it gave one",
        "gateway/src/aira_gateway/api/serving/controls.py",
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
        "            except Exception as exc:  # a failed write must never take the worker down with it",
        "            except ValueError as exc:",
        LOG_WRITER,
    ),
    Mutation(
        "M20",
        "a use case that declined storage gets none",
        "gateway/src/aira_gateway/persistence/writer.py",
        "        return False if record is None else bool(record.store_payloads)",
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
        # **"Purging"**, not "deleting". The anchor is in `_purge_usecase` and the description said
        # `deleting`, which is what `usecase.deleted` does — so a reader matching this line to a
        # test found `test_deleting_a_use_case_keeps_its_request_log`, which exercises retirement
        # and never reaches this code. The mutation survived for as long as the two words were
        # treated as one.
        "purging a use case keeps its request log",
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
        "            return cls(use_case, caller)",
        "            return cls(use_case)",
        "gateway/tests/test_scopes.py gateway/tests/test_budget_service.py "
        "gateway/tests/test_ratelimit.py",
    ),
    Mutation(
        "S9",
        "a per-person budget is accounted under the caller, not under the row",
        "gateway/src/aira_gateway/budgets/keys.py",
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
        #
        # **Re-anchored 2026-08-30.** The narrowing moved into a comprehension in the same
        # statement, because narrowing the *list* was not enough: a list containing a non-string
        # reached `usecases_from_group_paths`, which called `.startswith` on it —
        # `AttributeError` inside token validation, which is a 500 on every request that caller
        # makes. Two of the three readers filtered and the third did not.
        "gateway/src/aira_gateway/auth/oidc.py",
        "        groups = (\n            [path for path in raw_groups if isinstance(path, str)]",
        "        groups = (\n            list(raw_groups)",
        "gateway/tests/test_auth_oidc.py gateway/tests/"
        "test_the_identity_boundary_takes_any_value.py",
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
        "gateway/src/aira_gateway/api/kira/headers.py",
        '    "Deprecation": "true",',
        '    "X-Not-Deprecation": "true",',
        KIRA,
    ),
    Mutation(
        "K6",
        "a request on this surface passes the same pre-dispatch controls as any other",
        "gateway/src/aira_gateway/api/serving/prepare.py",
        # Re-anchored by `FRD-126`: this step moved out of the surfaces and into the one
        # sequence that owns their order. A mutation whose anchor has moved protects nothing.
        # `FRD-126` is what makes this one property rather than one per surface — which is the
        # change's whole claim, so the mutation now tests the claim instead of one copy of it.
        # Re-anchored again (2026-08-14) when `Prepared` gained the notices a step owes the
        # caller (`FRD-309`). The property is unchanged: one sequence, both surfaces.
        "    return Prepared(canonical, embed, fallbacks, declaration, reservation, notices, asked)",
        "    return Prepared(canonical, embed, fallbacks, declaration, Reservation(), notices, asked)",
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
        "gateway/src/aira_gateway/api/serving/accounting.py",
        "    if attribution_of(request) is None:",
        "    if True:",
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
        "gateway/src/aira_gateway/api/serving/prepare.py",
        "    if canonical is not None and canonical.media_types:",
        "    if False:",
        ATTACHMENTS,
    ),
    Mutation(
        "F4",
        "invalid base64 is refused rather than silently truncated into a shorter document",
        "gateway/src/aira_gateway/attachments.py",
        '    return base64.b64decode(text + "=" * (-len(text) % 4), validate=True)',
        '    return base64.b64decode(text + "=" * (-len(text) % 4))',
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
        "    if not all(data.startswith(mark, offset) for offset, mark in marks):",
        "    if False:",
        ATTACHMENTS,
    ),
    Mutation(
        "F7",
        "attachment bytes never reach the audit table",
        "gateway/src/aira_gateway/persistence/writer.py",
        "                stripped: dict[str, Any] = strip_attachments(bounded)",
        "                stripped: dict[str, Any] = bounded",
        ATTACHMENTS,
    ),
    Mutation(
        "F8",
        "the reservation counts the attachment rather than treating it as free",
        "gateway/src/aira_gateway/api/serving/controls.py",
        "    tokens += max(0, declaration.attachment_tokens(attachments or []))",
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
        "            check_region(described.region, self._allowed, model=model, for_models=self._for_models)",  # noqa: E501
        "            pass",
        REQUIREMENTS,
    ),
    Mutation(
        "D3",
        "an exhausted chain is a precondition failure, not an upstream outage",
        "gateway/src/aira_gateway/pipeline/dispatch.py",
        "    return NoCapableModel(skipped)",
        '    return UpstreamError("No provider available.")',
        REQUIREMENTS,
    ),
    Mutation(
        "D4",
        "an upstream that was tried and failed is still reported as an outage",
        "gateway/src/aira_gateway/pipeline/dispatch.py",
        "    # passed over, for the refusal's row (`PassedOver`).\n    if last_error is not None:\n        raise _exhausted(last_error, skipped, failed)",
        "    # passed over, for the refusal's row (`PassedOver`).\n    if False:\n        raise _exhausted(last_error, skipped, failed)",
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
        "        trail.passed_over(dispatched.skipped, dispatched.failed)",
        "        pass",
        REQUIREMENTS,
    ),
    # ---- Vertex EU and the second dialect (FRD-115, FRD-119) -------------------------------
    Mutation(
        "V1",
        "residency is enforced: a model outside the allowed regions refuses to start",
        "gateway/src/aira_gateway/residency.py",
        "    if region in allowed:\n        return",
        "    if True:\n        return",
        VERTEX,
    ),
    Mutation(
        "V2",
        "the EU multi-region has its own host, not a region-prefixed one",
        "gateway/src/aira_gateway/upstreams/vertex/transport.py",
        '    "eu": "aiplatform.eu.rep.googleapis.com",',
        '    "eu": "eu-aiplatform.googleapis.com",',
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
        "gateway/src/aira_gateway/upstreams/vertex/anthropic_mapping/response.py",
        '_ANSWER_BLOCKS = frozenset({"text"})',
        '_ANSWER_BLOCKS = frozenset({"text", "thinking"})',
        VERTEX,
    ),
    Mutation(
        "V5",
        "several system messages are concatenated rather than reduced to the last",
        "gateway/src/aira_gateway/upstreams/vertex/anthropic_mapping/request.py",
        '        joined = "\\n\\n".join(system_parts)',
        "        joined = system_parts[-1]",
        VERTEX,
    ),
    Mutation(
        "V6",
        "streamed usage is accumulated across events, not replaced by the last one",
        "gateway/src/aira_gateway/upstreams/vertex/anthropic_mapping/response.py",
        '        """Take each count the event carries: it is the total so far, never an increment."""\n        if not isinstance(reported, dict):',
        '        """Take each count the event carries: it is the total so far, never an increment."""\n        self._usage = {}\n        if not isinstance(reported, dict):',
        VERTEX,
    ),
    Mutation(
        "V7",
        "cache tokens are counted as input rather than dropped from the bill",
        "gateway/src/aira_gateway/upstreams/vertex/anthropic_mapping/response.py",
        '        prompt_tokens=int(usage.get("input_tokens", 0) or 0) + cached + created,',
        '        prompt_tokens=int(usage.get("input_tokens", 0) or 0),',
        VERTEX,
    ),
    Mutation(
        "V8",
        "an upstream status is preserved so the route can pass 429/503/504 through",
        "gateway/src/aira_gateway/upstreams/exchange.py",
        '    return UpstreamError(f"{name} returned {response.status_code}.{detail}", response.status_code)',
        '    return UpstreamError(f"{name} returned {response.status_code}.{detail}")',
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
        "gateway/src/aira_gateway/api/serving/controls.py",
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
        "gateway/src/aira_gateway/api/serving/answers.py",
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
        "C12",
        "a model field the console stops sending is reported, not shipped",
        "management/frontend/src/app/features/models/model-editor.ts",
        "        numeric_id: this.kiraId(),",
        "",
        "management/backend/tests/test_every_model_control_is_reachable.py",
    ),
    Mutation(
        "C13",
        "a use-case field the console stops sending is reported, not shipped",
        "management/frontend/src/app/features/use-cases/about-panel.ts",
        "        description: this.description().trim(),",
        "",
        "management/backend/tests/test_every_use_case_control_is_reachable.py",
    ),
    Mutation(
        "C14",
        "a pipeline setting the engine obeys and the builder cannot set is reported",
        "management/frontend/src/app/features/pipelines/pipeline-editor.html",
        "setStepField(selectedIndex(), 'use_builtins', $event)",
        "setStepField(selectedIndex(), 'unused', $event)",
        "management/backend/tests/test_every_use_case_control_is_reachable.py",
    ),
    Mutation(
        "C15",
        "an upsert that says nothing about `enabled` leaves a lifted budget lifted",
        "management/backend/src/aira_management/apps/usecases/views/budgets.py",
        '        if "enabled" in data:\n            defaults["enabled"] = data["enabled"]',
        '        defaults["enabled"] = data.get("enabled", True)',
        "management/backend/tests/test_budgets.py",
    ),
    Mutation(
        "C16",
        "a budget field the console stops sending is reported, not shipped",
        "management/frontend/src/app/features/use-cases/budgets-tab.ts",
        "      limit_requests: this.budgetRequests(),",
        "",
        "management/backend/tests/test_every_use_case_control_is_reachable.py",
    ),
    Mutation(
        "C17",
        "an anomaly-rule field the form stops sending is reported, not shipped",
        "management/frontend/src/app/features/security/rule-form.ts",
        "      min_sample: this.sample() ?? 0,",
        "",
        "management/backend/tests/test_every_use_case_control_is_reachable.py",
    ),
    Mutation(
        "C18",
        "the API-key listing serializer stays read-only in every field",
        "management/backend/src/aira_management/apps/apikeys/serializers.py",
        "        read_only_fields = fields",
        '        read_only_fields = ["prefix"]',
        "management/backend/tests/test_apikeys.py",
    ),
    Mutation(
        "C19",
        "a kill-switch field no person can set is reported, not shipped",
        "management/frontend/src/app/features/security/suspensions-panel.ts",
        "        use_case: this.scope() || null,",
        "",
        "management/backend/tests/test_every_use_case_control_is_reachable.py",
    ),
    Mutation(
        "C20",
        "an answered question is refused deletion by name, never with a 500",
        "management/backend/src/aira_management/apps/smoketests/views/catalogue.py",
        "        if instance.results.exists():",
        "        if False:",
        "management/backend/tests/test_smoketests.py",
    ),
    Mutation(
        "C21",
        "the expiry tolerance is its own decision, not the clock's",
        "libs/src/aira_common/oidc.py",
        '        overdue = now - float(claims["exp"])\n        if overdue > self._expiry_leeway:',
        '        overdue = now - float(claims["exp"])\n        if False:',
        "libs/tests/test_oidc_clock_tolerance.py",
    ),
    Mutation(
        "C22",
        "a clock behind the issuer's does not refuse every fresh token",
        "libs/src/aira_common/oidc.py",
        "                leeway=max(self._clock_skew, self._expiry_leeway),",
        "                leeway=0,",
        "libs/tests/test_oidc_clock_tolerance.py",
    ),
    Mutation(
        "C23",
        "a token is verified by the realm it names, not by whichever realm will take it",
        "gateway/src/aira_gateway/auth/oidc.py",
        "            if claims is not None:\n                return self._principal(claims, issuer)",
        "            if claims is not None:\n                return self._principal(claims, self._verifiers[0][0])",
        "gateway/tests/test_several_issuers.py",
    ),
    Mutation(
        "C24",
        "every configured issuer needs an audience, not just the first setting",
        "gateway/src/aira_gateway/security.py",
        "    unnamed = [name for name, audience, _ in settings.issuers() if not audience.strip()]",
        "    unnamed = [] if settings.oidc_audience.strip() else [settings.oidc_issuer]",
        "gateway/tests/test_deployment_safety.py",
    ),
    Mutation(
        "C25",
        "an API-key credential does not reach for a token that is not there",
        "gateway/src/aira_gateway/upstreams/vertex/transport.py",
        "        if self._api_key or self._tokens is None:",
        "        if False:",
        "gateway/tests/test_vertex_api_key.py",
    ),
    Mutation(
        "C26",
        "a service account is preferred where both credentials are configured",
        "gateway/src/aira_gateway/upstreams/vertex/__init__.py",
        '        api_key="" if settings.vertex_credentials else settings.vertex_api_key,',
        "        api_key=settings.vertex_api_key,",
        "gateway/tests/test_vertex_api_key.py",
    ),
    Mutation(
        "C27",
        "thinking tokens are counted — the 85% that was invisible",
        "gateway/src/aira_gateway/upstreams/gemini_mapping.py",
        '    thoughts = int(meta.get("thoughtsTokenCount", 0) or 0)',
        "    thoughts = 0",
        "gateway/tests/test_reasoning.py",
    ),
    # `FRD-135` FR-3 on the two dialects that were not honouring it. The switch reached the
    # canonical request and only the Gemini mapper read it, so a use case with reasoning on and a
    # Claude or OpenAI-dialect model was answered 200 with no thoughts — while the *counting*
    # worked on all three, so reporting showed thinking being paid for that no answer carried.
    #
    # Both directions per dialect, and the second is the one that matters more: these providers
    # send reasoning whether or not it was asked for, so an unconditional read discloses it to
    # every use case that never asked (`FRD-135` §8 puts it behind the stored-prompt gate).
    Mutation(
        "C28a",
        "the OpenAI dialect returns the reasoning a use case asked for",
        "gateway/src/aira_gateway/upstreams/openai/mapping/response.py",
        '    return str(thoughts) if wanted else ""',
        '    return ""',
        "gateway/tests/test_reasoning_reaches_every_dialect.py",
    ),
    Mutation(
        "C28b",
        "and returns it to nobody who did not",
        "gateway/src/aira_gateway/upstreams/openai/mapping/response.py",
        '    return str(thoughts) if wanted else ""',
        "    return str(thoughts)",
        "gateway/tests/test_reasoning_reaches_every_dialect.py",
    ),
    Mutation(
        "C28c",
        "Anthropic's thinking blocks reach a use case that asked for them",
        "gateway/src/aira_gateway/upstreams/vertex/anthropic_mapping/response.py",
        "    if not wanted or not isinstance(content, list):",
        "    if True:",
        "gateway/tests/test_reasoning_reaches_every_dialect.py",
    ),
    Mutation(
        "C28d",
        "and are dropped for every use case that did not",
        "gateway/src/aira_gateway/upstreams/vertex/anthropic_mapping/response.py",
        "    if not wanted or not isinstance(content, list):",
        "    if not isinstance(content, list):",
        "gateway/tests/test_reasoning_reaches_every_dialect.py",
    ),
    Mutation(
        "C28e",
        "the adapter carries the use case's switch rather than defaulting it away",
        "gateway/src/aira_gateway/upstreams/openai/adapter.py",
        "        return openai_to_canonical(data, request.model, include_reasoning=request.include_reasoning)",  # noqa: E501
        "        return openai_to_canonical(data, request.model)",
        "gateway/tests/test_reasoning.py gateway/tests/test_reasoning_reaches_every_dialect.py",
    ),
    Mutation(
        "C27a",
        "the subject a row is keyed on is bounded to its column, like the model name beside it",
        "gateway/src/aira_gateway/persistence/service.py",
        "            subject=str(_fits(subject, SUBJECT_COLUMN)),",
        "            subject=subject,",
        "gateway/tests/test_persistence_service.py",
    ),
    Mutation(
        "C27b",
        "and so is the name recorded beside it",
        "gateway/src/aira_gateway/persistence/service.py",
        "            username=_fits(username, SUBJECT_COLUMN),",
        "            username=username,",
        "gateway/tests/test_persistence_service.py",
    ),
    Mutation(
        "C28f",
        "the Vertex Anthropic adapter carries it too — two adapters, two chances to drop it",
        "gateway/src/aira_gateway/upstreams/vertex/adapters.py",
        "                include_reasoning=request.include_reasoning,",
        "                include_reasoning=False,",
        "gateway/tests/test_reasoning_reaches_every_dialect.py",
    ),
    Mutation(
        "C28",
        "a use case that has not enabled reasoning refuses the request for it",
        "gateway/src/aira_gateway/api/serving/controls.py",
        "    if asked_for and not allowed:",
        "    if False:",
        "gateway/tests/test_google_sdk_speaks_to_us.py",
    ),
    Mutation(
        "C29",
        "a ratio is not applied to a text too short to have one",
        "gateway/src/aira_gateway/pipeline/classifiers/redaction.py",
        "        if len(given) >= self.MIN_LENGTH_FOR_RATIO and len(rewritten) < len(given) * self.MIN_KEPT:",  # noqa: E501
        "        if len(rewritten) < len(given) * self.MIN_KEPT:",
        "gateway/tests/test_the_redactor_length_guard.py",
    ),
    Mutation(
        "C30",
        "the Agent Platform adapter can be asked whether it is reachable, without generating",
        "gateway/src/aira_gateway/upstreams/vertex/adapters.py",
        '            self._url(name, "countTokens"),',
        '            self._url(name, "generateContent"),',
        "gateway/tests/test_vertex_api_key.py",
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
        "gateway/src/aira_gateway/api/serving/accounting.py",
        "        await _write_refusal(request, trail, exc, status=status, started=started)",
        "        pass",
        "gateway/tests/test_audit_completeness.py",
    ),
    Mutation(
        "T2",
        "the audit never turns a correct refusal into a server error",
        "gateway/src/aira_gateway/api/serving/accounting.py",
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
        "gateway/src/aira_gateway/pipeline/engine/runner.py",
        # Widened when `run_over_texts` arrived beside `run` and the two now build their outcomes
        # the same way: the anchor names which of them this property is about.
        "            fallback_models=pipeline.fallback_models,\n"
        "            decisions=decisions if decisions is not None else [],",
        "            fallback_models=pipeline.fallback_models,\n            decisions=[],",
        "gateway/tests/test_audit_completeness.py",
    ),
    # ---- reporting (FRD-601) --------------------------------------------------------------
    Mutation(
        "N1",
        "a caller without oversight is scoped to their own use cases, never to everything",
        "gateway/src/aira_gateway/api/reporting/common.py",
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
        "gateway/src/aira_gateway/api/reporting/common.py",
        "    if principal.allows(permission):\n        return None",
        "    if principal.allows(permission):\n        return principal.use_cases",
        "gateway/tests/test_reporting.py gateway/tests/test_traces.py",
    ),
    Mutation(
        "N3",
        "unpriced traffic is counted apart, never summed into spend as zero",
        "gateway/src/aira_gateway/reporting/service.py",
        "                    (RequestLog.cost_nanos.is_(None)) & _was_served(),",
        "                    (False) & _was_served(),",
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
        "        func.sum(case((RequestLog.outcome.in_(sorted(FAILURES)), 1), else_=0)).label(",
        "        func.sum(case((RequestLog.outcome.in_([]), 1), else_=0)).label(",
        "gateway/tests/test_reporting.py",
    ),
    Mutation(
        "N6",
        "a reporting window is bounded, so a mistyped year is an error not a full scan",
        "gateway/src/aira_gateway/api/reporting/common.py",
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
        "the reservation reads the model's ceiling, and a level sends no number at all",
        "gateway/src/aira_gateway/thinking.py",
        "    _, maximum = declaration.thinking_bounds\n    return maximum or 0",
        "    return 0",
        THINKING,
    ),
    Mutation(
        "T10",
        "a fallback candidate that cannot honour the thinking is skipped, not served",
        "gateway/src/aira_gateway/thinking.py",
        "    if not declaration.can(Capability.THINKING) or setting.mode not in offered:",
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
        "gateway/src/aira_gateway/api/serving/answers.py",
        '    if canonical.response_schema is None or response.finish_reason == "stop":',
        "    if True:",
        SERVING_OPTIONS,
    ),
    Mutation(
        "S7",
        "an Anthropic model that answered in prose has not satisfied the schema",
        "gateway/src/aira_gateway/upstreams/vertex/anthropic_mapping/response.py",
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
        "gateway/src/aira_gateway/api/serving/controls.py",
        # Re-anchored twice: once when the call was reformatted and the raw `app.state` read
        # became the typed accessor, and again when the second argument became the **person**
        # rather than the subject (`ADR-0019`). `units` is still the whole property, and it is
        # still the argument this replaces.
        "        await rate_limits.check(\n            use_case,\n            caller,\n            units,",
        "        await rate_limits.check(\n            use_case,\n            caller,\n            1,",
        EMBEDDING,
    ),
    Mutation(
        "E2",
        "the bucket debits what the request weighs",
        "gateway/src/aira_gateway/ratelimit/buckets.py",
        "            if tokens < cost:",
        "            if tokens < 1:",
        EMBEDDING,
    ),
    Mutation(
        "E3",
        "a batch is booked against the budget as the many requests it is",
        "gateway/src/aira_gateway/api/serving/controls.py",
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
        "gateway/src/aira_gateway/api/serving/controls.py",
        "    if method not in EMBEDDING_METHODS and not speech and not declaration.can(Capability.GENERATE):",
        '    if method != "embedContent" and not speech and not declaration.can(Capability.GENERATE):',
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
        "gateway/src/aira_gateway/budgets/store.py",
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
        "gateway/src/aira_gateway/upstreams/openai/mapping/request.py",
        '        body["stream_options"] = {"include_usage": True}',
        "        pass",
        OPENAI_DIALECT,
    ),
    Mutation(
        "O3a",
        "usage in a chunk with no choices is read rather than dropped",
        "gateway/src/aira_gateway/upstreams/openai/mapping/response.py",
        '        return CanonicalChunk(text_delta="", finish_reason=None, usage=usage) if usage else None',  # noqa: E501
        "        return None",
        OPENAI_DIALECT,
    ),
    Mutation(
        "O4",
        "a token budget is refused rather than rounded to an effort level",
        "gateway/src/aira_gateway/upstreams/openai/mapping/request.py",
        "    if setting.mode == ThinkingMode.LIMITED:",
        "    if False:",
        OPENAI_DIALECT,
    ),
    Mutation(
        "O5",
        "a document is refused rather than sent to a dialect that carries only images",
        "gateway/src/aira_gateway/upstreams/openai/mapping/request.py",
        '        if not part.media_type.startswith("image/"):',
        "        if False:",
        OPENAI_DIALECT,
    ),
    Mutation(
        "O6",
        "vectors are returned in the order submitted, not the order received",
        "gateway/src/aira_gateway/upstreams/openai/mapping/response.py",
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
        "gateway/src/aira_gateway/upstreams/foundry/transport.py",
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
        "        check_region(entry.region, allowed)",
        "        pass",
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
        "libs/src/aira_common/config.py",
        "            VaultSource(settings_cls),\n            env_settings,",
        "            env_settings,\n            VaultSource(settings_cls),",
        SECRETS,
    ),
    Mutation(
        "V3a",
        "a key present but spelled differently is still found",
        "libs/src/aira_common/secrets.py",
        "    name = key.strip().upper()",
        "    name = key",
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
        "    degraded = not counters_ok or not kafka_ok or bool(fallbacks)"
        " or bool(probe and probe.degraded)",
        "    degraded = not counters_ok or not kafka_ok or bool(fallbacks)",
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
        "gateway/src/aira_gateway/cors.py",
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
        "gateway/src/aira_gateway/api/reporting/spend.py",
        "    report = await service.report(\n        scope,",
        "    report = await service.report(\n        None,",
        f"{CSV_EXPORT} gateway/tests/test_reporting.py",
    ),
    Mutation(
        "E10",
        "a format this endpoint does not serve is refused rather than answered in another",
        "gateway/src/aira_gateway/api/reporting/common.py",
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
        "gateway/src/aira_gateway/api/serving/controls.py",
        "    if canonical.is_empty:",
        "    if False:",
        f"{EDGE} {KIRA}",
    ),
    Mutation(
        "X2",
        "a non-positive output cap is refused, not silently applied as a slice",
        "gateway/src/aira_gateway/api/serving/controls.py",
        "    if requested is not None and requested <= 0:",
        "    if False:",
        f"{EDGE} {MODEL_CATALOG}",
    ),
    Mutation(
        "X4",
        "a shared control's refusal is rendered in the surface's own vocabulary, not as a 500",
        "gateway/src/aira_gateway/api/kira/refusals.py",
        "    if isinstance(exc, GeminiHTTPError):",
        "    if False:",
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
        "Y3b",
        "a model reachable only through the catalogue still takes its dialect's check",
        "gateway/src/aira_gateway/requirements.py",
        "    return registry.provider_for(model, declaration.provider, declaration.publisher)",
        "    return registry.provider_for(model)",
        NO_SILENT_DROP,
    ),
    Mutation(
        "Y3c",
        "a control a dialect has no word for is a named refusal, never a 500",
        "gateway/src/aira_gateway/upstreams/vertex/anthropic_mapping/request.py",
        "            raise DialectUnsupported(\n"
        "                f\"The Anthropic Messages API has {why}; '{name}' cannot be honoured.\"\n"
        "            )",
        "            raise ValueError(\n"
        "                f\"The Anthropic Messages API has {why}; '{name}' cannot be honoured.\"\n"
        "            )",
        NO_SILENT_DROP,
    ),
    Mutation(
        "Y4",
        "a candidate whose dialect cannot express a control is skipped, not served without it",
        "gateway/src/aira_gateway/api/serving/prepare.py",
        # The requirement carries the catalogue, so a model reachable only by being catalogued
        # takes its dialect's check too (see `Y3b`).
        "        checks.append(SamplingExpressible(registry, canonical.sampling_requested, catalog))",
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
    # **`!=`, not `is not`, and the difference is the whole entry.** This was written as
    # `mode is not ThinkingMode.DISABLED` while `Thinking.mode` was a `ThinkingMode`; `ADR-0021`
    # made it a plain `str` — a level is the vendor's own word, so a closed enum would make a new
    # word a code change — and from that day the comparison was **always true** and the mutation
    # changed nothing. It then reported `SURVIVED` about a property that is in fact defended:
    # `test_disabled_thinking_says_off_out_loud_rather_than_omitting_the_parameter` catches the
    # real edit immediately. A mutation that is inert is worse than a missing one, because it
    # sends whoever reads the report to write a test that already exists.
    Mutation(
        "Y6",
        "thinking switched off is sent as off, not as an absent parameter the model reads as its default",
        "gateway/src/aira_gateway/upstreams/openai/mapping/request.py",
        '    if request.thinking is not None:\n        body["reasoning_effort"]',
        "    if request.thinking is not None and request.thinking.mode != ThinkingMode.DISABLED:"
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
        "the compatibility surface does not ignore a field a caller sent",
        # The rule outlived its mechanism. It used to be `extra="forbid"` on the request shapes,
        # and this entry broke that single line — until a real chatbot was measured against the
        # surface and refusal turned out to be the thing making it unusable. The property is
        # unchanged and now has two halves, each with its own entry: `TC1` keeps the refusal where
        # ignoring would answer *wrongly* (a near-miss spelling), and `TC2`/`TC3` keep the naming
        # where it would only answer incompletely. Re-anchored onto the second, because that is
        # the half this id was really about: nothing is dropped in silence.
        "gateway/src/aira_gateway/api/kira/schemas.py",
        "            match = known.get(_normalise(str(sent)))",
        "            match = None",
        NO_SILENT_DROP + " gateway/tests/test_kira_field_spellings.py",
    ),
    Mutation(
        "Y9",
        "a request refused on size is audited, not answered 413 and forgotten",
        "gateway/src/aira_gateway/middleware.py",
        "            await record_oversized(scope, self.max_bytes)\n            await self._reject(scope, send)",
        "            await self._reject(scope, send)",
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
        "gateway/src/aira_gateway/pipeline/classifiers/injection.py",
        "        return Verdict.UNDETERMINED",
        "        return Verdict.CLEAN",
        CLASSIFIERS,
    ),
    Mutation(
        "Z2",
        "an upstream failure in the classifier is undetermined, never clean",
        "gateway/src/aira_gateway/pipeline/classifiers/injection.py",
        # Re-anchored: `FRD-125b` moved this return into `classify_text`, and the harness reported
        # the property as undefended rather than pretending otherwise — which is the behaviour that
        # makes "a mutation whose anchor moved protects nothing" checkable instead of aspirational.
        "            return Classification(Verdict.UNDETERMINED, reached=False)",
        "            return Classification(Verdict.CLEAN, reached=False)",
        CLASSIFIERS,
    ),
    Mutation(
        "Z3",
        "an undetermined verdict blocks a blocking filter rather than passing it through",
        "gateway/src/aira_gateway/pipeline/engine/steps.py",
        "    if verdict is Verdict.UNDETERMINED:\n        return str(config.get",
        "    if False:\n        return str(config.get",
        CLASSIFIERS,
    ),
    Mutation(
        "Z4",
        "a classifier asks for no thinking, so its one-word allowance is not spent reasoning",
        "gateway/src/aira_gateway/pipeline/classifiers/prompts.py",
        "    model: str, instruction: str, text: str, thinking: Thinking | None = THINKING_OFF\n"
        ") -> CanonicalRequest:",
        "    model: str, instruction: str, text: str, thinking: Thinking | None = None\n"
        ") -> CanonicalRequest:",
        CLASSIFIERS,
    ),
    Mutation(
        "Z5",
        "a filter that ran and passed is recorded, so it is distinguishable from no filter",
        "gateway/src/aira_gateway/pipeline/engine/steps.py",
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
        "gateway/src/aira_gateway/api/serving/pipeline_run.py",
        # Widened for the same reason as `T8`: `run_pipeline_over_texts` has the same `finally`,
        # and `Z6b` below is that one's own entry rather than this one's second match.
        "        # One site, on every way out: a blocking filter still spent tokens deciding to block.\n"
        "        await record_pipeline_calls(request, trail)",
        "        # One site, on every way out: a blocking filter still spent tokens deciding to block.\n"
        "        pass",
        ACCOUNTING,
    ),
    Mutation(
        "Z7",
        "a step that blocked still records what deciding to block cost",
        "gateway/src/aira_gateway/api/serving/pipeline_run.py",
        "    finally:\n        # One site",
        "    except Exception:\n        raise\n    else:\n        # One site",
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
        "gateway/src/aira_gateway/api/serving/pipeline_run.py",
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
        "gateway/src/aira_gateway/api/serving/prepare.py",
        # Re-anchored by `FRD-126`: this step moved out of the surfaces and into the one
        # sequence that owns their order. A mutation whose anchor has moved protects nothing.
        "    await guard_before_work(request, units=units)\n\n    fallbacks",
        "    fallbacks",
        f"{ACCOUNTING} gateway/tests/test_serving_options.py",
    ),
    Mutation(
        "Z12",
        "the early gate weighs a batch as the many requests it is",
        "gateway/src/aira_gateway/api/serving/prepare.py",
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
        "gateway/src/aira_gateway/api/serving/controls.py",
        '    if not getattr(request.state, "early_gate_taken", False):',
        "    if False:",
        "gateway/tests/test_ratelimit_routes.py gateway/tests/test_pipeline_accounting.py",
    ),
    Mutation(
        "Z15",
        "the shared sequence resolves thinking after routing, not against the model the caller named",
        "gateway/src/aira_gateway/api/serving/prepare.py",
        '            "thinking": resolve_thinking(asked.thinking, declaration),',
        '            "thinking": asked.thinking,',
        "gateway/tests/test_serving_options.py gateway/tests/test_kira_surface.py "
        "gateway/tests/test_every_candidate_is_sent_its_own_defaults.py",
    ),
    Mutation(
        "Z16",
        "a surface cannot assemble the pre-dispatch order itself",
        "gateway/src/aira_gateway/api/serving/prepare.py",
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
        "gateway/src/aira_gateway/api/serving/accounting.py",
        # Re-anchored by `FRD-128`: the hand-written finisher this pointed at is gone, and the
        # property it named now belongs to every path rather than to one surface's stream.
        "    if not record:\n        return",
        "    if True:\n        return",
        "gateway/tests/test_kira_streaming_disconnect.py gateway/tests/test_kira_surface.py",
    ),
    Mutation(
        "Z18",
        "a request that produced nothing is released, not settled for an answer nobody received",
        "gateway/src/aira_gateway/api/serving/accounting.py",
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
        "gateway/src/aira_gateway/api/serving/accounting.py",
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
        "gateway/src/aira_gateway/api/serving/accounting.py",
        "            requests=state.requests,",
        "            requests=1,",
        "gateway/tests/test_ratelimit_routes.py gateway/tests/test_serving_options.py",
    ),
    Mutation(
        "Z21",
        "an upstream 400 is a precondition failure an operator can fix, not an outage",
        "gateway/src/aira_gateway/api/serving/refusals.py",
        "    if status_code == 400:\n        return UPSTREAM_REFUSED",
        "    if False:\n        return UPSTREAM_REFUSED",
        "gateway/tests/test_error_handling.py",
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
        "C0b",
        "a price pair is checked against the model the edit produces, not against the edit",
        "management/backend/src/aira_management/apps/catalog/serializers.py",
        "        return attrs.get(field, getattr(self.instance, field, None))",
        "        return attrs.get(field)",
        CATALOG,
    ),
    Mutation(
        "N2b",
        "a partial edit is validated against the rule that exists, not against a default",
        "management/backend/src/aira_management/apps/anomalies/serializers.py",
        "        if field in attrs:\n"
        "            return attrs[field]\n"
        "        if self.instance is not None:\n"
        "            return getattr(self.instance, field, default)\n"
        "        return default",
        "        return attrs.get(field, default)",
        ANOMALY_RULES,
    ),
    Mutation(
        "N3a",
        "a rate rule keeps its sample floor, so one refusal of one is not 100 percent",
        "management/backend/src/aira_management/apps/anomalies/serializers.py",
        '        elif self._effective(attrs, "min_sample", DEFAULT_MIN_SAMPLE) < 1:',
        "        elif False:",
        ANOMALY_RULES,
    ),
    Mutation(
        "N4a",
        "only an oversight role may author a rule that acts everywhere",
        "management/backend/src/aira_management/apps/anomalies/views.py",
        '        if not may_author_global(self.request.user):\n            raise PermissionDenied(\n                "Authoring a global rule needs the permission for rules that apply everywhere "',
        '        if False:\n            raise PermissionDenied(\n                "Authoring a global rule needs the permission for rules that apply everywhere "',
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
        "        if self.suspensions is None or not self.suspensions.enforcing or not rule.action_minutes:",
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
        "gateway/src/aira_gateway/api/serving/refusals.py",
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
        "gateway/src/aira_gateway/api/incidents/suspensions.py",
        # Disambiguated 2026-08-12: a second endpoint (`FRD-506`'s reachability check) asks the
        # same predicate, so the bare line matched twice and the harness edits the first. It
        # happened to be the intended one; that it did was luck, and the next endpoint to ask this
        # question would have moved it. Anchored on the refusal `_require_an_incident_role` raises,
        # only the kill switch has.
        "    if not principal.allows(Permission.INCIDENT_SUSPEND):\n        raise GeminiHTTPError(\n            403,\n"
        '            "Suspending or restoring access needs the permission to stop traffic "',
        "    if False:\n        raise GeminiHTTPError(\n            403,\n"
        '            "Suspending or restoring access needs the permission to stop traffic "',
        "gateway/tests/test_suspensions.py gateway/tests/test_reporting.py",
    ),
    # ---- what the live round found (FRD-503 §7) -------------------------------------------
    Mutation(
        "N20",
        "a refused request is not counted as unpriced traffic",
        "gateway/src/aira_gateway/reporting/service.py",
        "    return (RequestLog.outcome == Outcome.SERVED) |",
        "    return (RequestLog.outcome != Outcome.SERVED) |",
        REPORTING,
    ),
    Mutation(
        "N21",
        "a row written before outcomes existed is still counted as unpriced",
        "gateway/src/aira_gateway/reporting/service.py",
        "| (RequestLog.outcome.is_(None))",
        "| (False)",
        REPORTING,
    ),
    Mutation(
        "N23",
        "every topic the code publishes to is one something creates",
        "Makefile",
        "rate-limits models anomaly-rules roles; do",
        "rate-limits models roles; do",
        TOPICS,
    ),
    # ---- the trace overview (FRD-502) -------------------------------------------------------
    Mutation(
        "N24",
        "a trace carries no payload, whatever the retention settings stored",
        "gateway/src/aira_gateway/api/reporting/traces.py",
        'TRACE_FIELDS = (\n    "id",',
        'TRACE_FIELDS = (\n    "request_payload",\n    "response_payload",\n    "id",',
        "gateway/tests/test_traces.py",
    ),
    Mutation(
        "N25",
        "a trace list is scoped to the use cases the caller is in",
        "gateway/src/aira_gateway/api/reporting/traces.py",
        "        stmt = stmt.where(RequestLog.use_case.in_(allowed))\n    if use_case:",
        "        stmt = stmt\n    if use_case:",
        "gateway/tests/test_traces.py",
    ),
    Mutation(
        "N26",
        "paging by (timestamp, id) shows no row twice when two share a moment",
        "gateway/src/aira_gateway/api/reporting/traces.py",
        "                and_(RequestLog.created_at == at, RequestLog.id < row_id),",
        "                and_(RequestLog.created_at == at, RequestLog.id <= row_id),",
        "gateway/tests/test_traces.py",
    ),
    Mutation(
        "N28",
        "an empty answer says whether it means 'nothing happened' or 'you see nothing'",
        "gateway/src/aira_gateway/api/reporting/traces.py",
        'return {"traces": [], "next_cursor": None, "scope": "use_cases", "in_scope": False}',
        'return {"traces": [], "next_cursor": None, "scope": "use_cases", "in_scope": True}',
        "gateway/tests/test_traces.py",
    ),
    Mutation(
        "N29",
        "findings are asked for by use case, not filtered out of a global page",
        "gateway/src/aira_gateway/api/reporting/anomalies.py",
        "    if use_case:\n        stmt = stmt.where(AnomalyEvent.use_case == use_case)",
        "    if False:\n        stmt = stmt.where(AnomalyEvent.use_case == use_case)",
        "gateway/tests/test_anomaly_engine.py",
    ),
    # ---- what an incident is allowed to ask (FRD-131 FR-7, FRD-502) -------------------------
    Mutation(
        "N40",
        "IT Security's own console is not empty — the wider role decides who sees everything",
        "gateway/src/aira_gateway/api/reporting/common.py",
        "    if principal.allows(permission):\n        return None",
        "    if principal.allows(Permission.USECASE_READ_RETIRED):\n        return None",
        "gateway/tests/test_traces.py gateway/tests/test_reporting.py",
    ),
    Mutation(
        "N41",
        "the calling machine's address is shown only to a role that may act on an incident",
        "gateway/src/aira_gateway/api/reporting/traces.py",
        "    return TRACE_FIELDS + (INCIDENT_FIELDS if investigating else ())",
        "    return TRACE_FIELDS + INCIDENT_FIELDS",
        "gateway/tests/test_traces.py",
    ),
    Mutation(
        "N42",
        "a filter nobody may use is refused, never quietly ignored",
        "gateway/src/aira_gateway/api/reporting/traces.py",
        "    if source_ip and not investigating:",
        "    if False:",
        "gateway/tests/test_traces.py",
    ),
    Mutation(
        "N43",
        "'only my own requests' means the caller's identity, not everybody's",
        "gateway/src/aira_gateway/api/reporting/traces.py",
        "        stmt = stmt.where(own_requests(principal))",
        "        stmt = stmt",
        "gateway/tests/test_traces.py gateway/tests/test_own_requests_are_the_persons.py",
    ),
    Mutation(
        "N44",
        "'only tool turns' excludes the requests where the model asked for nothing",
        "gateway/src/aira_gateway/api/reporting/traces.py",
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
        '    if principal.allows(Permission.PAYLOAD_READ_ANY):\n        return "incident"',
        '    if False:\n        return "incident"',
        "gateway/tests/test_payload_access.py",
    ),
    Mutation(
        "N46b",
        "a grant on a group makes an administrator, the same as a grant naming a person",
        "gateway/src/aira_gateway/payloads.py",
        "    return strongest([role for role in (dict(principal.grants).get(use_case), row) if role])",  # noqa: E501
        "    return strongest([role for role in (None, row) if role])",
        "gateway/tests/test_payload_access.py",
    ),
    Mutation(
        "N46c",
        "the role the grant resolver worked out reaches the principal that carries it",
        "gateway/src/aira_gateway/auth/dependencies.py",
        "    return replace(principal, use_cases=merged, grants=tuple(sorted(granted.items())))",
        "    return replace(principal, use_cases=merged)",
        "gateway/tests/test_group_grants.py",
    ),
    Mutation(
        "N47",
        "an oversight role is told it sees figures, not that the use case is not theirs",
        "gateway/src/aira_gateway/payloads.py",
        "        if principal.allows(Permission.TRACE_READ_ALL):\n            return PayloadRefusal.NOT_A_CONTENT_ROLE",
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
        "    if visibility == PayloadVisibility.OWN_REQUESTS_ONLY and not is_own_request(",
        "    if False and not is_own_request(",
        "gateway/tests/test_payload_access.py",
    ),
    Mutation(
        "N50",
        "a member reads a colleague's request until somebody restricts it",
        "gateway/src/aira_gateway/payloads.py",
        "    if visibility == PayloadVisibility.OWN_REQUESTS_ONLY and not is_own_request(",
        "    if not is_own_request(",
        "gateway/tests/test_payload_access.py",
    ),
    # ---- "own" means the person, not one credential's alphabet (FRD-606 × FRD-505) -----------
    #
    # The two credentials answer *who is this* in different alphabets, so `row.subject ==
    # principal.subject` compared a directory id with a username for every console reader and
    # matched nothing. Measured: a member of a use case that restricts members to their own
    # requests saw an empty trace list with their own rows in the table, and `403 others_request`
    # on their own prompt.
    #
    # Both directions, like `N49`/`N50` above. `N60` and `N62` narrow the rule back to the subject
    # — the defect itself — and `N61` widens it past *own*, which is the one that would matter for
    # a reason other than usability. The whole matrix in `test_payload_access.py` uses principals
    # whose subject and username coincide, which is exactly why it could not see any of this; the
    # tests named here are the ones that build a principal like a real token.
    Mutation(
        "N60",
        "a person's own request is theirs whichever credential made it (the predicate)",
        "gateway/src/aira_gateway/payloads.py",
        "    person = principal.person\n    return bool(person and _row_person(row.subject, row.username) == person)",
        "    return False",
        "gateway/tests/test_own_requests_are_the_persons.py",
    ),
    Mutation(
        "N61",
        "and never anybody else's — the widening stops at one person",
        "gateway/src/aira_gateway/payloads.py",
        "    return bool(person and _row_person(row.subject, row.username) == person)",
        "    return bool(person)",
        "gateway/tests/test_own_requests_are_the_persons.py",
    ),
    Mutation(
        "N62",
        "the same rule reaches the list, not only the single payload (the query)",
        "gateway/src/aira_gateway/payloads.py",
        "        own.append(RequestLog.username == person)",
        "        pass",
        "gateway/tests/test_own_requests_are_the_persons.py",
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
        "gateway/src/aira_gateway/api/reporting/traces.py",
        "        if row is None or (scope is not None and row.use_case not in scope):",
        "        if row is None:",
        "gateway/tests/test_payload_access.py",
    ),
    Mutation(
        "N54",
        "a read of stored content is recorded",
        "gateway/src/aira_gateway/api/reporting/traces.py",
        "        session.add(_access(principal, row, ground=verdict.ground, kind=ACCESS_CONTENT))",
        "        _access(principal, row, ground=verdict.ground, kind=ACCESS_CONTENT)",
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
        "gateway/src/aira_gateway/upstreams/openai/mapping/response.py",
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
        "gateway/src/aira_gateway/upstreams/vertex/anthropic_mapping/request.py",
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
        "gateway/src/aira_gateway/upstreams/vertex/anthropic_mapping/request.py",
        '    return {**_EPHEMERAL, "ttl": "1h"} if request.cache_ttl == "1h" else dict(_EPHEMERAL)',
        '    return {**_EPHEMERAL, "ttl": "1h"}',
        "gateway/tests/test_prompt_cache_accounting.py",
    ),
    Mutation(
        "U8",
        "the lifetime the console chose is the one that reaches the provider",
        "gateway/src/aira_gateway/api/serving/controls.py",
        '    chosen = getattr(record, "prompt_cache_ttl", None)',
        "    chosen = None",
        "gateway/tests/test_prompt_cache_accounting.py",
    ),
    Mutation(
        "U9",
        "a use case that did not opt in is never marked, however cheap it would be",
        "gateway/src/aira_gateway/api/serving/controls.py",
        "    return bool(record is not None and record.prompt_caching_enabled)",
        "    return True",
        "gateway/tests/test_prompt_cache_accounting.py",
    ),
    Mutation(
        "U6",
        "a request that did not opt in is byte-identical to what it was before caching existed",
        "gateway/src/aira_gateway/upstreams/vertex/anthropic_mapping/request.py",
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
        "gateway/src/aira_gateway/pipeline/classifiers/injection.py",
        "            elif (reason := catastrophic_reason(pattern)) is not None:",
        "            elif False:",
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
        "    held = roles_from_groups(token_groups(claims), role_groups())",
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
        '                "roles": [str(role) for role in ALL_ROLES if str(role) in held]\n',
        '                "roles": (claims.get("realm_access") or {}).get("roles", [])\n',
        "management/backend/tests/test_api.py",
    ),
    Mutation(
        "R27",
        "creating a use case is a Global Administrator's act",
        "management/backend/src/aira_management/apps/usecases/views/viewset.py",
        "            return [IsAuthenticated(), requires(Permission.USECASE_CREATE)()]",
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
        "gateway/src/aira_gateway/api/reporting/spend.py",
        "        if scope is None or use_case in scope:\n            scope = (use_case,)",
        "        if True:\n            scope = (use_case,)",
        "gateway/tests/test_reporting.py",
    ),
    Mutation(
        "N56",
        "an empty report says whether it was allowed to be full",
        "gateway/src/aira_gateway/api/reporting/spend.py",
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
        "management/backend/src/aira_management/apps/usecases/views/members.py",
        "            _revoke(group, usecase)\n            _grant(group, usecase, role)",
        "            _grant(group, usecase, role)",
        "management/backend/tests/test_group_grants.py",
    ),
    Mutation(
        "N37",
        "a group grant makes somebody a member, which is what issuing a key needs",
        "management/backend/src/aira_management/apps/usecases/access.py",
        "        return frozenset([*direct, *through_groups])",
        "        return frozenset(direct)",
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
        "        return frozenset(resolve(held, grants, direct))",
        "        return frozenset(resolve(held, grants))",
        "management/backend/tests/test_group_grants.py management/backend/tests/test_usecases.py",
    ),
    Mutation(
        "N27",
        "a page says there is more only when there is",
        "gateway/src/aira_gateway/api/reporting/traces.py",
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
        "S1b",
        "a caller's date on the register is a refusal, never a server error",
        "gateway/src/aira_gateway/api/reporting/common.py",
        # The register reads its window through `window()`, which the report shares, so the
        # defect — a caller's date reaching `datetime.fromisoformat` unguarded — lives there once.
        # `/v1beta/register` is the endpoint the sweep could not see before it read the served
        # document.
        '    window_start = _parse(start, "from") if start else default_start\n'
        '    window_end = _parse(end, "to") if end else default_end',
        "    window_start = datetime.fromisoformat(start) if start else default_start\n"
        "    window_end = datetime.fromisoformat(end) if end else default_end",
        "gateway/tests/test_a_callers_value_is_never_a_server_error.py",
    ),
    Mutation(
        "H3b",
        "every configured realm's URLs are checked, not just the last one named",
        "libs/src/aira_common/transport_security.py",
        "        for name, url in sorted(named_urls)",
        "        for name, url in sorted(dict(named_urls).items())",
        f"{DEPLOYMENT_SAFETY} libs/tests/test_transport_security.py",
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
        # Re-anchored again when `FRD-118` moved the audience into a per-issuer list: the check
        # now walks `settings.issuers()`, so the old anchor named a line that no longer exists.
        '    if "AIRA_OIDC_AUDIENCE" not in waived and settings.oidc_enabled and unnamed:',
        "    if False:",
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
        "libs/src/aira_common/observability/redaction.py",
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
        "management/backend/src/aira_management/apps/usecases/views/payloads.py",
        '        "expires_at": key.expires_at.isoformat() if key.expires_at is not None else None,',
        '        "expires_at": None,',
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
        "                redacted: dict[str, Any] = self._redactor.redact(stripped)",
        "                redacted: dict[str, Any] = stripped",
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
        "libs/src/aira_common/apikeys.py",
        "    if days > max_days:",
        "    if False:",
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
        "gateway/src/aira_gateway/api/serving/controls.py",
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
        "gateway/src/aira_gateway/upstreams/openai/mapping/response.py",
        '                entry["arguments"] += str(function["arguments"])',
        '                entry["arguments"] = str(function["arguments"])',
        TOOLS,
    ),
    Mutation(
        "T23",
        "the audit row records what the model asked to have run",
        "gateway/src/aira_gateway/api/serving/accounting.py",
        "        tool_calls=tool_summary(trail),\n        provenance=await provenance(request, model, trail.served_region),",  # noqa: E501
        "        tool_calls=None,\n        provenance=await provenance(request, model, trail.served_region),",  # noqa: E501
        TOOLS,
    ),
    Mutation(
        "T24",
        "the streamed exit records its tool calls too, not only the buffered one",
        "gateway/src/aira_gateway/api/serving/streaming.py",
        "            self._tool_calls.extend(call.name for call in chunk.tool_calls)",
        "            pass",
        TOOLS,
    ),
    Mutation(
        "T25",
        "a streamed tool call reaches the client, not only the audit row",
        "gateway/src/aira_gateway/api/gemini/mapping.py",
        "    parts.extend(_call_part(call) for call in chunk.tool_calls)",
        "    parts.extend(_call_part(call) for call in ())",
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
        "gateway/src/aira_gateway/upstreams/vertex/anthropic_mapping/response.py",
        "    try:\n        json.loads(text)\n    except ValueError:\n        return None\n    return text",
        "    return text",
        "gateway/tests/test_vertex.py",
    ),
    Mutation(
        "Q1",
        "a standing is the latest run, never a total across every run",
        "management/backend/src/aira_management/apps/smoketests/views/stats.py",
        'order_by("-started_at", "-id")',
        'order_by("started_at", "id")',
        "management/backend/tests/test_smoketests.py",
    ),
    Mutation(
        "Q1b",
        "a run may only be entered at a model the use case has been released",
        "management/backend/src/aira_management/apps/smoketests/views/runs.py",
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
        "management/backend/src/aira_management/apps/smoketests/views/runs.py",
        "        use_case = may_run_tests_queryset(\n"
        "            self.request.user, UseCase.objects.filter(slug=slug)\n"
        "        ).first()",
        "        use_case = UseCase.objects.filter(slug=slug).first()",
        "management/backend/tests/test_smoketests.py",
    ),
    Mutation(
        "Q1d",
        "a use case with nothing released is refused by name rather than run against nothing",
        "management/backend/src/aira_management/apps/smoketests/views/runs.py",
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
        "    reachable = may_call_queryset(user, queryset.live())",
        "    reachable = queryset.live()",
        "management/backend/tests/test_smoketests.py",
    ),
    Mutation(
        "Q2",
        "a run nobody has read is not a run that passed",
        "management/backend/src/aira_management/apps/smoketests/serializers.py",
        "counts[result.verdict] = counts.get(result.verdict, 0) + 1",
        "counts[result.verdict] = counts.get(result.verdict, 0)",
        "management/backend/tests/test_smoketests.py",
    ),
    Mutation(
        "Q5",
        "attribution offers what the gateway accepts, which grants a global admin no blanket",
        "management/backend/src/aira_management/apps/usecases/access.py",
        "    return queryset.filter(slug__in=sorted(_callable_slugs(user)))",
        "    return queryset if may(user, Permission.USECASE_MANAGE_ALL) else queryset.filter(slug__in=sorted(_callable_slugs(user)))",
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
        "Q4",
        "a retired question is not asked, so a model is judged against the current standard",
        "management/backend/src/aira_management/apps/smoketests/views/runs.py",
        "for case in TestCase.objects.filter(retired=False)",
        "for case in TestCase.objects.all()",
        "management/backend/tests/test_smoketests.py",
    ),
    Mutation(
        "T30",
        "the provider's required schema tightenings are added, not assumed",
        "gateway/src/aira_gateway/upstreams/vertex/anthropic_mapping/schema.py",
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
        "    if not principal.allows(Permission.CATALOG_WRITE):",
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
        "gateway/src/aira_gateway/api/serving/context.py",
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
        "management/backend/src/aira_management/apps/usecases/views/payloads.py",
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
        "gateway/src/aira_gateway/pipeline/engine/steps.py",
        "        return self._registry.provider_for(model, declaration.provider, declaration.publisher)",
        "        return None",
        "gateway/tests/test_pipeline_engine.py",
    ),
    Mutation(
        "J17",
        "a router that could not be asked says so instead of reading as 'nothing matched'",
        "gateway/src/aira_gateway/pipeline/engine/steps.py",
        '            decision = {"step": "model_route", "action": "not_asked", "why": reason}',
        '            decision = {"step": "model_route", "action": "unchanged"}',
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
        "        # is about a model that can think; here there is no default to beat.\n"
        "        return None",
        "        return Thinking(mode=ThinkingMode.DISABLED, tokens=0)",
        "gateway/tests/test_thinking.py",
    ),
    Mutation(
        "J22",
        "a model that will think gets room for the thinking as well as the word",
        "gateway/src/aira_gateway/pipeline/classifiers/prompts.py",
        "            else THINKING_CLASSIFIER_OUTPUT_TOKENS",
        "            else CLASSIFIER_OUTPUT_TOKENS",
        CLASSIFIERS,
    ),
    # -- 2026-08-11 quality/security review: three defects, each a rule the code claimed and did
    #    not have. Each mutation below was observed to fail before its fix was written.
    Mutation(
        "QA1",
        "a request with no chain still meets the dispatch conditions",
        "gateway/src/aira_gateway/api/serving/prepare.py",
        "    refusal = await permits(model)",
        "    refusal = None",
        STREAM_CONDITIONS,
    ),
    Mutation(
        "QA2",
        "a streamed request is answered by the model routing chose, not the one it named",
        "gateway/src/aira_gateway/api/gemini/routes.py",
        "    return await _stream_response(\n        request,\n        asked,",
        '    return await _stream_response(\n        request,\n        asked.model_copy(update={"model": model}),',
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
        "gateway/src/aira_gateway/exception_handlers.py",
        # Re-anchored: the handler learned to separate "nothing was presented" from "what was
        # presented was rejected" for a 401, so the code is chosen above the return rather than in
        # it. The branch this switches off is the same one, and it is the one the property is
        # about — `_kira(request)` is what decides whose envelope a refusal goes out in.
        "    if _kira(request):\n        code = kira_code_for_status(exc.code)",
        "    if False:\n        code = kira_code_for_status(exc.code)",
        KIRA_ENVELOPE,
    ),
    Mutation(
        "QA11",
        "the body ceiling answers each surface in its own error language",
        "gateway/src/aira_gateway/middleware.py",
        "    if is_kira_path(request.url.path):",
        "    if False:",
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
        "gateway/src/aira_gateway/api/kira/info_routes.py",
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
        "gateway/src/aira_gateway/api/serving/controls.py",
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
        "    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)\n    # Outside the ceiling, so it refuses on the path every route sees: `/uc/<slug>/kira/…` is a KIRA\n    # request when it is too large, too, and is audited as one.\n    app.add_middleware(UseCasePathMiddleware)\n    # Outside both, so a browser can read a refusal from either rather than a network error.\n    configure_cors(app, settings)\n    app.add_middleware(SecurityHeadersMiddleware)\n",
        "    app.add_middleware(SecurityHeadersMiddleware)\n    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)\n    # Outside the ceiling, so it refuses on the path every route sees: `/uc/<slug>/kira/…` is a KIRA\n    # request when it is too large, too, and is audited as one.\n    app.add_middleware(UseCasePathMiddleware)\n    # Outside both, so a browser can read a refusal from either rather than a network error.\n    configure_cors(app, settings)\n",
        ERROR_HEADERS,
    ),
    Mutation(
        "QA6",
        "an unhandled error carries them too — the one response no middleware can reach",
        "gateway/src/aira_gateway/exception_handlers.py",
        "    for name, value in SecurityHeadersMiddleware.HEADERS:",
        "    for name, value in ():",
        ERROR_HEADERS,
    ),
    Mutation(
        "QA15",
        "a stream hands each piece over as it is produced, not the lot at the end",
        "gateway/src/aira_gateway/api/kira/routes.py",
        '                    yield f"data: {json.dumps(update_event(led))}\\n\\n"\n            except KIRA_REFUSALS as exc:',
        '                    pass\n                for piece in [stream.text]:\n                    yield f"data: {json.dumps(update_event(piece))}\\n\\n"\n            except KIRA_REFUSALS as exc:',
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
        "gateway/src/aira_gateway/exception_handlers.py",
        "    app.exception_handler(KiraError)(_handle_kira_error)",
        "    pass  # no handler for KiraError",
        KIRA_ENVELOPE,
    ),
    Mutation(
        "QA19",
        "health answers the predecessor's shape rather than one we invented",
        "gateway/src/aira_gateway/api/kira/info_routes.py",
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
        "gateway/src/aira_gateway/api/gemini/mapping.py",
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
        # Re-anchored when `FRD-135` made reasoning a per-use-case decision: the refusal left the
        # schema for the surface, where the use case is known. Same property, and it must still
        # **name the field** — the message is what the test asserts on.
        "asking for the model's reasoning is refused rather than answered without it",
        "gateway/src/aira_gateway/api/serving/controls.py",
        "            f\"'includeThoughts' asks for the model's reasoning, and {why} (FRD-135). Send it as \"",
        '            "a thing this gateway does not do "',
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
        "        if record.revoked_at is None:\n            record.revoked_at = datetime.now(UTC)",
        "",
        REVOCATION_TIME,
    ),
    Mutation(
        "P10",
        "a rewrite that cannot be trusted blocks instead of being applied",
        "gateway/src/aira_gateway/pipeline/classifiers/redaction.py",
        # Re-anchored with `FRD-135`'s round: the ratio now applies only above a length floor,
        # because on a six-character prompt it refused a correct redaction.
        "        if len(given) >= self.MIN_LENGTH_FOR_RATIO and len(rewritten) < len(given) * self.MIN_KEPT:",  # noqa: E501
        "        if False:",
        PII,
    ),
    Mutation(
        "P11",
        "the request that goes upstream is the rewritten one",
        "gateway/src/aira_gateway/pipeline/engine/steps.py",
        "            request=_with_user_text(request, result.text) if changed else None,",
        "            request=None,",
        PII,
    ),
    Mutation(
        "P12",
        "the **stored** request is the rewritten one too",
        "gateway/src/aira_gateway/api/serving/pipeline_run.py",
        "        if needle not in text:\n            return None",
        "        if needle not in text:\n            return body",
        PII,
    ),
    Mutation(
        "P12b",
        "the stored request is the rewritten one on the **refused** path as well",
        "gateway/src/aira_gateway/api/serving/pipeline_run.py",
        "    if rewrites:\n        trail.body = _rewritten_body(trail.body, rewrites)",
        "    pass",
        PII,
    ),
    Mutation(
        "P13",
        "a redactor that failed refuses rather than passing the original through",
        "gateway/src/aira_gateway/pipeline/engine/steps.py",
        '            block_reason=None if allows else f"Personal data could not be removed: {why}.",',
        "            block_reason=None,",
        PII,
    ),
    Mutation(
        "P14",
        "a notice is never put in front of an answer a client parses",
        "gateway/src/aira_gateway/api/serving/answers.py",
        # A schema-constrained answer is non-empty text without a tool call, so only a fact about
        # the *request* (`structured`) can keep the notice away from it.
        "    if structured:\n        return WITHHELD_STRUCTURED\n",
        "",
        NOTICE,
    ),
    Mutation(
        "P14b",
        "a streamed answer is led by the notice, and only the first piece of text is",
        "gateway/src/aira_gateway/api/serving/answers.py",
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
        "management/backend/src/aira_management/apps/usecases/views/viewset.py",
        '        if getattr(self, "action", None) != "list":\n            return False',
        "        if False:\n            return False",
        "management/backend/tests/test_may_call_never_widens_a_detail_route.py",
    ),
    Mutation(
        "RV2",
        "a findings restriction is written over the findings, not over unrelated rows",
        "gateway/src/aira_gateway/api/reporting/anomalies.py",
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
        "    if not principal.allows(Permission.REPORT_READ_ALL):",
        "    if not principal.allows(Permission.USECASE_READ_RETIRED):",
        "gateway/tests/test_oversight_reads_every_figure.py",
    ),
    Mutation(
        "RV5",
        "IT Security is not refused the predecessor's usage report either",
        "gateway/src/aira_gateway/api/kira/info_routes.py",
        "    if not principal.allows(Permission.REPORT_READ_ALL):",
        "    if not principal.allows(Permission.USECASE_READ_RETIRED):",
        "gateway/tests/test_oversight_reads_every_figure.py",
    ),
    Mutation(
        "RV6",
        "an unexpected failure answers in the surface's own envelope",
        "gateway/src/aira_gateway/exception_handlers.py",
        "    if _kira(request):\n"
        "        response = kira_error_response(500, kira_code_for_status(500), "
        '"Internal server error.")',
        "    if False:\n"
        "        response = kira_error_response(500, kira_code_for_status(500), "
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
        "gateway/src/aira_gateway/api/serving/context.py",
        "        memoised = source.per_request()\n        request.state.catalog = memoised",
        "        memoised = source.per_request()",
        "gateway/tests/test_a_request_costs_a_bounded_number_of_reads.py",
    ),
    Mutation(
        "RV10",
        "a budget counter always carries an expiry, including the one that refused",
        "gateway/src/aira_gateway/budgets/ledger.py",
        "if redis.call('TTL', key) == -1 then\n  redis.call('EXPIRE', key, ARGV[10])\nend",
        "",
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
        "gateway/src/aira_gateway/pipeline/engine/steps.py",
        "            if template and category\n            else None",
        "            if template\n            else None",
        PII,
    ),
    Mutation(
        "QA31",
        "a pipeline's own model call is filed under the surface that caused it",
        "gateway/src/aira_gateway/api/serving/pipeline_run.py",
        "                api=trail.api,",
        '                api="gemini",',
        SURFACE_PARITY,
    ),
    Mutation(
        "QA32",
        "a refused request still records the functions it offered",
        "gateway/src/aira_gateway/api/serving/accounting.py",
        "        tool_calls=tool_summary(trail),\n"
        "        provenance=await provenance(request, trail.served_model, trail.served_region),",
        "        provenance=await provenance(request, trail.served_model, trail.served_region),",
        SURFACE_PARITY,
    ),
    Mutation(
        "QA33",
        "both surfaces read a thinking mode string the same way",
        "gateway/src/aira_gateway/thinking.py",
        "    mode = raw.strip().lower()",
        "    mode = raw",
        MODE_PARSE,
    ),
    # -- the dry run explains itself. The trace used to be a badge and a word, which says what
    #    happened and never why — and for the three LLM-backed steps the why is a model's own
    #    answer that nothing carried. These are what make the screen an explanation.
    Mutation(
        "QA34",
        "the dry run shows what the step's model actually replied",
        "gateway/src/aira_gateway/pipeline/engine/steps.py",
        '        **({"output": _shown(reply)} if reply.strip() else {}),',
        "",
        CLASSIFIERS,
    ),
    Mutation(
        "QA35",
        "a redaction is shown as what it did to the caller's sentence, not as a badge",
        "gateway/src/aira_gateway/pipeline/engine/steps.py",
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
        "gateway/src/aira_gateway/api/serving/controls.py",
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
        "gateway/src/aira_gateway/api/serving/controls.py",
        '    caller = getattr(attribution, "person", None)\n\n    expected = await estimate(',
        '    caller = getattr(attribution, "subject", None)\n\n    expected = await estimate(',
        "gateway/tests/test_one_person_one_allowance.py",
    ),
    Mutation(
        "QA46",
        "one person using two credentials is one figure, not two rows nothing joins",
        "gateway/src/aira_gateway/db/models/owned.py",
        'REQUEST_PERSON = func.coalesce(func.nullif(RequestLog.username, ""), RequestLog.subject)',
        "REQUEST_PERSON = RequestLog.subject",
        "gateway/tests/test_reporting.py",
    ),
    Mutation(
        "QA47",
        "the name is written beside the subject, or nothing can join a person to themselves",
        "gateway/src/aira_gateway/persistence/service.py",
        "            username=_fits(username, SUBJECT_COLUMN),",
        "            username=None,",
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
        "gateway/src/aira_gateway/pipeline/engine/runner.py",
        "                if not past_blocks:\n                    break",
        "                pass",
        "gateway/tests/test_pipeline_engine.py gateway/tests/test_pipeline_dryrun.py",
    ),
    Mutation(
        "QA38",
        "a step that only ran past a block is marked as the simulation it is",
        "gateway/src/aira_gateway/pipeline/engine/runner.py",
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
        "SC1",
        "the showcase offers the model the demo actually has, not merely the first one",
        "tools/showcase_try_it.py",
        '        if str(model.get("name")) == DEMO_CHAT_MODEL:\n            return str(model["name"]), int(model["id"])',
        "        pass",
        "tools/tests/test_the_showcase_offers_its_own_model.py",
    ),
    Mutation(
        "TM1",
        "a mode the dialect cannot express is marked before it is saved",
        "gateway/src/aira_gateway/api/incidents/thinking_check.py",
        '            "accepted": mode in expressible,',
        '            "accepted": True,',
        "gateway/tests/test_model_check.py",
    ),
    Mutation(
        "TM2",
        "an adapter that declares no modes is not marked red for it",
        "gateway/src/aira_gateway/api/incidents/thinking_check.py",
        "    if not declared:\n        return {str(mode) for mode in ThinkingMode}",
        "    if not declared:\n        return set()",
        "gateway/tests/test_model_check.py",
    ),
    # -- A dialect that cannot express the request refuses by name (2026-08-20).
    Mutation(
        "DU1",
        "a mapping that cannot express the request is a refusal, not a 500",
        "gateway/src/aira_gateway/api/serving/refusals.py",
        "    DialectUnsupported,\n    GeminiHTTPError,\n)",
        "    GeminiHTTPError,\n)",
        "gateway/tests/test_a_dialect_that_cannot_say_it.py",
    ),
    Mutation(
        "DU2",
        "the Gemini surface gives the dialect's own explanation, not an internal error",
        "gateway/src/aira_gateway/api/gemini/refusals.py",
        '        return _error(400, str(exc), "FAILED_PRECONDITION")\n    if isinstance(exc, UpstreamError):',
        '        return _error(500, "Internal error.", "INTERNAL")\n    if isinstance(exc, UpstreamError):',
        "gateway/tests/test_a_dialect_that_cannot_say_it.py",
    ),
    Mutation(
        "DU3",
        "the compatibility surface says the request is wrong, not that the model is missing",
        "gateway/src/aira_gateway/api/kira/refusals.py",
        "    if isinstance(exc, NoCapableModel | DialectUnsupported):",
        "    if isinstance(exc, NoCapableModel | DialectUnsupported) and False:",
        "gateway/tests/test_a_dialect_that_cannot_say_it.py",
    ),
    # -- `FRD-610`: nothing spends outside a bucket.
    Mutation(
        "IB1",
        "an installation budget binds only a request that names no use case",
        "gateway/src/aira_gateway/scopes.py",
        '            return cls("") if not use_case else None',
        '            return cls("")',
        "gateway/tests/test_installation_budget.py",
    ),
    Mutation(
        "IB2",
        "the installation counter has its own prefix, not an empty use case's",
        "gateway/src/aira_gateway/scopes.py",
        '            return "installation:"',
        '            return f"uc:{self.use_case}"',
        "gateway/tests/test_installation_budget.py",
    ),
    Mutation(
        "IB3",
        "a refusal says which allowance ran out",
        "gateway/src/aira_gateway/scopes.py",
        '            return "installation"',
        '            return "use case"',
        "gateway/tests/test_installation_budget.py",
    ),
    Mutation(
        "IB4",
        "spend that names no use case is reserved rather than waved through",
        "gateway/src/aira_gateway/budgets/service.py",
        "            if not self._enforce:\n                return Reservation(\n",
        "            if not self._enforce or not use_case:\n                return Reservation(\n",
        "gateway/tests/test_installation_budget.py",
    ),
    # Anchored in the **migration**, not in `models.py`: the test database is built from
    # migrations, so a constraint edited only on the model is a constraint the database never
    # hears about — both of these survived that way first, which is exactly the failure they are
    # here to describe. A `Meta.constraints` entry with no migration is enforced by nothing.
    Mutation(
        "IB5",
        "two installation budgets for one period are refused by the database, not only by a form",
        "management/backend/src/aira_management/apps/budgets/migrations/0005_installation_budget.py",
        '                condition=models.Q(("use_case__isnull", True)),',
        '                condition=models.Q(("use_case__isnull", False)),',
        "management/backend/tests/test_installation_budget.py",
    ),
    Mutation(
        "IB6",
        "a scope and an owner that disagree are refused by the database",
        "management/backend/src/aira_management/apps/budgets/migrations/0005_installation_budget.py",
        '                    models.Q(("scope", "installation"), ("use_case__isnull", True)),',
        '                    models.Q(("scope", "installation")),',
        "management/backend/tests/test_installation_budget.py",
    ),
    Mutation(
        "IB7",
        "the event that configures the installation bucket carries an empty use case",
        "management/backend/src/aira_management/apps/budgets/views.py",
        '            emit("budget.upserted", _budget_payload(budget, ""))',
        '            emit("budget.upserted", _budget_payload(budget, "installation"))',
        "management/backend/tests/test_installation_budget.py",
    ),
    Mutation(
        "IB8",
        "governance reads the installation's spend limit and does not set it",
        "management/backend/src/aira_management/apps/budgets/views.py",
        "        if not may(request.user, Permission.REPORT_READ_ALL):\n            return Response([], status=status.HTTP_200_OK)",
        "        pass",
        "management/backend/tests/test_installation_budget.py",
    ),
    Mutation(
        "QA36",
        "a router's answer names the model that gave it, not the one routed to",
        "gateway/src/aira_gateway/pipeline/engine/steps.py",
        "                        routing.reply,\n                        model,",
        "                        routing.reply,",
        CLASSIFIERS,
    ),
    # ── the review round of 2026-08-20: six defects, each reintroduced by one edit ────────────
    #
    # Every one of these was found by asking a question the existing tests did not: *what does this
    # do with a value nobody meant to send*. The tests that catch them are named beside each.
    Mutation(
        "RB1",
        "a rule whose target this build has no column for measures nothing, and does not raise",
        "gateway/src/aira_gateway/anomalies/evaluator.py",
        "    if _group_by(rule) is None:",
        "    if False:",
        ANOMALY_ENGINE,
    ),
    Mutation(
        "RB2",
        "a rule whose action this build cannot carry out is recorded, not raised past the tick",
        "gateway/src/aira_gateway/anomalies/service.py",
        "            action = None",
        "            raise",
        ANOMALY_ENGINE,
    ),
    Mutation(
        "RB3",
        "a fallback candidate is addressed where the catalogue puts it, not where the primary is",
        "gateway/src/aira_gateway/pipeline/dispatch.py",
        '            update["addressing"] = routing.addressing',
        "            pass",
        "gateway/tests/test_pipeline_dispatch.py",
    ),
    Mutation(
        "RB4",
        "an unstorable dict *key* costs the value and not the row, exactly as a value does",
        "gateway/src/aira_gateway/persistence/sanitize.py",
        "        return {str(storable(str(key))): storable(item) for key, item in value.items()}",
        "        return {str(key): storable(item) for key, item in value.items()}",
        LOG_WRITER,
    ),
    Mutation(
        "RB5",
        "a body that is not JSON is a refusal on the incident endpoints too, never a 500",
        "gateway/src/aira_gateway/api/incidents/common.py",
        "    try:\n        body = await json_body(request)\n    except ValueError as exc:",
        "    if True:\n        body = await json_body(request)\n    elif False:",
        SUSPENSIONS + " gateway/tests/test_model_check.py",
    ),
    Mutation(
        "RB6",
        "a suspension's two numbers are parsed, not `int()`-ed at the caller's risk",
        "gateway/src/aira_gateway/api/incidents/suspensions.py",
        "    try:\n        number = int(value)\n    except ValueError as exc:",
        "    if True:\n        number = int(value)\n    elif False:",
        SUSPENSIONS,
    ),
    Mutation(
        "RB7",
        "a suspension's two numbers are bounded, so neither overflows what stores it",
        "gateway/src/aira_gateway/api/incidents/suspensions.py",
        "    if not minimum <= number <= maximum:",
        "    if False:",
        SUSPENSIONS,
    ),
    Mutation(
        "RB8",
        "a bool is not a whole number: `true` in a rate field is a client bug, not one request",
        "gateway/src/aira_gateway/api/incidents/suspensions.py",
        "    if isinstance(value, bool) or not isinstance(value, int | str):",
        "    if not isinstance(value, int | str):",
        SUSPENSIONS,
    ),
    Mutation(
        "RB9",
        "an id in the path that is not a number is a 404, not a 500 out of the query builder",
        "management/backend/src/aira_management/apps/budgets/views.py",
        "            try:\n                budget = Budget.objects.filter(pk=pk, use_case__isnull=True).first()\n            except TypeError, ValueError:",
        "            if True:\n                budget = Budget.objects.filter(pk=pk, use_case__isnull=True).first()\n            elif False:",
        "management/backend/tests/test_installation_budget.py",
    ),
    Mutation(
        "RB10",
        "an amount `Decimal` accepts and money does not is refused in the documented word",
        "libs/src/aira_common/money.py",
        "    if not value.is_finite():",
        "    if False:",
        MONEY,
    ),
    Mutation(
        "RB11",
        "a usage-export cell a spreadsheet would evaluate is written as text",
        "gateway/src/aira_gateway/reporting/csv_export.py",
        '                safe_cell(row.get("key", "")),',
        '                row.get("key", ""),',
        CSV_EXPORT,
    ),
    Mutation(
        "RB12",
        "a model's own answer cannot become a formula in the evaluation somebody opens",
        "management/backend/src/aira_management/apps/smoketests/views/export.py",
        "                safe_cell(row.response),",
        "                row.response,",
        "management/backend/tests/test_smoketests.py",
    ),
    # ------------------------------------------------------------------------------------------
    # How deep a caller's body may nest (`aira_common.nesting`, 2026-09-08). The rule
    # `ensure_body_is_encodable` states about a **value** the audit row cannot hold, applied to a
    # **structure** nothing on the path can walk: every walk over a body recurses, and before this
    # the caller chose where the stack ran out. Measured — ~1 000 levels lost the audit row on a
    # refused request; the same depth inside a valid `functionResponse` answered 500 *after* the
    # model had been called; ~50 000 was a 500 out of `json.loads` on five routes and on every
    # endpoint of the control plane.
    # ------------------------------------------------------------------------------------------
    Mutation(
        "ND1",
        "a body deeper than the bound is refused at the door, not nine steps later",
        "gateway/src/aira_gateway/api/serving/body.py",
        "    if nests_deeper_than(raw, MAX_JSON_DEPTH):",
        "    if False:",
        NESTING,
    ),
    Mutation(
        "ND2",
        "the writer's clamp is wired into the payload transform, not merely defined",
        "gateway/src/aira_gateway/persistence/writer.py",
        "                bounded: dict[str, Any] = within_depth(payload)",
        "                bounded: dict[str, Any] = payload",
        NESTING,
    ),
    Mutation(
        "ND3",
        "a subtree too deep to walk is named, so a provider cannot erase the row about its answer",
        "gateway/src/aira_gateway/persistence/sanitize.py",
        "    if limit <= 0:\n        return TOO_DEEP",
        "    if False:\n        return TOO_DEEP",
        NESTING,
    ),
    Mutation(
        "ND4",
        "brackets inside a string are text, so a prompt carrying source code is not refused",
        "libs/src/aira_common/nesting.py",
        '        steps = b"".join(segments[in_string::2]).translate(_STEPS, _DROP)',
        '        steps = b"".join(segments).translate(_STEPS, _DROP)',
        "libs/tests/test_nesting.py",
    ),
    Mutation(
        "ND5",
        "an escaped backslash before a closing quote does not swallow the rest of the document",
        "libs/src/aira_common/nesting.py",
        '    cleaned = raw.replace(b"\\\\\\\\", b"").replace(b\'\\\\"\', b"")',
        '    cleaned = raw.replace(b\'\\\\"\', b"").replace(b"\\\\\\\\", b"")',
        "libs/tests/test_nesting.py",
    ),
    # ------------------------------------------------------------------------------------------
    # The second shape of catastrophic backtracking (`aira_common.patterns`, 2026-09-08): repeated
    # quantifiers **in a row** over the same characters. `a*a*a*…` and `\s*\s*\s*…` have no group
    # in them at all, so every check the nesting rule makes was skipped and the answer was False by
    # falling off the end. Measured at prompt scale — `a*a*b` costs over ten seconds against five
    # thousand characters, and this filter reads twenty thousand.
    # ------------------------------------------------------------------------------------------
    # A number a caller writes must not switch off the control that reads it (2026-09-08). The
    # reservation moves the shared counter with `HINCRBY` — 64-bit integer arithmetic — so an
    # estimate at 2⁶³ answers "increment would overflow", `RedisRunner` reports that as
    # `CountersUnavailable`, and budget enforcement fell back to its racy path for that request
    # while `/readyz` blamed the counter store. Both caller-named figures that reach it were
    # bounded *only where the model declared a bound*, and both declarations are nullable.
    # ------------------------------------------------------------------------------------------
    Mutation(
        "AT1",
        "a caller's maxOutputTokens is bounded even where the model declares no cap",
        "gateway/src/aira_gateway/api/serving/controls.py",
        "    if requested is not None and requested > MAX_ACCOUNTABLE_TOKENS:",
        "    if False:",
        ACCOUNTABLE,
    ),
    Mutation(
        "AT2",
        "a thinking budget is bounded even where the model declares no bounds",
        "gateway/src/aira_gateway/thinking.py",
        "    if tokens > MAX_ACCOUNTABLE_TOKENS:",
        "    if False:",
        ACCOUNTABLE,
    ),
    Mutation(
        "AT3",
        "an estimated total nobody wrote is clamped rather than handed to the counter",
        "gateway/src/aira_gateway/api/serving/controls.py",
        "    tokens = min(tokens, MAX_ACCOUNTABLE_TOKENS)",
        "    tokens = tokens",
        ACCOUNTABLE,
    ),
    Mutation(
        "AT4",
        "the clamp keeps the figure it is for, rather than flattening every reservation",
        "gateway/src/aira_gateway/api/serving/controls.py",
        "    tokens = min(tokens, MAX_ACCOUNTABLE_TOKENS)",
        "    tokens = min(tokens, 1)",
        ACCOUNTABLE,
    ),
    Mutation(
        "AT6",
        "the KIRA surface names the ceiling in its own vocabulary, not the shared layer's",
        "gateway/src/aira_gateway/api/kira/routes.py",
        "    if max_tokens is not None and max_tokens > MAX_ACCOUNTABLE_TOKENS:",
        "    if False:",
        ACCOUNTABLE,
    ),
    Mutation(
        "AT5",
        "the ceiling is the width of the column it is derived from",
        "gateway/src/aira_gateway/catalog.py",
        "MAX_ACCOUNTABLE_TOKENS = 2**31 - 1",
        "MAX_ACCOUNTABLE_TOKENS = 2**62",
        ACCOUNTABLE,
    ),
    # ------------------------------------------------------------------------------------------
    # The injection classifier reads its reply rather than searching it (2026-09-08). `"SAFE" in
    # "UNSAFE"` is true, and `safe`/`unsafe` is the vocabulary this field's safety classifiers use
    # — so a model normalising onto it had its verdict read as the **opposite** of what it said, on
    # a filter set to `block` and reporting that it ran. The router twenty lines below had been
    # fixed for the same substring defect a fortnight earlier.
    # ------------------------------------------------------------------------------------------
    # The injection classifier reads its reply rather than searching it (2026-09-08). `"SAFE" in
    # "UNSAFE"` is true, and `safe`/`unsafe` is the vocabulary this field's safety classifiers use
    # — so a model normalising onto it had its verdict read as the **opposite** of what it said, on
    # a filter set to `block` and reporting that it ran. The router twenty lines below had been
    # fixed for the same substring defect a fortnight earlier.
    # ------------------------------------------------------------------------------------------
    # The second shape of catastrophic backtracking (`aira_common.patterns`, 2026-09-08): repeated
    # quantifiers **in a row** over the same characters. `a*a*a*…` and `\s*\s*\s*…` have no group
    # in them at all, so every check the nesting rule makes was skipped and the answer was False by
    # falling off the end. Measured at prompt scale — `a*a*b` costs over ten seconds against five
    # thousand characters, and this filter reads twenty thousand.
    # ------------------------------------------------------------------------------------------
    # The injection classifier reads its reply rather than searching it (2026-09-08). `"SAFE" in
    # "UNSAFE"` is true, and `safe`/`unsafe` is the vocabulary this field's safety classifiers use
    # — so a model normalising onto it had its verdict read as the **opposite** of what it said, on
    # a filter set to `block` and reporting that it ran. The router twenty lines below had been
    # fixed for the same substring defect a fortnight earlier.
    # ------------------------------------------------------------------------------------------
    Mutation(
        "IV1",
        "a classifier's verdict is the word it said, not a substring of it",
        "gateway/src/aira_gateway/pipeline/classifiers/injection.py",
        '        answer = text.strip().strip("*_`\\"\'“”‘’.!?:;,-— \\t\\r\\n").upper()\n        if answer == "INJECTION":\n            return Verdict.INJECTION\n        if answer == "SAFE":\n            return Verdict.CLEAN\n        return Verdict.UNDETERMINED',
        '        answer = text.upper()\n        if "INJECTION" in answer:\n            return Verdict.INJECTION\n        if "SAFE" in answer:\n            return Verdict.CLEAN\n        return Verdict.UNDETERMINED',
        CLASSIFIERS,
    ),
    Mutation(
        "IV2",
        "a reply the classifier cannot read is undetermined, never clean",
        "gateway/src/aira_gateway/pipeline/classifiers/injection.py",
        '        if answer == "SAFE":\n            return Verdict.CLEAN\n        return Verdict.UNDETERMINED',
        '        if answer == "SAFE":\n            return Verdict.CLEAN\n        return Verdict.CLEAN',
        CLASSIFIERS,
    ),
    Mutation(
        "RR1",
        "two unbounded repeats over the same characters are refused",
        "libs/src/aira_common/patterns.py",
        "        if loop.unbounded and any(opened.loop.unbounded for opened in trading):",
        "        if False:",
        PATTERNS,
    ),
    Mutation(
        "RR2",
        "a run of bounded repeats is refused once it can divide the text too many ways",
        "libs/src/aira_common/patterns.py",
        "        if ways >= (MAX_AMBIGUITY_BESIDE_A_LOOP if beside_a_loop else MAX_AMBIGUITY):",
        "        if False:",
        PATTERNS,
    ),
    Mutation(
        "RR3",
        "an atom that can match nothing does not separate the two repeats around it",
        "libs/src/aira_common/patterns.py",
        "        return (*(self._consumed(run, loop.chars) if consumes else run), _Open(loop))",
        "        return (*self._consumed(run, loop.chars), _Open(loop))",
        PATTERNS,
    ),
    Mutation(
        "RR4",
        "an atom that must consume a character does separate them",
        "libs/src/aira_common/patterns.py",
        "            if self.overlaps(opened.loop.chars, chars)",
        "            if True",
        PATTERNS,
    ),
    Mutation(
        "RR5",
        "a bounded quantifier is told apart from one that grows with the prompt",
        "libs/src/aira_common/patterns.py",
        "    return high == int(_op.MAXREPEAT)",
        "    return False",
        PATTERNS,
    ),
    Mutation(
        "RR6",
        "a possessive quantifier cannot backtrack and is not refused for looking like one",
        "libs/src/aira_common/patterns.py",
        "        if possessive:",
        "        if False:",
        PATTERNS,
    ),
    Mutation(
        "RR7",
        "the refusal names which of the two shapes it found",
        "libs/src/aira_common/patterns.py",
        "            raise _Refused(TRADES_CHARACTERS)\n        # Bounded repeats multiply",
        "            raise _Refused(REPEATS_A_REPEAT)\n        # Bounded repeats multiply",
        PATTERNS,
    ),
    Mutation(
        "ND7",
        "a body that is valid JSON and not an object is refused before it reaches the audit trail",
        "gateway/src/aira_gateway/api/gemini/routes.py",
        "    if not isinstance(body, dict):",
        "    if False:",
        "gateway/tests/test_surfaces_record_refusals_alike.py",
    ),
    Mutation(
        "ND8",
        "a payload the writer cannot read as an object costs its shape, never the row",
        "gateway/src/aira_gateway/persistence/sanitize.py",
        "    return value if isinstance(value, dict) else {NOT_AN_OBJECT_KEY: value}",
        "    return dict(value)",
        LOG_WRITER,
    ),
    Mutation(
        "ND6",
        "the control plane parses a body through the bound too, not only the gateway",
        "management/backend/src/aira_management/apps/api/parsers.py",
        '        if nests_deeper_than(text.encode("utf-8", "surrogatepass"), MAX_JSON_DEPTH):',
        "        if False:",
        MGMT_SWEEP,
    ),
    # ------------------------------------------------------------------------------------------
    # The config file ranking above the deployment (`config/README.md`). Every one of these is a
    # way the stack could run on something nobody chose **while looking perfectly healthy** — the
    # integrator sees a stack that starts, so the only evidence that anything is wrong is a check
    # that says so. A silent verifier is worse than none: it converts an unknown into a false yes.
    # ------------------------------------------------------------------------------------------
    Mutation(
        "CV1",
        "a hand-made `.env` with no config file above it is not reported as a difference",
        "tools/config_render.py",
        "        if not marker.is_file():",
        "        if False:",
        "tools/tests/test_the_config_outranks_the_deployment.py",
    ),
    Mutation(
        "CV2",
        "a source edited without re-rendering is reported",
        "tools/config_render.py",
        "!= digest_of(source):",
        "!= digest_of(source) and False:",
        "tools/tests/test_the_config_outranks_the_deployment.py",
    ),
    Mutation(
        "CV3",
        "a variable deleted from the rendered file is reported",
        "tools/config_render.py",
        "if name not in present:",
        "if name not in present and False:",
        "tools/tests/test_the_config_outranks_the_deployment.py",
    ),
    Mutation(
        "CV4",
        "a rendered file edited by hand is reported",
        "tools/config_render.py",
        "elif present[name] != want:",
        "elif present[name] != want and False:",
        "tools/tests/test_the_config_outranks_the_deployment.py",
    ),
    Mutation(
        "CV5",
        "Docker being absent is said, not silently treated as a pass",
        "tools/config_render.py",
        '            "note: Docker is not available',
        '            "quietly nothing at all',
        "tools/tests/test_the_config_outranks_the_deployment.py",
    ),
    Mutation(
        "CV6",
        "a variable the file sets and no container receives is reported",
        "tools/config_render.py",
        "if values is None:",
        "if values is None and False:",
        "tools/tests/test_the_config_outranks_the_deployment.py",
    ),
    Mutation(
        "CV7",
        "a compose default taking over the file's value is reported",
        "tools/config_render.py",
        "elif not any(v.strip()",
        "elif False and not any(v.strip()",
        "tools/tests/test_the_config_outranks_the_deployment.py",
    ),
    Mutation(
        "CV8",
        "the two variables the deployment decides are exempt on purpose",
        "tools/config_render.py",
        "if name in COMPOSE_DECIDES:",
        "if name in ():",
        "tools/tests/test_the_config_outranks_the_deployment.py",
    ),
    Mutation(
        "CV9",
        "a difference exits non-zero, so `make up` can act on it",
        "tools/config_render.py",
        "            return 1",
        "            return 0",
        "tools/tests/test_the_config_outranks_the_deployment.py",
    ),
    Mutation(
        "CV10",
        "a missing `.env` is named rather than passing as nothing-to-check",
        "tools/config_render.py",
        "if not env_file.is_file():",
        "if False:",
        "tools/tests/test_the_config_outranks_the_deployment.py",
    ),
    Mutation(
        "CV11",
        "a source that has been deleted is named",
        "tools/config_render.py",
        "if not source.is_file():",
        "if False:",
        "tools/tests/test_the_config_outranks_the_deployment.py",
    ),
    Mutation(
        "CV12",
        "a rendered `.env` replaced by a hand-written one is refused, stamp and all",
        "tools/config_render.py",
        "        if not marker.is_file():",
        "        if marker.is_file() or True:",
        "tools/tests/test_the_config_outranks_the_deployment.py",
    ),
    Mutation(
        "CV13",
        "rendering records that this deployment is config-driven, outliving the file it renders",
        "tools/config_render.py",
        '        marker.write_text(f"{args.config}\\n", encoding="utf-8")',
        "        pass",
        "tools/tests/test_the_config_outranks_the_deployment.py",
    ),
    # ------------------------------------------------------------------------------------------
    # The showcase split. The core compose file is what somebody deploys onto their own Keycloak,
    # Vault and Postgres; every demo service left in it is a paragraph they must read to find out
    # it does not apply to them. The pull back is real — the YAML anchors live in the core file —
    # so each rule that keeps the split has a mutation.
    # ------------------------------------------------------------------------------------------
    Mutation(
        "CS1",
        "no demo-only service is defined in the core compose file",
        "deploy/compose/docker-compose.apps.yml",
        "\n  kafka-topics:\n    image: apache/kafka:3.9.0",
        "\n  vault-init:\n    image: alpine\n\n  kafka-topics:\n    image: apache/kafka:3.9.0",
        "tools/tests/test_the_core_stack_carries_no_demo.py",
    ),
    Mutation(
        "CS2",
        "the showcase file defines every service named as demo-only",
        "deploy/compose/docker-compose.showcase.yml",
        "  keycloak-init:",
        "  keycloak-init-renamed:",
        "tools/tests/test_the_core_stack_carries_no_demo.py",
    ),
    Mutation(
        "CS3",
        "no core service waits on demo provisioning, so the core stack starts alone",
        "deploy/compose/docker-compose.apps.yml",
        "      AIRA_ALLOWED_MODEL_REGIONS: ${AIRA_ALLOWED_MODEL_REGIONS:-}\n"
        "      AIRA_OTEL_ENABLED: ${AIRA_OTEL_ENABLED:-false}\n"
        "      AIRA_OTEL_ENDPOINT: ${AIRA_OTEL_ENDPOINT:-http://otel-collector:4318}\n"
        "      AIRA_LOG_JSON: ${AIRA_LOG_JSON:-true}\n"
        "      # One line per call to a system that is not ours, while one is being integrated\n"
        "      # (`FRD-617`). Empty is off. Names: otel, kafka, auth, vault, redis, postgres, all.\n"
        "      AIRA_DEBUG_INTEGRATIONS: ${AIRA_DEBUG_INTEGRATIONS:-}\n"
        "      # How many items of each OTLP batch to print as OTLP/JSON. 0 is off (`FRD-617` §3.10).\n"
        "      AIRA_DEBUG_OTEL_PAYLOAD: ${AIRA_DEBUG_OTEL_PAYLOAD:-0}\n"
        "    depends_on:\n      postgres:",
        "      AIRA_ALLOWED_MODEL_REGIONS: ${AIRA_ALLOWED_MODEL_REGIONS:-}\n"
        "      AIRA_OTEL_ENABLED: ${AIRA_OTEL_ENABLED:-false}\n"
        "      AIRA_OTEL_ENDPOINT: ${AIRA_OTEL_ENDPOINT:-http://otel-collector:4318}\n"
        "      AIRA_LOG_JSON: ${AIRA_LOG_JSON:-true}\n"
        "      # One line per call to a system that is not ours, while one is being integrated\n"
        "      # (`FRD-617`). Empty is off. Names: otel, kafka, auth, vault, redis, postgres, all.\n"
        "      AIRA_DEBUG_INTEGRATIONS: ${AIRA_DEBUG_INTEGRATIONS:-}\n"
        "      # How many items of each OTLP batch to print as OTLP/JSON. 0 is off (`FRD-617` §3.10).\n"
        "      AIRA_DEBUG_OTEL_PAYLOAD: ${AIRA_DEBUG_OTEL_PAYLOAD:-0}\n"
        "    depends_on:\n      vault-init:\n        condition: service_completed_successfully\n"
        "      postgres:",
        "tools/tests/test_the_core_stack_carries_no_demo.py",
    ),
    Mutation(
        "CS4",
        "every depends_on target in the core stack is defined by a core file",
        "deploy/compose/docker-compose.apps.yml",
        "      AIRA_ALLOWED_MODEL_REGIONS: ${AIRA_ALLOWED_MODEL_REGIONS:-}\n"
        "      AIRA_OTEL_ENABLED: ${AIRA_OTEL_ENABLED:-false}\n"
        "      AIRA_OTEL_ENDPOINT: ${AIRA_OTEL_ENDPOINT:-http://otel-collector:4318}\n"
        "      AIRA_LOG_JSON: ${AIRA_LOG_JSON:-true}\n"
        "      # One line per call to a system that is not ours, while one is being integrated\n"
        "      # (`FRD-617`). Empty is off. Names: otel, kafka, auth, vault, redis, postgres, all.\n"
        "      AIRA_DEBUG_INTEGRATIONS: ${AIRA_DEBUG_INTEGRATIONS:-}\n"
        "      # How many items of each OTLP batch to print as OTLP/JSON. 0 is off (`FRD-617` §3.10).\n"
        "      AIRA_DEBUG_OTEL_PAYLOAD: ${AIRA_DEBUG_OTEL_PAYLOAD:-0}\n"
        "    depends_on:\n      postgres:",
        "      AIRA_ALLOWED_MODEL_REGIONS: ${AIRA_ALLOWED_MODEL_REGIONS:-}\n"
        "      AIRA_OTEL_ENABLED: ${AIRA_OTEL_ENABLED:-false}\n"
        "      AIRA_OTEL_ENDPOINT: ${AIRA_OTEL_ENDPOINT:-http://otel-collector:4318}\n"
        "      AIRA_LOG_JSON: ${AIRA_LOG_JSON:-true}\n"
        "      # One line per call to a system that is not ours, while one is being integrated\n"
        "      # (`FRD-617`). Empty is off. Names: otel, kafka, auth, vault, redis, postgres, all.\n"
        "      AIRA_DEBUG_INTEGRATIONS: ${AIRA_DEBUG_INTEGRATIONS:-}\n"
        "      # How many items of each OTLP batch to print as OTLP/JSON. 0 is off (`FRD-617` §3.10).\n"
        "      AIRA_DEBUG_OTEL_PAYLOAD: ${AIRA_DEBUG_OTEL_PAYLOAD:-0}\n"
        "    depends_on:\n      nobody-defines-me:\n        condition: service_started\n"
        "      postgres:",
        "tools/tests/test_the_core_stack_carries_no_demo.py",
    ),
    Mutation(
        "CS5",
        "the showcase file adds to a core service, never restates one",
        "deploy/compose/docker-compose.showcase.yml",
        "  gateway-migrate:\n    depends_on:",
        "  gateway-migrate:\n    image: something-else\n    depends_on:",
        "tools/tests/test_the_core_stack_carries_no_demo.py",
    ),
    Mutation(
        "CS6",
        "a service is enabled only by profiles that also enable everything it depends on",
        "deploy/compose/docker-compose.apps.yml",
        "  kafka-topics:\n    image: apache/kafka:3.9.0",
        '  kafka-topics:\n    profiles: ["demo"]\n    image: apache/kafka:3.9.0',
        "tools/tests/test_showcase_is_repeatable.py",
    ),
    Mutation(
        "CS7",
        "a compose overlay that adds one edge does not replace the service it names",
        "tools/tests/test_the_stack_comes_back_whole.py",
        '        for service, body in (document.get("services") or {}).items():',
        '        merged.update(document.get("services") or {})\n'
        "        for service, body in {}.items():",
        "tools/tests/test_the_stack_comes_back_whole.py",
    ),
    Mutation(
        "CS8",
        "layering the showcase adds the dev-Vault edge without dropping the Postgres one",
        "deploy/compose/docker-compose.showcase.yml",
        "  gateway-migrate:\n    depends_on:\n      vault-init:",
        "  gateway-migrate:\n    depends_on:\n      vault-init-typo:",
        "tools/tests/test_the_core_stack_carries_no_demo.py",
    ),
    # `AIRA_STACK` is the one variable that lets a second stack run beside the first. It prefixed
    # every container name and nothing else, so both stacks joined one fixed network and every
    # service name answered for two containers. Measured: a second stack came up healthy, its own
    # database stayed empty, and its seed reported success against the first stack's Postgres.
    Mutation(
        "CS9",
        "the compose project name follows AIRA_STACK, so a second stack is its own project",
        "deploy/compose/docker-compose.yml",
        "\nname: ${AIRA_STACK:-aira}\n",
        "\nname: aira\n",
        "tools/tests/test_the_core_stack_carries_no_demo.py",
    ),
    Mutation(
        "CS10",
        "the network follows AIRA_STACK, or two stacks share one bridge and one set of names",
        "deploy/compose/docker-compose.yml",
        "\n    name: ${AIRA_STACK:-aira}\n",
        "\n    name: aira\n",
        "tools/tests/test_the_core_stack_carries_no_demo.py",
    ),
    Mutation(
        "CS11",
        "every container name follows AIRA_STACK",
        "deploy/compose/docker-compose.yml",
        "container_name: ${AIRA_STACK:-aira}-redis",
        "container_name: aira-redis",
        "tools/tests/test_the_core_stack_carries_no_demo.py",
    ),
    Mutation(
        "CS12",
        "the Makefile reads no AIRA_* name that nothing sets",
        "Makefile",
        "\t@-KEYCLOAK_URL=$(KEYCLOAK_URL) \\",
        "\t@-KEYCLOAK_URL=http://localhost:$${AIRA_KEYCLOAK_PORT:-8080} \\",
        "tools/tests/test_one_owner_for_the_stack_addresses.py",
    ),
    Mutation(
        "CS13",
        "no container is addressed by a literal name AIRA_STACK would have moved",
        "Makefile",
        "docker exec $${AIRA_STACK:-aira}-kafka /opt/kafka/bin/kafka-topics.sh",
        "docker exec aira-kafka /opt/kafka/bin/kafka-topics.sh",
        "tools/tests/test_the_core_stack_carries_no_demo.py",
    ),
    Mutation(
        "CS14",
        "the pull-wait loop names the container of the stack it is starting",
        "Makefile",
        "docker inspect -f '{{.State.Status}}' $${AIRA_STACK:-aira}-ollama-pull",
        "docker inspect -f '{{.State.Status}}' aira-ollama-pull",
        "tools/tests/test_the_core_stack_carries_no_demo.py",
    ),
    # The readiness gate, after it let sixty-one integration tests run against a catalogue that
    # held the chat model and not the embedding one. Every one of the four below is a step back to
    # a state this repository has actually been in.
    Mutation(
        "CS15",
        "the wait requires every model the demo serves, not only the first one pulled",
        "tools/demo_wait_ready.py",
        "REQUIRED_MODELS = (CHAT, EMBED)",
        "REQUIRED_MODELS = (CHAT,)",
        "tools/tests/test_the_demo_asks_only_what_it_allows.py",
    ),
    Mutation(
        "CS16",
        "the wait checks each required model, rather than declaring the list and asking about one",
        "tools/demo_wait_ready.py",
        "return True, tuple(name for name in REQUIRED_MODELS if name not in declared)",
        "return True, () if CHAT in declared else (CHAT,)",
        "tools/tests/test_the_demo_asks_only_what_it_allows.py",
    ),
    Mutation(
        "CS17",
        "the integration job waits for the catalogue and not only for four endpoints",
        ".github/workflows/ci.yml",
        "        run: make demo-ready\n\n      - name: Integration tests",
        "        run: make wait-healthy\n\n      - name: Integration tests",
        "tools/tests/test_showcase_is_repeatable.py",
    ),
    Mutation(
        "CS17b",
        "the browser job waits for the catalogue too: it starts a stack of its own",
        ".github/workflows/ci.yml",
        "        run: make demo-ready\n\n      - name: Install Playwright browser",
        "        run: make wait-healthy\n\n      - name: Install Playwright browser",
        "tools/tests/test_showcase_is_repeatable.py",
    ),
    Mutation(
        "CS19",
        "a pull that has not started yet counts as unfinished, not as finished",
        "Makefile",
        'case "$$s" in running|created) ;; *) break ;; esac;',
        '[ "$$s" = "running" ] || break;',
        "tools/tests/test_showcase_is_repeatable.py",
    ),
    Mutation(
        "CS18",
        "the pull-wait sequence is written once, so no caller can hold a stale copy of it",
        "Makefile",
        "\t@$(MAKE) --no-print-directory demo-ready\n",
        "\t@for i in $$(seq 1 300); do \\\n"
        "\t\ts=$$(docker inspect -f '{{.State.Status}}'"
        " $${AIRA_STACK:-aira}-ollama-pull 2>/dev/null || echo gone); \\\n"
        '\t\t[ "$$s" = "running" ] || break; \\\n'
        "\t\tsleep 3; \\\n"
        "\tdone\n",
        "tools/tests/test_showcase_is_repeatable.py",
    ),
    # `make config-check` — asked before a deployment, so that a hardening refusal is met at a
    # desk instead of in a maintenance window. Each of these makes it answer more permissively
    # than the service it stands in for, which is the failure mode of every pre-flight check.
    Mutation(
        "CC1",
        "a credential the config file cannot carry is not counted against the file",
        "tools/config_check.py",
        "theirs = [p for p in problems if p not in from_vault]",
        "theirs = list(problems)",
        "tools/tests/test_a_config_is_checked_before_it_is_deployed.py",
    ),
    Mutation(
        "CC2",
        "a Vault that is declared and unusable is reported, not treated as an answer",
        "tools/config_check.py",
        "if vault_refused:",
        "if False:",
        "tools/tests/test_a_config_is_checked_before_it_is_deployed.py",
    ),
    Mutation(
        "CC3",
        "the planes are asked in the rendered environment and no other",
        "tools/config_check.py",
        'env={**environment, "PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},',
        "env=None,",
        "tools/tests/test_a_config_is_checked_before_it_is_deployed.py",
    ),
    Mutation(
        "CC4",
        "both planes are asked, because half a configuration passes half a check",
        "tools/config_check.py",
        "for plane in sorted(PLANES):",
        'for plane in ["gateway"]:',
        "tools/tests/test_a_config_is_checked_before_it_is_deployed.py",
    ),
    Mutation(
        "CC5",
        "a problem the file owns sets the exit code",
        "tools/config_check.py",
        "return (1 if own_problems else 0), lines",
        "return 0, lines",
        "tools/tests/test_a_config_is_checked_before_it_is_deployed.py",
    ),
    Mutation(
        "CC6",
        "a config file that does not render is its own answer",
        "tools/config_check.py",
        'return 2, [f"error: {exc}"]',
        "return 0, []",
        "tools/tests/test_a_config_is_checked_before_it_is_deployed.py",
    ),
    # ---- the console asks the server's question, in the server's words (2026-08-27) ------
    Mutation(
        "CR1",
        "a console permission list that drifts from the server's is caught",
        "management/frontend/src/app/core/auth/roles.ts",
        "  'usecase.read_retired',\n  'usecase.purge',\n",
        "  'usecase.read_retired',\n",
        CONSOLE_ROLES,
    ),
    Mutation(
        "CR2",
        "a permission the server has never heard of is caught",
        "management/frontend/src/app/core/auth/roles.ts",
        "  'incident.suspend',\n",
        "  'incident.suspended',\n",
        CONSOLE_ROLES,
    ),
    Mutation(
        "CR3",
        "a component that decides authority from a role slug of its own is caught",
        "management/frontend/src/app/features/models/model-catalog.ts",
        "  protected readonly canEdit = computed(() => can(this.me(), 'catalog.write'));",
        "  protected readonly canEdit = computed(() => this.me()?.roles.includes('global-admin') ?? false);",
        CONSOLE_ROLES,
    ),
    Mutation(
        "CR4",
        "a role literal in a template is caught as well as one in a component",
        "management/frontend/src/app/app.html",
        "  @if (may('report.read_all')) {",
        "  @if (may('report.read_all') || me()?.roles?.includes('it-security')) {",
        CONSOLE_ROLES,
    ),
    Mutation(
        "CR5",
        "a role set restated in roles.ts is caught",
        "management/frontend/src/app/core/auth/roles.ts",
        "export const PERMISSIONS = [",
        "const INCIDENT_ROLES = ['it-security', 'global-admin'];\nexport const PERMISSIONS = [",
        CONSOLE_ROLES,
    ),
    # ---- an identity that crosses a boundary is set once (2026-08-27) --------------------
    Mutation(
        "PID1",
        "a use case's slug cannot be edited after it is created",
        "management/backend/src/aira_management/apps/usecases/serializers.py",
        "        if self.instance is not None and slug != self.instance.slug:",
        "        if False:",
        IDENTITY,
    ),
    Mutation(
        "PID2",
        "a slug sent unchanged is not a rename, so a full-document update still works",
        "management/backend/src/aira_management/apps/usecases/serializers.py",
        "        if self.instance is not None and slug != self.instance.slug:",
        "        if self.instance is not None:",
        IDENTITY,
    ),
    Mutation(
        "PID3",
        "a catalogued model's name cannot be edited after it is created",
        "management/backend/src/aira_management/apps/catalog/serializers.py",
        "        if self.instance is not None and name != self.instance.name:",
        "        if False:",
        IDENTITY,
    ),
    Mutation(
        "PID4",
        "the upsert by name is not read as a rename",
        "management/backend/src/aira_management/apps/catalog/serializers.py",
        "        if self.instance is not None and name != self.instance.name:",
        "        if self.instance is not None:",
        IDENTITY,
    ),
    # ---- a redactor reaches an embedding, and a failed one keeps nothing (2026-08-27) ----
    Mutation(
        "PE1",
        "an embedding runs the steps that are about the text it carries",
        "gateway/src/aira_gateway/api/serving/prepare.py",
        "        embed = await run_pipeline_over_texts(request, embed, trail)",
        "        pass",
        EMBED_PII,
    ),
    Mutation(
        "Z6b",
        "a model call the **embedding** pipeline made leaves its own audit row",
        "gateway/src/aira_gateway/api/serving/pipeline_run.py",
        "    finally:\n"
        "        await record_pipeline_calls(request, trail)\n"
        "        _keep_only_what_a_redactor_allows(trail, rewrites)\n"
        "    if outcome.decisions:",
        "    finally:\n"
        "        _keep_only_what_a_redactor_allows(trail, rewrites)\n"
        "    if outcome.decisions:",
        EMBED_PII,
    ),
    Mutation(
        "PE2",
        "a step about an answer does not run on an embedding",
        "gateway/src/aira_gateway/pipeline/engine/runner.py",
        "            if step.type not in TEXT_ONLY_STEPS:\n                continue",
        "            if False:\n                continue",
        EMBED_PII,
    ),
    Mutation(
        "PE3",
        "every text of a batch is offered to the redactor, in order",
        "gateway/src/aira_gateway/pipeline/engine/runner.py",
        "        return list(await asyncio.gather(*(one(text) for text in texts)))",
        "        return [await one(texts[0])] * len(texts) if texts else []",
        EMBED_PII,
    ),
    Mutation(
        "PE4",
        "one text that cannot be redacted refuses the whole batch",
        "gateway/src/aira_gateway/pipeline/engine/runner.py",
        "            if notable is not None and notable.block_reason is not None:\n"
        "                raise PipelineRejected(notable.block_reason)",
        "            if False:\n                raise PipelineRejected(str(notable))",
        EMBED_PII,
    ),
    Mutation(
        "PE5",
        "a redaction that failed leaves no payload behind",
        "gateway/src/aira_gateway/api/serving/pipeline_run.py",
        "    if redaction_failed(trail.decisions):\n        trail.body = None",
        "    if False:\n        trail.body = None",
        EMBED_PII + " " + PII,
    ),
    Mutation(
        "PE6",
        "serving anyway under on_failure=allow is not storing anyway",
        "gateway/src/aira_gateway/audit.py",
        '        decision.get("step") == "pii_filter" '
        'and str(decision.get("action")) not in APPLIED_ACTIONS',
        '        decision.get("step") == "pii_filter" and str(decision.get("action")) == "blocked"',
        EMBED_PII,
    ),
    Mutation(
        "PE7",
        "a batch reports the least good of its texts, not the first",
        "gateway/src/aira_gateway/pipeline/engine/texts.py",
        "    return next((e for e in evaluations if e.action not in APPLIED_ACTIONS), evaluations[0])",
        "    return evaluations[0]",
        EMBED_PII,
    ),
    Mutation(
        "PE8",
        "the decision says how much of the batch the step changed",
        "gateway/src/aira_gateway/pipeline/engine/texts.py",
        '        "texts": len(evaluations),\n        "changed": changed,',
        '        "texts": len(evaluations),\n        "changed": 0,',
        EMBED_PII,
    ),
    Mutation(
        "PE9",
        "a batch is billed for every call it made, not for one of them",
        "gateway/src/aira_gateway/pipeline/engine/texts.py",
        "        prompt_tokens=sum(call.usage.prompt_tokens for call in calls),",
        "        prompt_tokens=calls[0].usage.prompt_tokens,",
        EMBED_PII,
    ),
    # ---- a router reads its classifier's reply rather than searching it (2026-08-27) -----
    Mutation(
        "PR1",
        "a category is named by a whole word, not found anywhere inside the reply",
        "gateway/src/aira_gateway/pipeline/classifiers/routing.py",
        'if name and re.search(rf"\\b{re.escape(name.upper())}\\b", answer)',
        "if name and name.upper() in answer",
        CLASSIFIERS,
    ),
    Mutation(
        "PR2",
        "a reply naming two categories has named none",
        "gateway/src/aira_gateway/pipeline/classifiers/routing.py",
        "        return found[0] if len(found) == 1 else None",
        "        return found[0] if found else None",
        CLASSIFIERS,
    ),
    Mutation(
        "PR3",
        "the exact answer the instruction asks for is taken before anything cleverer",
        "gateway/src/aira_gateway/pipeline/classifiers/routing.py",
        "            if name and answer == name.upper():\n                return name",
        "            if False:\n                return name",
        CLASSIFIERS,
    ),
    # ---- a pipeline step reaches only the model somebody named (2026-08-27) --------------
    Mutation(
        "PD1",
        "a redactor that names no model blocks rather than borrowing the first in the registry",
        "gateway/src/aira_gateway/pipeline/engine/steps.py",
        "        candidates = await self._models_for(config, declaration_of, permits)\n"
        "        if not candidates.found:\n            why = candidates.why_none(",
        "        candidates = await self._models_for(config, declaration_of, permits)\n"
        "        if not candidates.found:\n"
        "            candidates.found.append(\n"
        "                (self._registry.models()[0].name, self._registry.provider_for(self._registry.models()[0].name))\n"
        "            )\n"
        "        if not candidates.found:\n            why = candidates.why_none(",
        PII,
    ),
    Mutation(
        "PD2",
        "an LLM filter that names no model falls back to the heuristic, not to any model at hand",
        "gateway/src/aira_gateway/pipeline/engine/steps.py",
        '        if config.get("mode") == "llm":\n\n            async def ask',
        '        if config.get("mode") == "llm":\n'
        '            config = {**config, "model": config.get("model") or self._registry.models()[0].name}\n'
        "\n            async def ask",
        CLASSIFIERS,
    ),
    Mutation(
        "PD6",
        "a router that names no classifier is not asked, rather than asking any model at hand",
        "gateway/src/aira_gateway/pipeline/engine/steps.py",
        "        candidates = await self._models_for(config, declaration_of, permits)\n"
        "        answered = await self._asked(candidates.found, ask)\n        if answered is None:",
        '        config = {**config, "model": config.get("model") or self._registry.models()[0].name}\n'
        "        candidates = await self._models_for(config, declaration_of, permits)\n"
        "        answered = await self._asked(candidates.found, ask)\n        if answered is None:",
        PIPELINE,
    ),
    Mutation(
        "PD3",
        "the dry run asks the installation's approval, which has no third state",
        "gateway/src/aira_gateway/api/pipeline.py",
        "    for name in classifiers_named_in(payload.pipeline):",
        "    for name in []:",
        DRYRUN,
    ),
    Mutation(
        "PD5",
        "a router's classifier is one of the models the dry run checks",
        "gateway/src/aira_gateway/api/pipeline.py",
        '    "model_route": lambda config: bool(config.get("categories")),',
        '    "model_route": lambda config: False,',
        DRYRUN,
    ),
    Mutation(
        "PD4",
        "a redactor's model is one of the classifiers the dry run checks",
        "gateway/src/aira_gateway/api/pipeline.py",
        '    "pii_filter": lambda config: True,',
        '    "pii_filter": lambda config: False,',
        DRYRUN,
    ),
    # ---- one person, one identity (`FRD-613`) --------------------------------------------------
    #
    # Eight properties from the round that swept every reader of a caller's identity. Each is a
    # defect that was live on 2026-08-30, expressed as the one-line edit that puts it back — which
    # is what this file is for: *"when you fix a bug, add the mutation that reintroduces it."*
    Mutation(
        "BUS5",
        "the span says how many tools were offered and how many were used",
        "gateway/src/aira_gateway/persistence/recorder.py",
        "            **_tool_attributes(tool_calls),",
        "            # tool figures dropped",
        "gateway/tests/test_the_trace_says_how_many_tools.py",
    ),
    Mutation(
        "BUS6",
        "what a request offered is recorded even when it is then refused",
        "gateway/src/aira_gateway/persistence/recorder.py",
        '        "aira.tools.offered": int(tool_calls.get("declared") or 0),',
        '        "aira.tools.offered": len(called),',
        "gateway/tests/test_the_trace_says_how_many_tools.py",
    ),
    Mutation(
        "ONE1",
        "a suspension naming a person stops them whichever credential they hold",
        "gateway/src/aira_gateway/anomalies/suspensions.py",
        "        return row.target_value in {name for name in (subject, person) if name}",
        "        return subject is not None and row.target_value == subject",
        IDENTITY_SWEEP,
    ),
    Mutation(
        "ONE2",
        "the kill switch is handed the person as well as the subject",
        "gateway/src/aira_gateway/api/serving/controls.py",
        "    throttles = await suspensions.check(use_case, subject, credential, caller)",
        "    throttles = await suspensions.check(use_case, subject, credential)",
        IDENTITY_SWEEP,
    ),
    Mutation(
        "ONE3",
        "a rule about a person measures both of their credentials together",
        "gateway/src/aira_gateway/anomalies/evaluator.py",
        "    RuleTarget.SUBJECT: _PERSON,",
        "    RuleTarget.SUBJECT: RequestLog.subject,",
        IDENTITY_SWEEP,
    ),
    Mutation(
        "ONE4",
        "the trace query finds a person's rows in either alphabet",
        "gateway/src/aira_gateway/payloads.py",
        "    if person:\n        # Written as indexable disjuncts",
        "    if person and person != principal.subject:\n        # Written as indexable disjuncts",
        IDENTITY_SWEEP,
    ),
    Mutation(
        "ONE5",
        "a group claim carrying something that is not a path confers nothing rather than a 500",
        "libs/src/aira_common/access.py",
        "        if not isinstance(group, str):",
        "        if False:",
        "libs/tests/test_access.py",
    ),
    Mutation(
        "ONE6",
        "a selector is anchored at the end of the string, not before a trailing newline",
        "gateway/src/aira_gateway/auth/attribution.py",
        '_SLUG = re.compile(r"^[a-z0-9-]{1,64}\\Z")',
        '_SLUG = re.compile(r"^[a-z0-9-]{1,64}$")',
        IDENTITY_SWEEP,
    ),
    Mutation(
        "ONE7",
        "ending somebody's access revokes the keys that rested on it",
        "management/backend/src/aira_management/apps/usecases/views/grants.py",
        "        if holds_a_grant(key.owner, usecase):\n            continue",
        "        if True:\n            continue",
        ACCESS_LIFECYCLE,
    ),
    Mutation(
        "ONE8",
        "naming somebody else as a key's owner is an administrator's act",
        "management/backend/src/aira_management/apps/usecases/views/api_keys.py",
        "        if not may_manage(caller, usecase):",
        "        if False:",
        ACCESS_LIFECYCLE,
    ),
    Mutation(
        "ONE9",
        "the seed leaves nobody a superuser, so authority stays the directory's answer",
        "management/backend/src/aira_management/apps/seed/contributions/roles_and_users.py",
        "            user.is_superuser = False",
        "            user.is_superuser = user.is_superuser",
        "management/backend/tests/test_seed.py",
    ),
    # ---- a trace crosses the bus (`FRD-615`) ---------------------------------------------------
    Mutation(
        "BUS1",
        "the consumer continues the trace the message came from",
        "gateway/src/aira_gateway/consumer/worker.py",
        "        message.headers,\n        {",
        "        None,  # context dropped\n        {",
        TRACE_BUS,
    ),
    Mutation(
        "BUS2",
        "the context of the causing request is stored on the outbox row",
        "management/backend/src/aira_management/apps/outbox/subscriber.py",
        "        traceparent=traceparent_from_context(),",
        '        traceparent="",',
        "management/backend/tests/test_outbox.py",
    ),
    Mutation(
        "BUS3",
        "the stored context is what the message is published under, not the publisher's",
        "libs/src/aira_common/kafka.py",
        "        headers.extend(kafka_headers_for(record.traceparent))",
        "        headers.extend(kafka_headers_for())",
        TRACE_BUS,
    ),
    Mutation(
        "BUS4",
        "an event that could not be applied is red in the trace, not only in the log",
        "gateway/src/aira_gateway/consumer/worker.py",
        "            processing.failed(exc)",
        "            pass  # failure left off the span",
        TRACE_BUS,
    ),
    Mutation(
        "ONE10",
        "a claim too long for its column does not become a 500 on a first sign-in",
        "management/backend/src/aira_management/apps/api/authentication.py",
        "    if text and len(text) <= USERNAME_MAX_LENGTH:",
        "    if text:",
        "management/backend/tests/test_an_identity_is_claimed_once.py",
    ),
    # ── FRD-117 FR-5 and FRD-001 FR-6, both specified years before they were built ────────────
    Mutation(
        "OT1",
        "an upstream call and a database read are spans of their own",
        "gateway/src/aira_gateway/app.py",
        "        instrument_outgoing_calls(engine)",
        "        pass  # outgoing calls intentionally untraced",
        "gateway/tests/test_outgoing_calls_are_traced.py",
    ),
    Mutation(
        "OT2",
        "this installation's upstream key never reaches a client span",
        "gateway/src/aira_gateway/telemetry.py",
        "            async_request_hook=prepare_client_span_async,",
        "            async_request_hook=None,",
        "gateway/tests/test_outgoing_calls_are_traced.py",
    ),
    Mutation(
        "OT3",
        "the application's own log lines are exported, not only printed",
        "libs/src/aira_common/logging.py",
        "        processors=[*processors, renderer, forward_to_stdlib],",
        "        processors=[*processors, renderer],",
        "libs/tests/test_app_logs_are_exported.py",
    ),
    Mutation(
        "OT4",
        "a span says what the request carried — parts, attachment bytes, schema, batch",
        "gateway/src/aira_gateway/api/serving/prepare.py",
        "    describe_on_the_span(canonical, embed)",
        "    pass  # request shape intentionally left off the span",
        "gateway/tests/test_the_span_says_what_the_request_was.py",
    ),
    Mutation(
        "OT6",
        "every path that attributes a request says so on the span, not only on the row",
        "gateway/src/aira_gateway/auth/attribution.py",
        """    request.state.attribution = attribution
    identity = {""",
        """    request.state.attribution = attribution
    _unused = {""",
        "gateway/tests/test_every_attribution_reaches_the_span.py",
    ),
    # `FRD-619` — a model access that says who it was for. Measured on 2026-09-04 against a live
    # delivery feed: the model call carried three HTTP attributes and nothing else, while the
    # request span above it carried twenty-nine. A tracing backend joins the two; a SIEM does not.
    Mutation(
        "MC1",
        "a model call names the model, which an OpenAI-compatible URL never does",
        "gateway/src/aira_gateway/telemetry.py",
        '    with model_call({"aira.model": model, "aira.model_call.purpose": purpose}):',
        '    with model_call({"aira.model_call.purpose": purpose}):',
        "gateway/tests/test_outgoing_calls_are_traced.py",
    ),
    Mutation(
        "MC2",
        "the caller's identity reaches the span of the call, not only the request above it",
        "gateway/src/aira_gateway/telemetry.py",
        """    for key, value in carried:
        span.set_attribute(key, value)""",
        """    for _key, _value in carried:
        pass""",
        "gateway/tests/test_outgoing_calls_are_traced.py",
    ),
    Mutation(
        "MC3",
        "a call the gateway makes for itself is not labelled with a caller who did not cause it",
        "libs/src/aira_common/observability/spans.py",
        """    if call is None:
        return None
    return (*_caller.get(), *call)""",
        """    return (*_caller.get(), *(call or ()))""",
        "gateway/tests/test_outgoing_calls_are_traced.py",
    ),
    Mutation(
        "MC4",
        "the mark ends with the model call, so the next outgoing request is not one",
        "libs/src/aira_common/observability/spans.py",
        """    token = _model_call.set(_primitives(attributes))
    try:
        yield
    finally:
        _model_call.reset(token)""",
        """    _model_call.set(_primitives(attributes))
    yield""",
        "gateway/tests/test_outgoing_calls_are_traced.py",
    ),
    Mutation(
        "MC5",
        "who is calling is carried from the one place that knows it",
        "gateway/src/aira_gateway/auth/attribution.py",
        "    attribute_model_calls_to(identity)",
        "    pass  # identity not carried to the model call",
        "gateway/tests/test_every_attribution_reaches_the_span.py",
    ),
    Mutation(
        "MC6",
        "a fallback chain marks each attempt, so three tried models leave three named records",
        "gateway/src/aira_gateway/pipeline/dispatch.py",
        '            with model_call_span(model, purpose="serve"):\n                response',
        '            with model_call_span(request.model, purpose="serve"):\n                response',
        "gateway/tests/test_pipeline_dispatch.py",
    ),
    Mutation(
        "MC7",
        "an unmarked upstream call is a test failure rather than a silent anonymous record",
        "gateway/tests/test_every_model_call_says_what_it_is.py",
        '        if INVOCATION.search(code) and "def " not in code:',
        "        if False:",
        "gateway/tests/test_every_model_call_says_what_it_is.py",
    ),
    Mutation(
        "MC8",
        "a stream is marked for as long as it streams, not only while it is built",
        "gateway/src/aira_gateway/pipeline/dispatch.py",
        '            with model_call_span(model, purpose="serve"):\n                chunks = provider.stream_generate(addressed)\n                first = await anext(chunks)',
        "            if True:\n                chunks = provider.stream_generate(addressed)\n                first = await anext(chunks)",
        "gateway/tests/test_outgoing_calls_are_traced.py",
    ),
    Mutation(
        "OT5",
        "a span says which surface the request came in on",
        "gateway/src/aira_gateway/persistence/recorder.py",
        '            "aira.api.surface": api,',
        "            # surface intentionally left off the span",
        "gateway/tests/test_the_span_says_what_the_request_was.py",
    ),
    # --- FRD-617: what a call to another system says about itself ---------------------------
    Mutation(
        "ID1",
        "a misspelled system name refuses the process rather than watching nothing",
        "libs/src/aira_common/integration_debug.py",
        "        raise UnknownIntegration(",
        "        return frozenset(wanted)\n        raise UnknownIntegration(",
        "libs/tests/test_integration_debug.py",
    ),
    Mutation(
        "ID2",
        "a call that failed is reported as failed, not as a slow success",
        "libs/src/aira_common/integration_debug.py",
        '        collected["outcome"] = "timeout" if _is_timeout(exc) else "failed"',
        '        collected["outcome"] = "ok"',
        "libs/tests/test_integration_debug.py",
    ),
    Mutation(
        "ID3",
        "a timeout is told apart from a refusal",
        "libs/src/aira_common/integration_debug.py",
        '        if isinstance(seen, TimeoutError) or "Timeout" in type(seen).__name__:',
        "        if False:",
        "libs/tests/test_integration_debug.py",
    ),
    Mutation(
        "ID16",
        "a timeout a client wrapped in its own type is still reported as a timeout",
        "libs/src/aira_common/integration_debug.py",
        "        seen = seen.__cause__  # type: ignore[assignment]",
        "        seen = None  # type: ignore[assignment]",
        "libs/tests/test_integration_debug.py",
    ),
    Mutation(
        "ID4",
        "a credential in an address never reaches a debug line",
        "libs/src/aira_common/integration_debug.py",
        "        key: (redact_target(value) if key in _URL_FIELDS and isinstance(value, str) else value)",
        "        key: value",
        "libs/tests/test_integration_debug.py libs/tests/test_the_state_stores_say_what_they_answered.py",
    ),
    Mutation(
        "ID5",
        "a password in a URL's authority is redacted, not only one in its query",
        "libs/src/aira_common/observability/redaction.py",
        '    return f"{scheme}{marker}{user}:{REDACTED}@{host}{slash}{path}"',
        "    return value",
        "libs/tests/test_integration_debug.py gateway/tests/test_the_channel_is_wired_to_what_it_names.py",
    ),
    Mutation(
        "ID6",
        "off is off: nothing is written for a system nobody is watching",
        "libs/src/aira_common/integration_debug.py",
        "    if system not in _watched:\n        return\n    try:",
        "    if not _watched:\n        return\n    try:",
        "libs/tests/test_integration_debug.py",
    ),
    Mutation(
        "ID14",
        "off is off: a watched call site yields the shared no-op for a system nobody watches",
        "libs/src/aira_common/integration_debug.py",
        "    if system not in _watched:\n        yield _IGNORED",
        "    if not _watched:\n        yield _IGNORED",
        "libs/tests/test_integration_debug.py",
    ),
    Mutation(
        "OT7",
        "an OTLP export that answers FAILURE is reported as a failure",
        "libs/src/aira_common/observability/export.py",
        '                call.failed(f"exporter returned {name}")',
        "                pass",
        "libs/tests/test_a_line_about_otel_does_not_travel_by_otel.py",
    ),
    Mutation(
        "ID35",
        "forgetting the endpoint does not put the collector in a restart loop",
        "deploy/compose/docker-compose.yml",
        "      AIRA_OTEL_FORWARD_ENDPOINT: ${AIRA_OTEL_FORWARD_ENDPOINT:-http://forward-endpoint-not-set.invalid:4318}",
        "      AIRA_OTEL_FORWARD_ENDPOINT: ${AIRA_OTEL_FORWARD_ENDPOINT:-}",
        "tools/tests/test_the_siem_gets_requests_not_plumbing.py",
    ),
    Mutation(
        "ID36",
        "the second destination is off unless a variable selects it",
        "deploy/compose/docker-compose.yml",
        "      - --config=${AIRA_OTEL_FORWARD_CONFIG:-/etc/otelcol-contrib/empty.yaml}",
        "      - --config=${AIRA_OTEL_FORWARD_CONFIG:-/etc/otelcol-contrib/forward.yaml}",
        "tools/tests/test_the_siem_gets_requests_not_plumbing.py",
    ),
    Mutation(
        "ID33",
        "the second destination gets requests, not every database span",
        "deploy/compose/otel/collector-forward.yaml",
        "      processors: [memory_limiter, resource, filter/siem, batch/siem]",
        "      processors: [memory_limiter, resource, batch/siem]",
        "tools/tests/test_the_siem_gets_requests_not_plumbing.py",
    ),
    Mutation(
        "ID34",
        "delivery has its own batching, so its tuning cannot retime observability",
        "deploy/compose/otel/collector-forward.yaml",
        "  batch/siem:\n    timeout: ${env:AIRA_OTEL_FORWARD_BATCH_SECONDS}",
        "  batch:\n    timeout: ${env:AIRA_OTEL_FORWARD_BATCH_SECONDS}",
        "tools/tests/test_the_siem_gets_requests_not_plumbing.py",
    ),
    Mutation(
        "ID46",
        "the delivery fragment never reaches into the observability stream's pipelines",
        "deploy/compose/otel/collector-forward.yaml",
        "    traces/siem:\n      receivers: [otlp]\n      processors: [memory_limiter, resource, filter/siem, batch/siem]",
        "    traces:\n      exporters: [otlp/backend, debug, file/arrived, otlphttp/forward]\n    traces/siem:\n      receivers: [otlp]\n      processors: [memory_limiter, resource, filter/siem, batch/siem]",
        "tools/tests/test_the_siem_gets_requests_not_plumbing.py",
    ),
    Mutation(
        "ID47",
        "metrics and logs reach the second destination on pipelines of their own",
        "deploy/compose/otel/collector-forward.yaml",
        "    logs/siem:\n      receivers: [otlp]\n      processors: [memory_limiter, resource, batch/siem]\n      exporters: [otlphttp/forward]",
        "    logs:\n      exporters: [otlp/backend, debug, file/arrived, otlphttp/forward]",
        "tools/tests/test_the_siem_gets_requests_not_plumbing.py",
    ),
    Mutation(
        "ID37",
        "the forwarding leg sends no Authorization header until an auth fragment says so",
        "deploy/compose/otel/collector-forward.yaml",
        "    tls:\n      # For a first reachability test, not for a deployment.",
        "    headers:\n      authorization: ${env:AIRA_OTEL_FORWARD_AUTHORIZATION}\n    tls:\n      # For a first reachability test, not for a deployment.",
        "tools/tests/test_the_siem_gets_requests_not_plumbing.py",
    ),
    Mutation(
        "ID38",
        "how the leg authenticates is off until a variable selects it",
        "deploy/compose/docker-compose.yml",
        "      - --config=${AIRA_OTEL_FORWARD_AUTH_CONFIG:-/etc/otelcol-contrib/empty.yaml}",
        "      - --config=${AIRA_OTEL_FORWARD_AUTH_CONFIG:-/etc/otelcol-contrib/forward-auth-header.yaml}",
        "tools/tests/test_the_siem_gets_requests_not_plumbing.py",
    ),
    Mutation(
        "ID39",
        "the encoding is a variable, so a receiver that refuses JSON is reachable",
        "deploy/compose/otel/collector-forward.yaml",
        "    encoding: ${env:AIRA_OTEL_FORWARD_ENCODING:-json}",
        "    encoding: json",
        "tools/tests/test_the_siem_gets_requests_not_plumbing.py",
    ),
    Mutation(
        "ID40",
        "the SIEM filter survives the semantic conventions renaming http.url",
        "deploy/compose/otel/collector-forward.yaml",
        '(attributes["http.url"] == nil and attributes["url.full"] == nil)',
        '(attributes["http.url"] == nil)',
        "tools/tests/test_the_siem_gets_requests_not_plumbing.py",
    ),
    Mutation(
        "ID43",
        "a header name goes through the extension, not into a key the collector never substitutes",
        "deploy/compose/otel/collector-forward-auth-header.yaml",
        "  otlphttp/forward:\n    auth:\n      authenticator: headers_setter",
        "  otlphttp/forward:\n    headers:\n      ${env:AIRA_OTEL_FORWARD_AUTH_HEADER:-authorization}: ${env:AIRA_OTEL_FORWARD_AUTHORIZATION}",
        "tools/tests/test_the_siem_gets_requests_not_plumbing.py",
    ),
    Mutation(
        "ID44",
        "both OTLP transports are defined, so a credential fragment cannot create a bare exporter",
        "deploy/compose/otel/collector-forward.yaml",
        "    endpoint: ${env:AIRA_OTEL_FORWARD_GRPC_ENDPOINT:-forward-endpoint-not-set.invalid:4317}",
        "    endpoint: ",
        "tools/tests/test_the_siem_gets_requests_not_plumbing.py",
    ),
    Mutation(
        "ID45",
        "switching transport moves every pipeline, not some of them",
        "deploy/compose/otel/collector-forward-grpc.yaml",
        "    logs/siem:\n      exporters: [otlp/forward]",
        "    logs/siem:\n      exporters: [otlphttp/forward]",
        "tools/tests/test_the_siem_gets_requests_not_plumbing.py",
    ),
    # --- FRD-620: the two channels are configured the same way ---------------------------------
    Mutation(
        "CHAN1",
        "the two channels take the same settings, held side by side",
        "deploy/compose/docker-compose.yml",
        "      AIRA_OTEL_BACKEND_ENCODING: ${AIRA_OTEL_BACKEND_ENCODING:-json}",
        "",
        "tools/tests/test_both_channels_are_configured_the_same_way.py",
    ),
    Mutation(
        "CHAN2",
        "the observability channel can choose its encoding at all",
        "deploy/compose/otel/collector-config.yaml",
        "    encoding: ${env:AIRA_OTEL_BACKEND_ENCODING:-json}",
        "    encoding: json",
        "tools/tests/test_both_channels_are_configured_the_same_way.py",
    ),
    Mutation(
        "CHAN3",
        "no fragment owns `service::extensions`, which replaces on merge",
        "deploy/compose/otel/collector-backend-auth-header.yaml",
        "exporters:\n  otlp/backend:",
        "service:\n  extensions: [headers_setter/backend]\n\nexporters:\n  otlp/backend:",
        "tools/tests/test_both_channels_are_configured_the_same_way.py",
    ),
    Mutation(
        "CHAN5",
        "switching the observability channel off outranks its transport fragment",
        "deploy/compose/docker-compose.yml",
        "      - --config=${AIRA_OTEL_BACKEND_PROTOCOL_CONFIG:-/etc/otelcol-contrib/empty.yaml}\n"
        "      - --config=${AIRA_OTEL_BACKEND_AUTH_CONFIG:-/etc/otelcol-contrib/empty.yaml}\n"
        "      - --config=${AIRA_OTEL_BACKEND_CONFIG:-/etc/otelcol-contrib/empty.yaml}",
        "      - --config=${AIRA_OTEL_BACKEND_CONFIG:-/etc/otelcol-contrib/empty.yaml}\n"
        "      - --config=${AIRA_OTEL_BACKEND_PROTOCOL_CONFIG:-/etc/otelcol-contrib/empty.yaml}\n"
        "      - --config=${AIRA_OTEL_BACKEND_AUTH_CONFIG:-/etc/otelcol-contrib/empty.yaml}",
        "tools/tests/test_both_channels_are_configured_the_same_way.py",
    ),
    Mutation(
        "CHAN6",
        "a credential fallback is spelled where an empty string cannot override it",
        "deploy/compose/docker-compose.yml",
        "      AIRA_OTEL_BACKEND_AUTHORIZATION: ${AIRA_OTEL_BACKEND_AUTHORIZATION:-AIRA_OTEL_BACKEND_AUTHORIZATION-is-not-set}",
        "      AIRA_OTEL_BACKEND_AUTHORIZATION: ${AIRA_OTEL_BACKEND_AUTHORIZATION:-}",
        "tools/tests/test_both_channels_are_configured_the_same_way.py",
    ),
    Mutation(
        "CHAN7",
        "the transport fragment moves the pipelines and keeps the arrivals exporters",
        "deploy/compose/otel/collector-backend-http.yaml",
        "    logs:\n      exporters: [otlphttp/backend, debug, file/arrived]",
        "    logs:\n      exporters: [otlphttp/backend]",
        "tools/tests/test_both_channels_are_configured_the_same_way.py",
    ),
    Mutation(
        "CHAN8",
        "each channel batches on its own clock, and the observability one is reachable",
        "deploy/compose/otel/collector-config.yaml",
        "    timeout: ${env:AIRA_OTEL_BACKEND_BATCH_SECONDS:-200ms}\n",
        "    timeout: 200ms\n",
        "tools/tests/test_both_channels_are_configured_the_same_way.py",
    ),
    Mutation(
        "CHAN10",
        "the observability channel can present a client certificate and hold a queue",
        "deploy/compose/otel/collector-config.yaml",
        "      cert_file: ${env:AIRA_OTEL_BACKEND_CLIENT_CERT_FILE}\n      key_file: ${env:AIRA_OTEL_BACKEND_CLIENT_KEY_FILE}\n    compression: ${env:AIRA_OTEL_BACKEND_COMPRESSION:-gzip}\n    sending_queue:",
        "    compression: ${env:AIRA_OTEL_BACKEND_COMPRESSION:-gzip}\n    sending_queue:",
        "tools/tests/test_both_channels_are_configured_the_same_way.py",
    ),
    Mutation(
        "LOOP1",
        "the console stops signing in again when signing in is not the problem",
        "management/frontend/src/app/core/auth/auth.service.ts",
        "    if (attempts > LOOP_LIMIT) {",
        "    if (false) {",
        "tools/tests/test_the_console_cannot_loop_through_the_login.py",
    ),
    Mutation(
        "LOOP2",
        "the way out of the loop ends the session at the provider, not only locally",
        "management/frontend/src/app/core/auth/auth.service.ts",
        "    this.oauth.logOut({ client_id: authConfig.clientId ?? '' });",
        "    this.oauth.logOut(true);",
        "tools/tests/test_the_console_cannot_loop_through_the_login.py",
    ),
    Mutation(
        "CFGCH1",
        "the file somebody copies shows how to switch the observability channel off",
        "config/showcase.example.yaml",
        "  #   backend_config: /etc/otelcol-contrib/nobackend.yaml",
        "  #   (ask somebody how to turn this one off)",
        "tools/tests/test_the_config_examples_are_real.py",
    ),
    Mutation(
        "CFGCH2",
        "a channel switch is commented out, never rendered empty",
        "config/showcase.example.yaml",
        "  sample_ratio: 1.0",
        "  sample_ratio: 1.0\n  forward_config: ''",
        "tools/tests/test_the_config_examples_are_real.py",
    ),
    Mutation(
        "RENDER1",
        "a variable appended to a rendered .env is reported, not silently carried",
        "tools/config_render.py",
        "    for name in sorted(set(present) - set(rendered)):",
        "    for name in sorted(set()):",
        "tools/tests/test_the_config_outranks_the_deployment.py",
    ),
    Mutation(
        "ADDR1",
        "the page and the OTLP endpoint are one address, so a collector elsewhere can use it",
        "deploy/compose/docker-compose.yml",
        '      - "${AIRA_BIND_HOST:-127.0.0.1}:${AIRA_PUBLISH_OTLP_INSPECTOR_PORT:-4319}:4318"',
        '      - "${AIRA_BIND_HOST:-127.0.0.1}:${AIRA_PUBLISH_OTLP_INSPECTOR_PORT:-4319}:8080"',
        "tools/tests/test_the_siem_gets_requests_not_plumbing.py",
    ),
    Mutation(
        "NAME1",
        "the inspector answers to the name the rest of the stack is spelled with",
        "deploy/compose/docker-compose.yml",
        "        aliases: [otel-inspector]",
        "        aliases: []",
        "tools/tests/test_the_siem_gets_requests_not_plumbing.py",
    ),
    Mutation(
        "STUCK1",
        "a status that says nothing is lost has looked at what the exporters are holding",
        "tools/otel_status.py",
        "    held = _queued(body)",
        "    held = {}",
        "tools/tests/test_the_siem_gets_requests_not_plumbing.py",
    ),
    Mutation(
        "OI6",
        "an empty page names the hop before this one, not only its own configuration",
        "tools/otlp_inspector.py",
        '            "before this one: <code>AIRA_OTEL_ENABLED</code> and <code>AIRA_OTEL_ENDPOINT</code> "',
        '            "before this one. "',
        "tools/tests/test_the_inspector_shows_what_was_forwarded.py",
    ),
    Mutation(
        "OI5",
        "the tree shows the document as it is, rather than a tidied-up version of it",
        "tools/otlp_inspector.py",
        '        kids = "".join(json_tree(v, name=k, depth=depth + 1) for k, v in value.items())',
        '        kids = "".join(json_tree(v, depth=depth + 1) for k, v in value.items())',
        "tools/tests/test_the_inspector_shows_what_was_forwarded.py",
    ),
    Mutation(
        "OI4",
        "a credential under any header name is found, not only under Authorization",
        "tools/otlp_inspector.py",
        'CREDENTIAL_HINTS = ("auth", "key", "token", "secret", "password", "credential", "cookie", "team")',
        'CREDENTIAL_HINTS = ("authorization",)',
        "tools/tests/test_the_inspector_shows_what_was_forwarded.py",
    ),
    Mutation(
        "OI1",
        "the inspector reports a credential without printing it",
        "tools/otlp_inspector.py",
        '                f"{scheme} ({len(value)} chars)" if rest else f"(opaque, {len(value)} chars)"',
        "                value",
        "tools/tests/test_the_inspector_shows_what_was_forwarded.py",
    ),
    Mutation(
        "OI2",
        "the inspector escapes an attribute value it did not write",
        "tools/otlp_inspector.py",
        '    return html.escape("" if value is None else str(value))',
        '    return "" if value is None else str(value)',
        "tools/tests/test_the_inspector_shows_what_was_forwarded.py",
    ),
    Mutation(
        "OI3",
        "the inspector's totals outlive what its ring buffer still holds",
        "tools/otlp_inspector.py",
        "            self.totals[arrival.signal] += 1",
        "            self.totals[arrival.signal] = len(self._arrivals)",
        "tools/tests/test_the_inspector_shows_what_was_forwarded.py",
    ),
    Mutation(
        "ID31",
        "the health probes produce no spans",
        "libs/src/aira_common/observability/providers.py",
        "    exclude_health_probes()",
        "    pass  # probes traced like requests",
        "libs/tests/test_observability.py",
    ),
    Mutation(
        "ID32",
        "excluding the probes does not swallow real traffic",
        "libs/src/aira_common/observability/providers.py",
        'HEALTH_PATHS = r"^[a-z]+://[^/]+/(healthz|readyz)\\??$"',
        'HEALTH_PATHS = ".*"',
        "libs/tests/test_observability.py",
    ),
    Mutation(
        "ID30",
        "the collector says what arrives on every signal, metrics included",
        "deploy/compose/otel/collector-config.yaml",
        "      exporters: [otlp/backend, debug, file/arrived]\n    metrics:",
        "      exporters: [otlp/backend]\n    metrics:",
        "tools/tests/test_the_collector_says_what_arrives.py",
    ),
    Mutation(
        "ID29",
        "a rendered payload is one line, so a log stays line-oriented",
        "libs/src/aira_common/observability/payload.py",
        '        return str(json.dumps(_to_otlp_json(json.loads(rendered)), separators=(",", ":")))',
        "        return str(json.dumps(_to_otlp_json(json.loads(rendered)), indent=2))",
        "libs/tests/test_a_line_about_otel_does_not_travel_by_otel.py",
    ),
    Mutation(
        "ID27",
        "OTLP identifiers are rendered as hex, not protobuf's base64",
        "libs/src/aira_common/observability/payload.py",
        "                base64.b64decode(value).hex()",
        "                value",
        "libs/tests/test_a_line_about_otel_does_not_travel_by_otel.py",
    ),
    Mutation(
        "ID28",
        "enums are rendered as the numbers a receiver is handed",
        "libs/src/aira_common/observability/payload.py",
        "        rendered = MessageToJson(encode(batch), use_integers_for_enums=True)",
        "        rendered = MessageToJson(encode(batch))",
        "libs/tests/test_a_line_about_otel_does_not_travel_by_otel.py",
    ),
    Mutation(
        "ID25",
        "the OTLP batch is rendered as JSON only when somebody asked for it",
        "libs/src/aira_common/observability/payload.py",
        '    if not args or items <= 0:\n        return ""',
        '    if not args:\n        return ""',
        "libs/tests/test_a_line_about_otel_does_not_travel_by_otel.py",
    ),
    Mutation(
        "ID26",
        "only as many items as were asked for are rendered",
        "libs/src/aira_common/observability/payload.py",
        "            batch = batch[:items]",
        "            batch = batch[:]",
        "libs/tests/test_a_line_about_otel_does_not_travel_by_otel.py",
    ),
    Mutation(
        "ID23",
        "an export line says how many requests are in the batch",
        "libs/src/aira_common/observability/export.py",
        '            fields["traces"] = _distinct_traces(args)',
        '            fields["traces"] = None',
        "libs/tests/test_a_line_about_otel_does_not_travel_by_otel.py",
    ),
    Mutation(
        "ID24",
        "a content policy that does not name the issuer is a mismatch, and an absent one is not a pass",
        "tools/showcase_doctor.py",
        '    for part in policy.split(";"):',
        "    for part in ['connect-src ' + name]:",
        "tools/tests/test_the_doctor_does_not_cry_wolf.py",
    ),
    Mutation(
        "ID21",
        "a key set twice in the env file is reported, whatever the file's provenance",
        "tools/config_render.py",
        "    duplicates = duplicated_keys(text)",
        "    duplicates = []",
        "tools/tests/test_the_config_outranks_the_deployment.py",
    ),
    Mutation(
        "ID22",
        "the console host reaches the realm's redirect URIs",
        "deploy/compose/docker-compose.yml",
        "      AIRA_CONSOLE_HOST: ${AIRA_CONSOLE_HOST:-localhost}",
        "      # console host intentionally not passed",
        "tools/tests/test_the_realm_follows_the_console_port.py",
    ),
    Mutation(
        "ID19",
        "a batch the collector partly threw away is not reported as a success",
        "libs/src/aira_common/observability/export.py",
        "            if rejected:",
        "            if False:",
        "libs/tests/test_a_line_about_otel_does_not_travel_by_otel.py",
    ),
    Mutation(
        "ID20",
        "the OTLP response body is read, not just its status",
        "libs/src/aira_common/observability/export.py",
        "            self._last = response\n            return response",
        "            return response",
        "libs/tests/test_a_line_about_otel_does_not_travel_by_otel.py",
    ),
    Mutation(
        "OT8",
        "a line about OTel does not travel by OTel",
        "libs/src/aira_common/logging.py",
        "    if not isinstance(event, str) or _hold_back.get():",
        "    if not isinstance(event, str):",
        "libs/tests/test_a_line_about_otel_does_not_travel_by_otel.py",
    ),
    Mutation(
        "OT9",
        "the OTel SDK's own explanation of a failed export reaches a terminal",
        "libs/src/aira_common/observability/diagnostics.py",
        "    logger.addHandler(SdkDiagnostics())",
        "    pass",
        "libs/tests/test_a_line_about_otel_does_not_travel_by_otel.py",
    ),
    Mutation(
        "ID17",
        "Vault is watched even though it is read before the settings exist",
        "libs/src/aira_common/secrets.py",
        "    _say_something_before_the_settings_exist()\n    vault = VaultClient(config, client)",
        "    vault = VaultClient(config, client)",
        "libs/tests/test_the_state_stores_say_what_they_answered.py",
    ),
    Mutation(
        "ID18",
        "reading secrets does not reconfigure logging a process already set up",
        "libs/src/aira_common/secrets.py",
        "    if not structlog.is_configured():\n        configure_logging()",
        "    if True:\n        configure_logging()",
        "libs/tests/test_the_state_stores_say_what_they_answered.py",
    ),
    Mutation(
        "ID7",
        "an unreachable identity provider is not reported as a rejected token",
        "libs/src/aira_common/oidc.py",
        "        except jwt.PyJWKClientConnectionError as exc:",
        "        except jwt.PyJWKSetError as exc:",
        "libs/tests/test_when_the_identity_provider_is_gone.py",
    ),
    Mutation(
        "ID15",
        "a malformed bearer token is a 401, not an unhandled exception",
        "libs/src/aira_common/oidc.py",
        "        except jwt.PyJWTError as exc:\n            # Not an outage.",
        "        except jwt.PyJWKClientError as exc:\n            # Not an outage.",
        "libs/tests/test_when_the_identity_provider_is_gone.py",
    ),
    Mutation(
        "ID8",
        "the JWKS fetch is bounded, rather than taking PyJWT's thirty seconds",
        "libs/src/aira_common/oidc.py",
        "        super().__init__(uri, timeout=timeout)",
        "        super().__init__(uri)",
        "libs/tests/test_when_the_identity_provider_is_gone.py",
    ),
    Mutation(
        "ID9",
        "token validation does not run on the event loop",
        "gateway/src/aira_gateway/auth/dependencies.py",
        "        principal = await asyncio.to_thread(validator.validate, token)",
        "        principal = validator.validate(token)",
        "gateway/tests/test_token_validation_does_not_hold_the_event_loop.py",
    ),
    Mutation(
        "ID10",
        "a published record says which partition and offset it landed on",
        "libs/src/aira_common/kafka.py",
        "            call.note(\n                partition=",
        "            dict(\n                partition=",
        "libs/tests/test_kafka_says_what_it_sent.py",
    ),
    Mutation(
        "ID11",
        "connecting to the broker is a watched call",
        "libs/src/aira_common/kafka.py",
        '            "producer.start",',
        '            "producer.unwatched",',
        "libs/tests/test_kafka_says_what_it_sent.py",
    ),
    Mutation(
        "ID12",
        "an event arriving on the bus is reported, not only one that could not be applied",
        "gateway/src/aira_gateway/consumer/worker.py",
        '                "consumer.receive",',
        '                "consumer.unwatched",',
        "gateway/tests/test_the_channel_is_wired_to_what_it_names.py",
    ),
    Mutation(
        "ID13",
        "a database that cannot be reached is reported",
        "gateway/src/aira_gateway/db/base.py",
        '        if not (getattr(context, "is_disconnect", False) or context.connection is None):',
        "        if True:",
        "gateway/tests/test_the_channel_is_wired_to_what_it_names.py",
    ),
    # A live round against real Gemini on Vertex found every embedding answering 500: the adapter
    # spoke the Generative Language API's verbs, which Vertex does not serve to an API key, and the
    # catalogue's addressing never reached an embedding request.
    Mutation(
        "EM1",
        "an embedding request carries the catalogue's addressing to its adapter",
        "gateway/src/aira_gateway/api/serving/prepare.py",
        '        embed = embed.model_copy(update={"addressing": declaration.addressing})',
        "        embed = embed.model_copy(update={})",
        "gateway/tests/test_a_catalogued_embedding_model_is_reached.py",
    ),
    Mutation(
        "EM2",
        "Vertex embeds through its own verb, `:predict`",
        "gateway/src/aira_gateway/upstreams/vertex/adapters.py",
        '                region=region, publisher=publisher, model=request.model, method="predict"',
        '                region=region, publisher=publisher, model=request.model, method="embedContent"',  # noqa: E501
        "gateway/tests/test_vertex.py",
    ),
    Mutation(
        "EM3",
        "an over-long text is refused by Vertex rather than embedded in part",
        "gateway/src/aira_gateway/upstreams/vertex/embedding_mapping.py",
        '    parameters: dict[str, Any] = {"autoTruncate": False}',
        "    parameters: dict[str, Any] = {}",
        "gateway/tests/test_vertex.py",
    ),
    Mutation(
        "EM4",
        "a model with nowhere to be sent is a refusal on a direct verb, not a 500",
        "gateway/src/aira_gateway/api/serving/refusals.py",
        "    AmbiguousModel,\n    RegionNotAllowed,\n    # A declaration",
        "    RegionNotAllowed,\n    # A declaration",
        "gateway/tests/test_a_catalogued_embedding_model_is_reached.py",
    ),
    Mutation(
        "IE1",
        "a request that fails inside this gateway still leaves an audit row",
        "gateway/src/aira_gateway/api/serving/accounting.py",
        "            state.failed(500, Outcome.INTERNAL_ERROR)",
        "            record = False",
        RATELIMIT_ROUTES,
    ),
    Mutation(
        "SA1",
        "a Gemini stream the caller left after usage arrived is 499, not served",
        "gateway/src/aira_gateway/api/serving/streaming.py",
        "            acct.abandoned(\n                self.model,\n                self.usage,\n                stored,\n                self._tool_calls,\n                complete=self.finish_reason is not None,\n            )",
        "            acct.served(self.model, self.usage, stored, self._tool_calls)",
        RATELIMIT_ROUTES,
    ),
    Mutation(
        "SA2",
        "a KIRA stream the caller left after usage arrived is settled and recorded",
        "gateway/src/aira_gateway/api/serving/streaming.py",
        "        elif self.usage is not None:",
        "        elif False:",
        "gateway/tests/test_kira_streaming_disconnect.py",
    ),
    Mutation(
        "SA3",
        "an abandoned stream keeps the status that says the caller left",
        "gateway/src/aira_gateway/api/serving/accounting.py",
        "        self.served(model, usage, payload, tool_calls)\n        self.status = CLIENT_CLOSED_REQUEST",  # noqa: E501
        "        self.served(model, usage, payload, tool_calls)\n        self.status = 200",
        RATELIMIT_ROUTES,
    ),
    Mutation(
        "OC1",
        "the model's declared default output cap is sent when the caller sets none",
        "gateway/src/aira_gateway/api/serving/prepare.py",
        '            "max_output_tokens": declaration.output_cap(asked.max_output_tokens),',
        '            "max_output_tokens": asked.max_output_tokens,',
        "gateway/tests/test_model_catalog.py "
        "gateway/tests/test_every_candidate_is_sent_its_own_defaults.py",
    ),
    Mutation(
        "RO1",
        "a role Gemini does not have is refused, not read as the user's turn",
        "gateway/src/aira_gateway/api/gemini/schemas.py",
        "        if self.role is not None and self.role not in _ROLES:",
        "        if False:",
        "gateway/tests/test_a_callers_mistake_is_refused_by_name.py",
    ),
    Mutation(
        "MT1",
        "a response media type this gateway cannot honour is refused, not ignored",
        "gateway/src/aira_gateway/api/gemini/schemas.py",
        '        if mime is None or mime == "text/plain":',
        "        if True:",
        "gateway/tests/test_a_callers_mistake_is_refused_by_name.py",
    ),
    Mutation(
        "SB1",
        "a sampling value outside every dialect's range is the caller's 400, before dispatch",
        "gateway/src/aira_gateway/api/serving/controls.py",
        "        if value is not None and not low <= value <= high:",
        "        if False:",
        "gateway/tests/test_a_callers_mistake_is_refused_by_name.py",
    ),
    Mutation(
        "LS1",
        "a listed model offers only the verbs its declaration allows",
        "gateway/src/aira_gateway/api/serving/context.py",
        "    verbs = [verb for verb in offered if verb in allowed]",
        "    verbs = list(offered)",
        "gateway/tests/test_the_model_list_says_what_is_served.py",
    ),
    Mutation(
        "LS2",
        "a catalogued model an adapter serves is listed, and an unapproved one is not",
        "gateway/src/aira_gateway/api/serving/context.py",
        "        if declaration.name in configured or not declaration.approved or not declaration.provider:",  # noqa: E501
        "        if declaration.name in configured or not declaration.provider:",
        "gateway/tests/test_the_model_list_says_what_is_served.py",
    ),
    Mutation(
        "PV1",
        "a served row names the region that answered, not the configured one",
        "gateway/src/aira_gateway/api/serving/accounting.py",
        "        provenance=await provenance(request, model, trail.served_region),",
        "        provenance=await provenance(request, model),",
        "gateway/tests/test_the_audit_row_says_where_and_what_it_cost.py",
    ),
    Mutation(
        "PV2",
        "a catalogued model's row names the adapter of its own publisher",
        "gateway/src/aira_gateway/api/serving/context.py",
        "    configured = registry.provenance_for(declared.provider, declared.publisher)",
        "    configured = registry.provenance_for(declared.provider)",
        "gateway/tests/test_the_audit_row_says_where_and_what_it_cost.py",
    ),
    Mutation(
        "EP1",
        "an embedding is priced by the input tokens its adapter reported",
        "gateway/src/aira_gateway/api/serving/accounting.py",
        "            usage = CanonicalUsage(prompt_tokens=tokens, completion_tokens=0)",
        "            usage = None",
        "gateway/tests/test_the_audit_row_says_where_and_what_it_cost.py",
    ),
    Mutation(
        "EP2",
        "Vertex reports where an embedding was answered",
        "gateway/src/aira_gateway/upstreams/vertex/adapters.py",
        "                served_region=region,",
        '                served_region="",',
        "gateway/tests/test_vertex.py",
    ),
    Mutation(
        "TK1",
        "the Gemini surface counts the answer and the thinking apart, as Google does",
        "gateway/src/aira_gateway/api/gemini/mapping.py",
        "    return max(0, completion - reasoning)",
        "    return completion",
        "gateway/tests/test_reasoning.py",
    ),
    Mutation(
        "RS1",
        "thoughts are never asked for while thinking is off",
        "gateway/src/aira_gateway/upstreams/gemini_mapping.py",
        "    if request.include_reasoning and thinking_on:",
        "    if request.include_reasoning:",
        "gateway/tests/test_the_callers_word_on_reasoning.py gateway/tests/test_reasoning.py",
    ),
    Mutation(
        "RS4",
        "thoughts are not asked of a model whose thinking no setting switched on",
        "gateway/src/aira_gateway/upstreams/gemini_mapping.py",
        "    thinking_on = request.thinking is not None and request.thinking.mode != ThinkingMode.DISABLED",
        "    thinking_on = request.thinking is None or request.thinking.mode != ThinkingMode.DISABLED",
        "gateway/tests/test_reasoning.py",
    ),
    Mutation(
        "RS2",
        "an explicit includeThoughts: false withholds the reasoning the use case allows",
        "gateway/src/aira_gateway/api/serving/controls.py",
        "    returned = allowed and asked_for is not False",
        "    returned = allowed",
        "gateway/tests/test_the_callers_word_on_reasoning.py",
    ),
    Mutation(
        "RS3",
        "a stream asking for thoughts is refused rather than answered without them",
        "gateway/src/aira_gateway/api/gemini/routes.py",
        '        if method == "streamGenerateContent" and _asked_for_reasoning(gemini_request):',
        "        if False:",
        "gateway/tests/test_the_callers_word_on_reasoning.py",
    ),
    Mutation(
        "AE1",
        "an empty attachment is refused, not forwarded",
        "gateway/src/aira_gateway/attachments.py",
        "    if not decoded:",
        "    if False:",
        "gateway/tests/test_an_empty_attachment_is_refused.py",
    ),
    Mutation(
        "HEC6",
        "the inspector accepts HEC at /services/collector",
        "tools/otlp_inspector.py",
        "        signal = HEC_SIGNAL if path in HEC_PATHS else SIGNAL_PATHS.get(path)",
        "        signal = SIGNAL_PATHS.get(path)",
        "tools/tests/test_the_inspector_shows_what_was_forwarded.py",
    ),
    Mutation(
        "HEC7",
        "the inspector counts a HEC body by the events in it",
        "tools/otlp_inspector.py",
        "                arrival.records = len(events)",
        "                arrival.records = 1",
        "tools/tests/test_the_inspector_shows_what_was_forwarded.py",
    ),
    Mutation(
        "HEC8",
        "the inspector answers HEC with HEC's success body",
        "tools/otlp_inspector.py",
        '        self._send(200, HEC_OK_BODY if signal == HEC_SIGNAL else OK_BODY, "application/json")',
        '        self._send(200, OK_BODY, "application/json")',
        "tools/tests/test_the_inspector_shows_what_was_forwarded.py",
    ),
    Mutation(
        "REC1",
        "a recipe that brings an extension keeps the base's in the list it restates",
        "deploy/compose/otel/recipes/forward-oauth2.yaml",
        "  extensions: [headers_setter/backend, headers_setter/forward, oauth2client/forward]",
        "  extensions: [oauth2client/forward]",
        "tools/tests/test_the_collector_has_a_core_and_recipes.py",
    ),
    Mutation(
        "REC2",
        "a recipe's secret falls back to the name of its variable",
        "deploy/compose/otel/recipes/forward-splunk-hec.yaml",
        "    token: ${env:SPLUNK_HEC_TOKEN:-SPLUNK_HEC_TOKEN-is-not-set}",
        "    token: ${env:SPLUNK_HEC_TOKEN}",
        "tools/tests/test_the_collector_has_a_core_and_recipes.py",
    ),
    Mutation(
        "REC3",
        "the recipes' env file is optional, so the collector starts without it",
        "deploy/compose/docker-compose.yml",
        "      - path: ./otel/custom/collector.env\n        required: false",
        "      - path: ./otel/custom/collector.env\n        required: true",
        "tools/tests/test_the_collector_has_a_core_and_recipes.py",
    ),
    Mutation(
        "REC4",
        "the recipes are mounted into the collector",
        "deploy/compose/docker-compose.yml",
        "      - ./otel/recipes:/etc/otelcol-contrib/recipes:ro\n",
        "",
        "tools/tests/test_the_collector_has_a_core_and_recipes.py",
    ),
    Mutation(
        "REC5",
        "the Splunk HEC recipe sends metrics to the metrics exporter",
        "deploy/compose/otel/recipes/forward-splunk-hec.yaml",
        "      exporters: [splunk_hec/forward_metrics]",
        "      exporters: [splunk_hec/forward]",
        "tools/tests/test_the_collector_has_a_core_and_recipes.py",
    ),
    Mutation(
        "REC6",
        "the core of collector variables is closed",
        "deploy/compose/docker-compose.yml",
        "      AIRA_OTEL_FORWARD_RETRY_INITIAL: ${AIRA_OTEL_FORWARD_RETRY_INITIAL:-5s}\n",
        "      AIRA_OTEL_FORWARD_RETRY_INITIAL: ${AIRA_OTEL_FORWARD_RETRY_INITIAL:-5s}\n      AIRA_OTEL_FORWARD_TRACES_ENDPOINT: ${AIRA_OTEL_FORWARD_TRACES_ENDPOINT:-}\n",
        "tools/tests/test_the_collector_has_a_core_and_recipes.py",
    ),
    Mutation(
        "REC7",
        "a recipe reads no AIRA variable outside the core",
        "deploy/compose/otel/recipes/forward-splunk-hec.yaml",
        "      insecure_skip_verify: ${env:AIRA_OTEL_FORWARD_INSECURE}",
        "      insecure_skip_verify: ${env:AIRA_OTEL_FORWARD_HEC_INSECURE:-false}",
        "tools/tests/test_the_collector_has_a_core_and_recipes.py",
    ),
    Mutation(
        "REC8",
        "an installation's own collector files are git-ignored",
        ".gitignore",
        "deploy/compose/otel/custom/*\n",
        "",
        "tools/tests/test_the_collector_has_a_core_and_recipes.py",
    ),
    Mutation(
        "REC9",
        "every value a recipe reads is listed in the example env file",
        "deploy/compose/otel/recipes/collector.env.example",
        "#SPLUNK_HEC_METRICS_INDEX=aira_metrics",
        "#SPLUNK_HEC_METRICS_IDX=aira_metrics",
        "tools/tests/test_the_collector_has_a_core_and_recipes.py",
    ),
    Mutation(
        "FX1",
        "creating a use case announces the creator's membership to the gateway",
        "management/backend/src/aira_management/apps/usecases/views/grants.py",
        '    emit("membership.upserted", _membership_payload(usecase, user.get_username(), role))',
        "    pass",
        "management/backend/tests/test_usecases.py",
    ),
    Mutation(
        "SR1",
        "the delivery channel keeps a request refused before attribution",
        "deploy/compose/otel/collector-forward.yaml",
        'attributes["aira.use_case"] == nil and attributes["aira.outcome"] == nil and ',
        'attributes["aira.use_case"] == nil and ',
        "tools/tests/test_the_siem_gets_requests_not_plumbing.py",
    ),
    Mutation(
        "SR2",
        "an unauthenticated attempt is marked on its span",
        "gateway/src/aira_gateway/auth/dependencies.py",
        "        mark_refused(UNAUTHENTICATED, 401)\n",
        "",
        "gateway/tests/test_a_refused_access_is_on_its_span.py",
    ),
    Mutation(
        "SR3",
        "a forbidden attempt is marked on its span",
        "gateway/src/aira_gateway/auth/dependencies.py",
        "    if reason:\n        mark_refused(",
        "    if False:\n        mark_refused(",
        "gateway/tests/test_a_refused_access_is_on_its_span.py",
    ),
    Mutation(
        "SR4",
        "an access attempt carries where it came from before the verdict",
        "gateway/src/aira_gateway/auth/dependencies.py",
        '    set_span_attributes({"aira.source_ip": client_ip(request)})\n',
        "",
        "gateway/tests/test_a_refused_access_is_on_its_span.py",
    ),
    Mutation(
        "SR5",
        "the use case a refused caller asked for is written only when it is a slug",
        "gateway/src/aira_gateway/auth/attribution.py",
        "    requested = use_case if use_case and is_valid_use_case(use_case) else None",
        "    requested = use_case",
        "gateway/tests/test_a_refused_access_is_on_its_span.py",
    ),
    Mutation(
        "RD1",
        "a content read records the roles the reader held at that moment",
        "gateway/src/aira_gateway/api/reporting/traces.py",
        '        roles=",".join(sorted(principal.roles)),\n',
        "",
        "gateway/tests/test_the_content_read_log.py",
    ),
    Mutation(
        "RD2",
        "the content-read log is served to the platform roles only",
        "gateway/src/aira_gateway/api/incidents/content_reads.py",
        '    if not (principal.allows(Permission.CONTENT_READ_READ) or principal.method == "demo"):',
        "    if False:",
        "gateway/tests/test_the_content_read_log.py",
    ),
    Mutation(
        "RD3",
        "a read from before roles were kept says unknown, not none",
        "gateway/src/aira_gateway/api/incidents/content_reads.py",
        '            value = None if value is None else [role for role in value.split(",") if role]',
        '            value = [role for role in (value or "").split(",") if role]',
        "gateway/tests/test_the_content_read_log.py",
    ),
    Mutation(
        "RQ1",
        "the trace list narrows to one request by its id",
        "gateway/src/aira_gateway/api/reporting/traces.py",
        "        stmt = stmt.where(RequestLog.id == request_id)",
        "        stmt = stmt",
        "gateway/tests/test_traces.py",
    ),
    # ---- the browser suite tidies after a red run -------------------------------------------
    Mutation(
        "ET1",
        "a red browser run still purges the use cases it retired",
        "Makefile",
        "@( cd e2e && npm install --silent && npx playwright test )",
        "@set -e; ( cd e2e && npm install --silent && npx playwright test )",
        "tools/tests/test_e2e_tidies_after_a_red_run.py",
    ),
    # ---- stored roles (FRD-614) -------------------------------------------------------------
    Mutation(
        "RL1",
        "a roles table the gateway cannot read leaves IT Steuerung nothing, not its default",
        "gateway/src/aira_gateway/auth/role_definitions.py",
        "        return builtin_roles(self._mapping, it_steuerung=frozenset(), strict_data=self._strict_data)",
        "        return builtin_roles(self._mapping, strict_data=self._strict_data)",
        "gateway/tests/test_role_definitions.py",
    ),
    Mutation(
        "RL2",
        "a stored row cannot pass itself off as a fixed role",
        "gateway/src/aira_gateway/auth/role_definitions.py",
        "            if not row.builtin and row.slug not in _BUILTIN_SLUGS",
        "            if not row.builtin",
        "gateway/tests/test_role_definitions.py",
    ),
    Mutation(
        "RL3",
        "a group Keycloak does not have is never bound",
        "management/backend/src/aira_management/apps/roles/views.py",
        "    if not exists:\n        raise ValidationError",
        "    if False:\n        raise ValidationError",
        "management/backend/tests/test_roles_api.py",
    ),
    Mutation(
        "RL4",
        "a group nobody could verify is refused, not trusted",
        "management/backend/src/aira_management/apps/roles/views.py",
        "    if directory is None:\n        raise DirectoryCannotVerify()",
        "    if directory is None:\n        return path",
        "management/backend/tests/test_roles_api.py",
    ),
    Mutation(
        "RL5",
        "the Global Administrator and IT Security cannot be changed",
        "management/backend/src/aira_management/apps/roles/views.py",
        "        if role.fixed:\n            raise PermissionDenied",
        "        if False:\n            raise PermissionDenied",
        "management/backend/tests/test_roles_api.py",
    ),
    Mutation(
        "RL6",
        "the reserved permission cannot be given to a role",
        "management/backend/src/aira_management/apps/roles/views.py",
        "    reserved = wanted - GRANTABLE",
        "    reserved: frozenset[Permission] = frozenset()",
        "management/backend/tests/test_roles_api.py",
    ),
    Mutation(
        "RL7",
        "a stored role is held only by the people in its group",
        "management/backend/src/aira_management/rbac.py",
        "            else any(django_group_name(path) in mirrors for path in role.group_paths)",
        "            else True",
        "management/backend/tests/test_roles_api.py",
    ),
    Mutation(
        "RL8",
        "a group exists only when Keycloak answers with exactly the path asked for",
        "libs/src/aira_common/directory.py",
        '        return isinstance(body, dict) and body.get("path") == wanted',
        "        return isinstance(body, dict)",
        "libs/tests/test_directory.py",
    ),
    Mutation(
        "RL9",
        "writing a role takes role.manage, which reading does not give",
        "management/backend/src/aira_management/apps/roles/views.py",
        "        return [IsAuthenticated(), requires(Permission.ROLE_MANAGE)()]",
        "        return [IsAuthenticated(), requires(Permission.ROLE_READ)()]",
        "management/backend/tests/test_roles_api.py",
    ),
    Mutation(
        "RL10",
        "reading any use case's content includes reaching the request",
        "gateway/src/aira_gateway/api/reporting/traces.py",
        "    reach_any = principal.allows(Permission.PAYLOAD_READ_ANY)",
        "    reach_any = False",
        "gateway/tests/test_traces.py",
    ),
    Mutation(
        "RL11",
        "administering every use case includes seeing them",
        "management/backend/src/aira_management/rbac.py",
        "    if Permission.USECASE_READ_ALL in granted or Permission.USECASE_MANAGE_ALL in granted:",
        "    if Permission.USECASE_READ_ALL in granted:",
        "management/backend/tests/test_roles_api.py",
    ),
    Mutation(
        "RL12",
        "a directory search is literal: a wildcard cannot list the whole directory",
        "libs/src/aira_common/directory.py",
        '        needle = query.replace("%", "").replace("*", "").strip()',
        "        needle = query.strip()",
        "libs/tests/test_directory.py",
    ),
    Mutation(
        "RL13",
        "a parent group that does not match is not offered as a result",
        "libs/src/aira_common/directory.py",
        "            if isinstance(path, str) and path and needle in path.lower():",
        "            if isinstance(path, str) and path:",
        "libs/tests/test_directory.py",
    ),
    Mutation(
        "RD4",
        "the content-read log counts what its filters match, not every read",
        "gateway/src/aira_gateway/api/incidents/content_reads.py",
        "                select(func.count()).select_from(PayloadAccess).where(*conditions)",
        "                select(func.count()).select_from(PayloadAccess)",
        "gateway/tests/test_the_content_read_log.py",
    ),
    # ---- the pairwise permission matrix (FRD-614 FR-7) ------------------------------------
    Mutation(
        "PW1",
        "two permissions together open nothing either opens alone (gateway)",
        "gateway/src/aira_gateway/api/incidents/suspensions.py",
        "    if not principal.allows(Permission.INCIDENT_SUSPEND):",
        "    if not (principal.allows(Permission.INCIDENT_SUSPEND) or (principal.allows(Permission.CATALOG_WRITE) and principal.allows(Permission.REPORT_READ_ALL))):",
        "gateway/tests/test_the_pairwise_permission_matrix.py",
    ),
    Mutation(
        "PW2",
        "two permissions together open nothing either opens alone (Management)",
        "management/backend/src/aira_management/apps/usecases/views/retirement.py",
        "        if not may(request.user, Permission.USECASE_PURGE):",
        "        if not (may(request.user, Permission.USECASE_PURGE) or (may(request.user, Permission.SMOKETEST_AUTHOR) and may(request.user, Permission.DIRECTORY_SEARCH))):",
        "management/backend/tests/test_the_pairwise_permission_matrix.py",
    ),
    Mutation(
        "RL14",
        "an admin grant on a retired use case opens no directory",
        "management/backend/src/aira_management/rbac.py",
        "        return get_objects_for_user(user, MANAGE_PERM, klass=UseCase.objects.live()).exists()",
        "        return get_objects_for_user(user, MANAGE_PERM, klass=UseCase).exists()",
        "management/backend/tests/test_the_pairwise_permission_matrix.py",
    ),
    Mutation(
        "RL15",
        "a retired use case is run by nobody, and opens no question catalogue",
        "management/backend/src/aira_management/apps/usecases/access.py",
        "    reachable = may_call_queryset(user, queryset.live())",
        "    reachable = may_call_queryset(user, queryset)",
        "management/backend/tests/test_the_pairwise_permission_matrix.py",
    ),
    # ---- the Kafka stage and Redis Sentinel -----------------------------------------------
    Mutation(
        "KS1",
        "a stage names every topic: aira.<stage>.<entity>",
        "libs/src/aira_common/kafka.py",
        '    return f"{head}.{cleaned}.{rest}"',
        "    return topic",
        "libs/tests/test_kafka_stage.py",
    ),
    Mutation(
        "KS2",
        "Management publishes to the topics of its stage",
        "management/backend/src/aira_management/apps/outbox/subscriber.py",
        '    topic = staged(base, getattr(settings, "AIRA_KAFKA_STAGE", "") or "")',
        "    topic = base",
        "management/backend/tests/test_outbox_stage.py",
    ),
    Mutation(
        "KS3",
        "the gateway reads under a consumer group of its stage",
        "libs/src/aira_common/kafka.py",
        '    return f"{CONSUMER_GROUP}.{cleaned}" if cleaned else CONSUMER_GROUP',
        "    return CONSUMER_GROUP",
        "libs/tests/test_kafka_stage.py gateway/tests/test_stage_and_sentinel_settings.py",
    ),
    Mutation(
        "KS4",
        "the gateway subscribes to the topics of its stage",
        "gateway/src/aira_gateway/consumer/worker.py",
        "    return tuple(staged(topic, stage) for topic in CONFIG_TOPICS)",
        "    return CONFIG_TOPICS",
        "gateway/tests/test_stage_and_sentinel_settings.py",
    ),
    Mutation(
        "KS5",
        "a stage that cannot be one name segment is refused",
        "libs/src/aira_common/kafka.py",
        "    if cleaned and not _STAGE.fullmatch(cleaned):",
        "    if False:",
        "libs/tests/test_kafka_stage.py gateway/tests/test_stage_and_sentinel_settings.py",
    ),
    Mutation(
        "SN1",
        "configured sentinels are used instead of the URL",
        "libs/src/aira_common/counters.py",
        "    if sentinel is not None:\n        return RedisRunner(sentinel=sentinel)",
        "    if False:\n        return RedisRunner(sentinel=sentinel)",
        "libs/tests/test_counters.py",
    ),
    Mutation(
        "SN2",
        "the leader is asked with the data password",
        "libs/src/aira_common/counters.py",
        "                    password=config.password or None,",
        "                    password=None,",
        "libs/tests/test_counters.py",
    ),
    Mutation(
        "SN3",
        "sentinels without the leader's name refuse to start",
        "gateway/src/aira_gateway/config.py",
        "        if not self.redis_sentinel_service.strip():",
        "        if False:",
        "gateway/tests/test_stage_and_sentinel_settings.py",
    ),
    # ---- reasoning on by default, tool calling off (`FRD-135` §5.6, 2026-09-14) -------------
    Mutation(
        "RN1",
        "a new use case returns reasoning",
        "management/backend/src/aira_management/apps/usecases/models.py",
        "    include_reasoning = models.BooleanField(\n        default=True,",
        "    include_reasoning = models.BooleanField(\n        default=False,",
        "management/backend/tests/test_usecases.py",
    ),
    Mutation(
        "RN2",
        "the migration switches every existing use case on",
        "management/backend/src/aira_management/apps/usecases/migrations/0015_reasoning_on_by_default.py",
        "    use_case.objects.update(include_reasoning=True)",
        "    use_case.objects.update(include_reasoning=False)",
        "management/backend/tests/test_a_migration_announces_every_live_use_case.py",
    ),
    Mutation(
        "RN3",
        "the migration does not bring a retired use case back",
        "management/backend/src/aira_management/apps/usecases/migrations/0016_payload_visibility.py",
        '    for usecase in use_case.objects.filter(deleted_at__isnull=True).order_by("slug"):',
        '    for usecase in use_case.objects.order_by("slug"):',
        "management/backend/tests/test_a_migration_announces_every_live_use_case.py",
    ),
    Mutation(
        "RN4",
        "the migration announces a use case in full, so the gateway resets nothing",
        "management/backend/src/aira_management/apps/usecases/migrations/0016_payload_visibility.py",
        '        "allowed_models": sorted(usecase.allowed_models.values_list("name", flat=True)),\n',
        "",
        "management/backend/tests/test_a_migration_announces_every_live_use_case.py",
    ),
    Mutation(
        "RN5",
        "an event without the switch returns reasoning in the gateway",
        "gateway/src/aira_gateway/consumer/apply.py",
        '        "include_reasoning": bool(payload.get("include_reasoning", True)),',
        '        "include_reasoning": bool(payload.get("include_reasoning", False)),',
        CONSUMER,
    ),
    # ---- speech output on the Gemini surface (`FRD-624`, 2026-09-14) ---------------------------
    Mutation(
        "SP1",
        "speech without a voice is refused by name",
        "gateway/src/aira_gateway/api/gemini/schemas.py",
        "        if self.speaks and self.speechConfig is None:",
        "        if False:",
        "gateway/tests/test_speech.py",
    ),
    Mutation(
        "SP2",
        "a modality other than speech alone is refused",
        "gateway/src/aira_gateway/api/gemini/schemas.py",
        '        if modalities not in ([], ["TEXT"], ["AUDIO"]):',
        "        if False:",
        "gateway/tests/test_speech.py",
    ),
    Mutation(
        "SP3",
        "a system instruction beside speech is refused",
        "gateway/src/aira_gateway/api/gemini/schemas.py",
        "        if speaks and self.systemInstruction is not None:",
        "        if False:",
        "gateway/tests/test_speech.py",
    ),
    Mutation(
        "SP4",
        "thoughts are not asked of a speech model",
        "gateway/src/aira_gateway/api/gemini/schemas.py",
        "        if self.speaks and self.thinkingConfig is not None and self.thinkingConfig.includeThoughts:",
        "        if False:",
        "gateway/tests/test_speech.py",
    ),
    Mutation(
        "SP5",
        "only a model that declares speech serves it",
        "gateway/src/aira_gateway/requirements.py",
        "        if not declaration.can(Capability.SPEECH):",
        "        if False:",
        "gateway/tests/test_speech.py",
    ),
    Mutation(
        "SP6",
        "only a dialect that can ask for speech serves it",
        "gateway/src/aira_gateway/requirements.py",
        '        if provider is not None and not getattr(provider, "speaks", False):',
        "        if False:",
        "gateway/tests/test_speech.py",
    ),
    Mutation(
        "SP7",
        "every speech request asks the speech condition",
        "gateway/src/aira_gateway/api/serving/prepare.py",
        "    if canonical is not None and canonical.speech is not None:\n        checks.append(SpeechSupported(registry, catalog))",
        "    if False:\n        checks.append(SpeechSupported(registry, catalog))",
        "gateway/tests/test_speech.py",
    ),
    Mutation(
        "SP8",
        "the provider is asked for audio",
        "gateway/src/aira_gateway/upstreams/gemini_mapping.py",
        '        generation_config["responseModalities"] = ["AUDIO"]',
        "        pass",
        "gateway/tests/test_speech.py",
    ),
    Mutation(
        "SP9",
        "the audio the provider returns is read back",
        "gateway/src/aira_gateway/upstreams/gemini_mapping.py",
        '        if not mime.startswith("audio/"):',
        "        if True:",
        "gateway/tests/test_speech.py",
    ),
    Mutation(
        "SP10",
        "a streamed answer's audio is described as it passes",
        "gateway/src/aira_gateway/api/serving/streaming.py",
        "            self._audio.add(chunk.audio_delta)",
        "            pass",
        "gateway/tests/test_speech.py",
    ),
    Mutation(
        "SP11",
        "the stored description says how long the audio was",
        "gateway/src/aira_gateway/attachments.py",
        '            if seconds is not None:\n                described["seconds"] = seconds',
        '            if False:\n                described["seconds"] = seconds',
        "gateway/tests/test_speech.py",
    ),
    Mutation(
        "SP12",
        "a speech-only model answers a spoken request",
        "gateway/src/aira_gateway/api/serving/controls.py",
        "    if method not in EMBEDDING_METHODS and not speech and not declaration.can(Capability.GENERATE):",
        "    if method not in EMBEDDING_METHODS and not declaration.can(Capability.GENERATE):",
        "gateway/tests/test_speech.py",
    ),
    Mutation(
        "SP13",
        "Vertex's Gemini wire says it can ask for speech",
        "gateway/src/aira_gateway/upstreams/vertex/adapters.py",
        "    speaks = True\n\n    async def ping",
        "    speaks = False\n\n    async def ping",
        "gateway/tests/test_speech.py",
    ),
    # ---- a member sees what stops their use case or them, and nobody else's (FRD-503) ----------
    Mutation(
        "SU1",
        "a member is not shown a stop on a colleague",
        "gateway/src/aira_gateway/api/incidents/suspensions.py",
        "                AccessSuspension.target_value.in_(names),",
        "                AccessSuspension.target_value.isnot(None),",
        "gateway/tests/test_suspensions.py gateway/tests/test_the_pairwise_permission_matrix.py",
    ),
    Mutation(
        "SU2",
        "without naming a use case, only an incident or oversight role gets the list",
        "gateway/src/aira_gateway/api/incidents/suspensions.py",
        "        if not use_case:\n            _require_an_incident_role(principal)",
        "        if False:\n            _require_an_incident_role(principal)",
        "gateway/tests/test_suspensions.py gateway/tests/test_the_pairwise_permission_matrix.py",
    ),
    Mutation(
        "SU3",
        "a use case outside the caller's scope answers empty",
        "gateway/src/aira_gateway/api/incidents/suspensions.py",
        "        if scope is not None and use_case not in scope:",
        "        if False:",
        "gateway/tests/test_suspensions.py gateway/tests/test_the_pairwise_permission_matrix.py",
    ),
    Mutation(
        "SU4",
        "whoever reads every finding reads every stop",
        "gateway/src/aira_gateway/api/incidents/suspensions.py",
        "    oversight = principal.allows(Permission.INCIDENT_SUSPEND) or principal.allows(",
        "    oversight = principal.allows(Permission.INCIDENT_SUSPEND) or False and principal.allows(",
        "gateway/tests/test_suspensions.py gateway/tests/test_the_pairwise_permission_matrix.py",
    ),
    # ---- the privacy notice, and a change to what is stored is a change to it (FRD-625) ---------
    Mutation(
        "PN1",
        "monthly mode asks again once the month has turned",
        "management/backend/src/aira_management/apps/privacy/schedule.py",
        "    if mode is NoticeMode.MONTHLY and last_of_version.at < month_start(now):",
        "    if False:",
        "management/backend/tests/test_privacy_notice_schedule.py management/backend/tests/test_privacy_notice.py",
    ),
    Mutation(
        "PN2",
        "a changed notice is due again in every mode",
        "management/backend/src/aira_management/apps/privacy/schedule.py",
        "    if last_of_version is None or last_of_version.version != version:",
        "    if last_of_version is None:",
        "management/backend/tests/test_privacy_notice_schedule.py management/backend/tests/test_privacy_notice.py",
    ),
    Mutation(
        "PN3",
        "always mode asks every time",
        "management/backend/src/aira_management/apps/privacy/schedule.py",
        "    if mode is NoticeMode.ALWAYS:",
        "    if False:",
        "management/backend/tests/test_privacy_notice_schedule.py management/backend/tests/test_privacy_notice.py",
    ),
    Mutation(
        "PN4",
        "an acknowledgement of a version the reader was not shown is refused",
        "management/backend/src/aira_management/apps/privacy/views.py",
        "        if version != current:",
        "        if False:",
        "management/backend/tests/test_privacy_notice.py",
    ),
    Mutation(
        "PN5",
        "each role's access to other people's data is printed in the notice",
        "management/backend/src/aira_management/apps/privacy/notice.py",
        '                *(access(language, installation) if key == "access" else ()),',
        "",
        "management/backend/tests/test_privacy_notice.py",
    ),
    Mutation(
        "PN6",
        "what the notice prints about retention is part of its version",
        "management/backend/src/aira_management/apps/privacy/notice.py",
        '    filled["log_retention"] = _days(language, installation.log_retention_days)',
        '    filled["log_retention"] = _days(language, 0)',
        "management/backend/tests/test_privacy_notice.py",
    ),
    Mutation(
        "PN7",
        "a stored column the notice's register does not name fails the suite",
        "management/backend/src/aira_management/apps/privacy/activities.py",
        '    "credential", "api", "operation", "model", "requested_model", "model_selection", "provider",',
        '    "api", "operation", "model", "requested_model", "model_selection", "provider",',
        "management/backend/tests/test_the_privacy_notice_names_every_column.py",
    ),
    Mutation(
        "PN8",
        "a person-shaped column in a table declared not personal is not waved through",
        "management/backend/src/aira_management/apps/privacy/activities.py",
        '    r"(^|_)(user|subject|email|username|actor|owner|author|person|member|ip|address|agent)(_|$)"',
        '    r"(^|_)(nothing)(_|$)"',
        "management/backend/tests/test_the_privacy_notice_names_every_column.py",
    ),
    Mutation(
        "PN9",
        "a changed register or text without a new edition fails the suite",
        "management/backend/src/aira_management/apps/privacy/texts/en.toml",
        'retention = "Without automatic deletion."',
        'retention = "Deleted after a while."',
        "management/backend/tests/test_privacy_notice.py",
    ),
    # Strict data mode, Management's half (`FRD-627`): what refuses, what is reported, what the
    # notice says. Each read of the setting is a place the mode can quietly stop applying.
    Mutation(
        "SDM1",
        "strict data mode refuses giving any stored role what sees a person",
        "management/backend/src/aira_management/apps/roles/views.py",
        "    withheld = wanted - grantable(strict_data=get_settings().strict_data_mode)",
        "    withheld: frozenset[Permission] = frozenset()",
        "management/backend/tests/test_roles_api.py",
    ),
    Mutation(
        "SDM2",
        "a stored grant of what sees a person confers nothing while strict data mode is on",
        "management/backend/src/aira_management/rbac.py",
        "    strict = get_settings().strict_data_mode",
        "    strict = False",
        "management/backend/tests/test_roles_api.py "
        "management/backend/tests/test_the_pairwise_permission_matrix.py",
    ),
    Mutation(
        "SDM3",
        "the roles catalogue tells the console what strict data mode withholds",
        "management/backend/src/aira_management/apps/roles/views.py",
        '                    "strict_data_reserved": strict and permission in STRICT_DATA_RESERVED,',
        '                    "strict_data_reserved": False,',
        "management/backend/tests/test_roles_api.py",
    ),
    Mutation(
        "SDM4",
        "strict data mode refuses saving a content visibility other than oversight",
        "management/backend/src/aira_management/apps/usecases/serializers.py",
        "        if get_settings().strict_data_mode and visibility != UseCase.PAYLOAD_VISIBILITY_OVERSIGHT:",
        "        if False:",
        "management/backend/tests/test_usecases.py",
    ),
    Mutation(
        "SDM5",
        "a use case reports the effective visibility, oversight, while strict data mode is on",
        "management/backend/src/aira_management/apps/usecases/serializers.py",
        '        if "payload_visibility" in data and get_settings().strict_data_mode:',
        "        if False:",
        "management/backend/tests/test_usecases.py",
    ),
    Mutation(
        "SDM6",
        "/me tells the console whether strict data mode is on",
        "management/backend/src/aira_management/apps/api/views.py",
        '                "strict_data": settings.strict_data_mode,',
        '                "strict_data": True,',
        "management/backend/tests/test_api.py",
    ),
    Mutation(
        "SDM7",
        "the privacy notice states the data mode the installation runs",
        "management/backend/src/aira_management/apps/privacy/notice.py",
        '        "strict_data_on" if installation.strict_data else "strict_data_off"',
        '        "strict_data_on"',
        "management/backend/tests/test_privacy_notice.py",
    ),
    Mutation(
        "SDM8",
        "under strict data mode a role that may not see people is not described as seeing them",
        "management/backend/src/aira_management/apps/privacy/notice.py",
        "        if installation.strict_data and not role.sees_people and name in language.access_strict:",
        "        if False:",
        "management/backend/tests/test_privacy_notice.py",
    ),
    Mutation(
        "PN10",
        "Management refuses to boot outside local with no privacy controller",
        "management/backend/src/aira_management/config/security.py",
        "    if not settings.privacy_controller.strip():",
        "    if False:",
        "management/backend/tests/test_hardening.py",
    ),
    Mutation(
        "PN11",
        "a translation missing a section is refused",
        "management/backend/src/aira_management/apps/privacy/texts.py",
        "    missing = [key for key in expected if key not in table]",
        "    missing: list[str] = []",
        "management/backend/tests/test_privacy_notice_texts.py",
    ),
    # ---- integration tokens come from the issuer both planes trust (2026-09-15) ------------------
    Mutation(
        "IS1",
        "the issuer is Compose's, not the bind address",
        "tools/stack_addresses.py",
        '    return _NESTED.sub(lambda match: _resolve(match.group(1), match.group(2)), default).rstrip("/")',
        '    return url("keycloak") + "/realms/aira"',
        "tools/tests/test_one_owner_for_the_stack_addresses.py",
    ),
    Mutation(
        "IS2",
        "integration tokens are requested from the trusted issuer",
        "tests/integration/conftest.py",
        '            f"{stack_addresses.issuer()}/protocol/openid-connect/token",',
        '            f"{KEYCLOAK_URL}/realms/{REALM}/protocol/openid-connect/token",',
        "tools/tests/test_one_owner_for_the_stack_addresses.py",
    ),
    # ---- the usage chart's series: when, and which model (2026-10-01, `FRD-626`) ------------------
    #
    # Every one of these is a chart that looks right. A bucket off by one, a scale against the wrong
    # denominator and a folded tail that drops its rows all draw a plausible picture — which is why
    # the arithmetic is in a module of its own and why it is guarded here rather than by eye.
    Mutation(
        "US1",
        "a quiet bucket is a column, not a bucket left off the axis",
        "gateway/src/aira_gateway/reporting/series.py",
        "    while cursor < end:",
        "    while False:",
        SERIES,
    ),
    Mutation(
        "US2",
        "the axis stops before the exclusive bound, like every other window on this surface",
        "gateway/src/aira_gateway/reporting/series.py",
        "    while cursor < end:",
        "    while cursor <= end:",
        SERIES,
    ),
    Mutation(
        "US3",
        "a day bucket is named by its day and not by its hour",
        "gateway/src/aira_gateway/reporting/series.py",
        '_WIDTH: dict[str, int] = {"day": 10, "hour": 13}',
        '_WIDTH: dict[str, int] = {"day": 13, "hour": 13}',
        SERIES,
    ),
    Mutation(
        "US4",
        "a granularity too wide to draw is refused by name, not quietly coarsened",
        "gateway/src/aira_gateway/reporting/series.py",
        "        if hours > MAX_BUCKETS:",
        "        if False:",
        SERIES,
    ),
    Mutation(
        "US5",
        "a named granularity is honoured rather than overridden by what auto would choose",
        "gateway/src/aira_gateway/reporting/series.py",
        '    if asked == "day":\n        return "day"',
        "    pass",
        SERIES,
    ),
    Mutation(
        "US6",
        "the tail beyond the colours is folded into one band, never dropped",
        "gateway/src/aira_gateway/reporting/series.py",
        "    return case((named.in_(kept), named), else_=literal(OTHER))",
        "    return named",
        SERIES,
    ),
    Mutation(
        "US7",
        "a band the breakdown calls `(none)` is not folded into `(other)` by the chart",
        "gateway/src/aira_gateway/reporting/series.py",
        '    named = func.coalesce(func.nullif(SPLITS[split], literal("")), literal(NONE))',
        "    named = SPLITS[split]",
        SERIES,
    ),
    Mutation(
        "US8",
        "the series is scoped by the same window and scope as every other figure",
        "gateway/src/aira_gateway/reporting/service.py",
        '            select(bucket.label("bucket"), band.label("key"), *_measures()),\n            scope,',
        '            select(bucket.label("bucket"), band.label("key"), *_measures()),\n            None,',
        SERIES,
    ),
    Mutation(
        "US9",
        "the band order is stable in a period where nothing has a price",
        "gateway/src/aira_gateway/reporting/service.py",
        "            ranked, key=lambda row: (-row.cost_nanos, -row.requests, -row.total_tokens, row.key)",
        "            ranked, key=lambda row: (-row.cost_nanos,)",
        SERIES,
    ),
    Mutation(
        "US10",
        "a report nobody asked a series of does not compute one",
        "gateway/src/aira_gateway/reporting/service.py",
        "            if granularity is not None:",
        "            if granularity is None:",
        SERIES,
    ),
    Mutation(
        "US11",
        "a split this endpoint does not have is refused in the surface's own envelope",
        "gateway/src/aira_gateway/api/reporting/spend.py",
        "    if split not in SPLITS:",
        "    if False:",
        SERIES,
    ),
    Mutation(
        "US17",
        "a request nothing was reported for is counted apart from one with no price",
        "gateway/src/aira_gateway/reporting/service.py",
        "                    & (RequestLog.total_tokens.is_(None))",
        "                    & (RequestLog.total_tokens.isnot(None))",
        SERIES,
    ),
    Mutation(
        "US18",
        "a refusal is not counted as unmetered, so the caveat does not become permanent",
        "gateway/src/aira_gateway/reporting/service.py",
        "    return (RequestLog.outcome == Outcome.SERVED) | (RequestLog.outcome.is_(None))",
        "    return RequestLog.outcome.isnot(None) | RequestLog.outcome.is_(None)",
        SERIES + " gateway/tests/test_reporting.py",
    ),
    Mutation(
        "US14",
        "a person filter actually narrows the window rather than being ignored",
        "gateway/src/aira_gateway/reporting/service.py",
        "        if person:",
        "        if False:",
        SERIES,
    ),
    Mutation(
        "US15",
        "a person is found by the name their credential carried, not by subject alone",
        "gateway/src/aira_gateway/reporting/service.py",
        "            statement = statement.where(_PERSON.__eq__(person))",
        "            statement = statement.where(RequestLog.subject.__eq__(person))",
        SERIES,
    ),
    Mutation(
        "US13",
        "the failed column of an export carries the figure the report actually holds",
        "gateway/src/aira_gateway/reporting/csv_export.py",
        '                safe_cell(row.get("failed_requests", 0)),',
        '                safe_cell(row.get("failed", 0)),',
        CSV_EXPORT,
    ),
    # The embedding-usage defect, reintroduced both ways it has been believed. The fix landed
    # without these, and an integration test then **asserted the defect** for a round: it read
    # "this dialect reports no usage for an embedding" and required the row to carry none.
    Mutation(
        "US19",
        "the usage an embedding upstream reports reaches the accounting",
        "gateway/src/aira_gateway/upstreams/openai/mapping/response.py",
        "        input_tokens=None if reported is None else int(reported),",
        "        input_tokens=None,",
        OPENAI_DIALECT,
    ),
    Mutation(
        "US20",
        "an embedding nobody metered is unknown, not free",
        "gateway/src/aira_gateway/upstreams/openai/mapping/response.py",
        "        input_tokens=None if reported is None else int(reported),",
        "        input_tokens=int(reported or 0),",
        OPENAI_DIALECT,
    ),
    # Releasing (`FRD-137`, `ADR-0030`). The version arithmetic first, then the workflow's
    # shape, then the description — each one leaves a workflow that parses and publishes.
    Mutation(
        "PB1",
        "the newest release is chosen by number, not by the order git happens to list tags in",
        "tools/release_version.py",
        "    return max(found) if found else None",
        "    return found[-1] if found else None",
        "tools/tests/test_the_release_version_comes_from_the_tags.py",
    ),
    Mutation(
        "PB2",
        "a minor bump resets the patch number instead of carrying it over",
        "tools/release_version.py",
        "        return major, minor + 1, 0",
        "        return major, minor + 1, patch",
        "tools/tests/test_the_release_version_comes_from_the_tags.py",
    ),
    Mutation(
        "PB3",
        "a major bump resets both of the numbers below it",
        "tools/release_version.py",
        "        return major + 1, 0, 0",
        "        return major + 1, minor, patch",
        "tools/tests/test_the_release_version_comes_from_the_tags.py",
    ),
    Mutation(
        "PB4",
        "the first release is an early version of something, not a patch to nothing",
        "tools/release_version.py",
        "FIRST = (0, 1, 0)",
        "FIRST = (0, 0, 0)",
        "tools/tests/test_the_release_version_comes_from_the_tags.py",
    ),
    Mutation(
        "PB5",
        "an unknown bump is refused on an unreleased project too, not only on a released one",
        "tools/release_version.py",
        "        if part not in PARTS:\n            raise ValueError(f\"'{part}' is not one of {', '.join(PARTS)}\")\n        return FIRST",  # noqa: E501
        "        return FIRST",
        "tools/tests/test_the_release_version_comes_from_the_tags.py",
    ),
    Mutation(
        "PB6",
        "a pre-release tag is not a release this project bumps from",
        "tools/release_version.py",
        "    match = _RELEASE_TAG.fullmatch(tag.strip())",
        "    match = _RELEASE_TAG.match(tag.strip())",
        "tools/tests/test_the_release_version_comes_from_the_tags.py",
    ),
    # The workflow's shape. Each of these leaves a workflow that parses and publishes.
    Mutation(
        "PB7",
        "nothing is published that the live-stack job did not pass",
        ".github/workflows/ci.yml",
        "    needs: [python, frontend, stack, browser, described]",
        "    needs: [python, frontend, browser, described]",
        "tools/tests/test_a_release_is_published_only_behind_the_gates.py",
    ),
    Mutation(
        "PB8",
        "nothing is published that has not been shown to carry a description",
        ".github/workflows/ci.yml",
        "    needs: [python, frontend, stack, browser, described]",
        "    needs: [python, frontend, stack, browser]",
        "tools/tests/test_a_release_is_published_only_behind_the_gates.py",
    ),
    Mutation(
        "PB30",
        "nothing is published that the browser suite did not pass",
        ".github/workflows/ci.yml",
        "    needs: [python, frontend, stack, browser, described]",
        "    needs: [python, frontend, stack, described]",
        "tools/tests/test_a_release_is_published_only_behind_the_gates.py",
    ),
    Mutation(
        "PB31",
        "a check job added later gates the release, though nobody listed it",
        ".github/workflows/ci.yml",
        "  browser:\n    name: Stack — browser end-to-end (strict data mode",
        "  later:\n    runs-on: ubuntu-latest\n    steps:\n      - run: exit 1\n\n"
        "  browser:\n    name: Stack — browser end-to-end (strict data mode",
        "tools/tests/test_a_release_is_published_only_behind_the_gates.py",
    ),
    Mutation(
        "PB9",
        "the description check reports in seconds rather than behind forty minutes of gates",
        ".github/workflows/ci.yml",
        "    name: The tag says what the release is\n",
        "    name: The tag says what the release is\n    needs: [python]\n",
        "tools/tests/test_a_release_is_published_only_behind_the_gates.py",
    ),
    Mutation(
        "PB10",
        "a release tag starts a release; a loose pattern does not start one for a spike",
        ".github/workflows/ci.yml",
        '    tags: ["v*.*.*"]',
        '    tags: ["v*"]',
        "tools/tests/test_a_release_is_published_only_behind_the_gates.py",
    ),
    Mutation(
        "PB11",
        "only a tag publishes, never an ordinary push",
        ".github/workflows/ci.yml",
        "    if: startsWith(github.ref, 'refs/tags/v')\n    runs-on: ubuntu-latest\n    timeout-minutes: 30",
        "    if: ${{ always() }}\n    runs-on: ubuntu-latest\n    timeout-minutes: 30",
        "tools/tests/test_a_release_is_published_only_behind_the_gates.py",
    ),
    Mutation(
        "PB12",
        "the release jobs see the tags and the history the notes are built from",
        ".github/workflows/ci.yml",
        "      # Full history and tags: the notes quote the commits since the previous release, and\n      # `--depth 1` would make every release look like the first one.\n      - uses: actions/checkout@v5\n        with:\n          fetch-depth: 0",
        "      # Full history and tags: the notes quote the commits since the previous release, and\n      # `--depth 1` would make every release look like the first one.\n      - uses: actions/checkout@v5\n        with:\n          fetch-depth: 1",
        "tools/tests/test_a_release_is_published_only_behind_the_gates.py",
    ),
    Mutation(
        "PB13",
        "a version that is already released is refused rather than republished over",
        ".github/workflows/ci.yml",
        'echo "::error::$TAG is already released; tag a new version instead"\n            exit 1',
        'echo "::warning::$TAG is already released"',
        "tools/tests/test_a_release_is_published_only_behind_the_gates.py",
    ),
    Mutation(
        "PB14",
        "a tag that is not on the default branch is refused",
        ".github/workflows/ci.yml",
        'if ! git merge-base --is-ancestor "${{ github.sha }}" FETCH_HEAD; then\n            echo "::error::$TAG names a commit that is not on main"\n            exit 1\n          fi',
        'if ! git merge-base --is-ancestor "${{ github.sha }}" origin/main; then\n            echo "::error::$TAG names a commit that is not on main"\n            exit 1\n          fi',
        "tools/tests/test_a_release_is_published_only_behind_the_gates.py",
    ),
    Mutation(
        "PB15",
        "a version older than the newest release is refused, so `latest` never moves backwards",
        ".github/workflows/ci.yml",
        'newest="$(uv run python tools/release_version.py --current)"',
        'newest="${{ github.ref_name }}"',
        "tools/tests/test_a_release_is_published_only_behind_the_gates.py",
    ),
    Mutation(
        "PB16",
        "the release notes are published after the images, never before them",
        ".github/workflows/ci.yml",
        "      - uses: docker/setup-buildx-action@v3",
        "      - name: Publish the release notes\n        run: echo early\n\n      - uses: docker/setup-buildx-action@v3",
        "tools/tests/test_a_release_is_published_only_behind_the_gates.py",
    ),
    Mutation(
        "PB17",
        "the notes are the tagger's own words, not a list GitHub generated",
        ".github/workflows/ci.yml",
        '            --notes-file "$RUNNER_TEMP/notes.md"',
        "            --generate-notes",
        "tools/tests/test_a_release_is_published_only_behind_the_gates.py",
    ),
    Mutation(
        "PB18",
        "a release is all three images at one version, never two of them",
        ".github/workflows/ci.yml",
        "/aira-frontend:${{ steps.release.outputs.version }}",
        "/aira-console:${{ steps.release.outputs.version }}",
        "tools/tests/test_a_release_is_published_only_behind_the_gates.py",
    ),
    Mutation(
        "PB19",
        "every published image carries the commit it was built from",
        ".github/workflows/ci.yml",
        "            ghcr.io/${{ steps.release.outputs.owner }}/aira-gateway:sha-${{ github.sha }}\n",
        "",
        "tools/tests/test_a_release_is_published_only_behind_the_gates.py",
    ),
    Mutation(
        "PB20",
        "only the release job may write; the workflow's default stays read-only",
        ".github/workflows/ci.yml",
        "permissions:\n  contents: read",
        "permissions:\n  contents: write",
        "tools/tests/test_a_release_is_published_only_behind_the_gates.py",
    ),
    Mutation(
        "PB21",
        "the registry login uses the built-in token, not a long-lived one somebody stored",
        ".github/workflows/ci.yml",
        "          password: ${{ secrets.GITHUB_TOKEN }}",
        "          password: ${{ secrets.GHCR_TOKEN }}",
        "tools/tests/test_a_release_is_published_only_behind_the_gates.py",
    ),
    Mutation(
        "PB22",
        "a release run is not cancelled between two image pushes",
        ".github/workflows/ci.yml",
        "  cancel-in-progress: ${{ github.ref != 'refs/heads/main' && !startsWith(github.ref, 'refs/tags/') }}",
        "  cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}",
        "tools/tests/test_a_release_is_published_only_behind_the_gates.py",
    ),
    # The description itself. `v0.1.0` shipped with the annotation `Release v0.1.0` and no
    # GitHub Release at all — what a mechanism with nowhere to put a description produces.
    Mutation(
        "PB23",
        "a lightweight tag, which carries no message at all, cannot be released",
        "tools/release_notes.py",
        '    if not text:\n        raise NotDescribed(\n            f"{version} has no annotation.',
        '    if False:\n        raise NotDescribed(\n            f"{version} has no annotation.',
        "tools/tests/test_a_release_says_what_it_is.py",
    ),
    Mutation(
        "PB24",
        "a subject that only restates the version is not a description",
        "tools/release_notes.py",
        "    if not _stripped_subject(subject):",
        "    if False:",
        "tools/tests/test_a_release_says_what_it_is.py",
    ),
    Mutation(
        "PB25",
        "a headline with no body is not a description",
        "tools/release_notes.py",
        "    if not body.strip():",
        "    if False:",
        "tools/tests/test_a_release_says_what_it_is.py",
    ),
    Mutation(
        "PB26",
        "the restatement check sees every spelling of it, not only the one the old job wrote",
        "tools/release_notes.py",
        r'    r"\b(aira|gateway|release|version|tag|v?\d+\.\d+\.\d+|v\d+)\b|[^\w\s]",',
        r'    r"^Release v\d+\.\d+\.\d+$",',
        "tools/tests/test_a_release_says_what_it_is.py",
    ),
    Mutation(
        "PB29",
        "the project's own name is as empty a subject as the word 'release'",
        "tools/release_notes.py",
        r'    r"\b(aira|gateway|release|version|tag|v?\d+\.\d+\.\d+|v\d+)\b|[^\w\s]",',
        r'    r"\b(release|version|tag|v?\d+\.\d+\.\d+|v\d+)\b|[^\w\s]",',
        "tools/tests/test_a_release_says_what_it_is.py",
    ),
    Mutation(
        "PB27",
        "the notes put the tagger's prose before the generated evidence",
        "tools/release_notes.py",
        "        sections = [self.body.strip()]",
        "        sections = []",
        "tools/tests/test_a_release_says_what_it_is.py",
    ),
    Mutation(
        "PB28",
        "a first release's notes are its description, not an empty Commits section",
        "tools/release_notes.py",
        "    if earlier is None:\n        return []",
        '    if earlier is None:\n        return [""]',
        "tools/tests/test_a_release_says_what_it_is.py",
    ),
    # ---- running a published release (2026-10-11, `FRD-138`, `ADR-0031`) -------------------------
    #
    # **Every one of these leaves a stack that comes up healthy.** That is what the overlays are
    # against: a `build:` left in place is a deployment running this checkout under the registry's
    # version number; an address defaulted instead of required is a container reaching for a
    # hostname that is not there; a dependency that merges instead of replacing is a composition
    # Compose refuses outright. None of them is visible in the console, in `/readyz` or in a log
    # that anybody reads on purpose.
    Mutation(
        "IM1",
        "every service that builds an image here is re-pointed at the registry",
        "deploy/compose/docker-compose.release.yml",
        "  gateway-consumer:\n    <<: *gateway\n",
        "",
        "tools/tests/test_a_release_runs_from_the_registry.py",
    ),
    Mutation(
        "IM2",
        "a published image's build is removed, not overridden",
        "deploy/compose/docker-compose.release.yml",
        "  build: !reset null\n  # `missing` for a pinned version",
        "  build: !override {context: ../.., dockerfile: gateway/Dockerfile}\n  # `missing` for a pinned version",
        "tools/tests/test_a_release_runs_from_the_registry.py",
    ),
    Mutation(
        "IM3",
        "a release names its version and never falls back to a moving tag",
        "deploy/compose/docker-compose.release.yml",
        "  image: ${AIRA_IMAGE_REGISTRY:-ghcr.io/inigoimago}/aira-gateway:${AIRA_VERSION:?set AIRA_VERSION to a published version, e.g. v0.1.1 — make current-version prints the newest — or pass RELEASE=vX.Y.Z to any make up target}",
        "  image: ${AIRA_IMAGE_REGISTRY:-ghcr.io/inigoimago}/aira-gateway:${AIRA_VERSION:-latest}",
        "tools/tests/test_a_release_runs_from_the_registry.py",
    ),
    Mutation(
        "IM4",
        "the registry a fork publishes to is a variable, not this owner's",
        "deploy/compose/docker-compose.release.yml",
        "  image: ${AIRA_IMAGE_REGISTRY:-ghcr.io/inigoimago}/aira-gateway:${AIRA_VERSION:?set AIRA_VERSION to a published version, e.g. v0.1.1 — make current-version prints the newest — or pass RELEASE=vX.Y.Z to any make up target}",
        "  image: ghcr.io/inigoimago/aira-gateway:${AIRA_VERSION:?name a version}",
        "tools/tests/test_a_release_runs_from_the_registry.py",
    ),
    Mutation(
        "IM5",
        "the default registry path is one GHCR accepts, which excludes upper case",
        "deploy/compose/docker-compose.release.yml",
        "  image: ${AIRA_IMAGE_REGISTRY:-ghcr.io/inigoimago}/aira-gateway:${AIRA_VERSION:?set AIRA_VERSION to a published version, e.g. v0.1.1 — make current-version prints the newest — or pass RELEASE=vX.Y.Z to any make up target}",
        "  image: ${AIRA_IMAGE_REGISTRY:-ghcr.io/InigoImago}/aira-gateway:${AIRA_VERSION:?name a version}",
        "tools/tests/test_a_release_runs_from_the_registry.py",
    ),
    Mutation(
        "IM6",
        "the images a deployment pulls are the images a release pushes",
        "deploy/compose/docker-compose.release.yml",
        "/aira-frontend:${AIRA_VERSION",
        "/aira-console:${AIRA_VERSION",
        "tools/tests/test_a_release_runs_from_the_registry.py",
    ),
    Mutation(
        "IM7",
        "the demo's seed runs the published image too, although `extends` hides it",
        "deploy/compose/docker-compose.release.yml",
        '    <<: *management\n    profiles: ["demo"]',
        "    <<: *management",
        "tools/tests/test_a_release_runs_from_the_registry.py",
    ),
    Mutation(
        "IM20",
        "the release overlay is layered after the showcase, so the seed's extends cannot win",
        "Makefile",
        "COMPOSE_FULL := docker compose $(INFRA_F) $(APPS_F) $(SHOWCASE_F) $(RELEASE_F)",
        "COMPOSE_FULL := docker compose $(INFRA_F) $(APPS_F) $(RELEASE_F) $(SHOWCASE_F)",
        "tools/tests/test_a_release_runs_from_the_registry.py",
    ),
    Mutation(
        "IM8",
        "a pull policy cannot fall back to building the image instead",
        "deploy/compose/docker-compose.release.yml",
        "  pull_policy: ${AIRA_PULL_POLICY:-missing}\n\nx-management-image",
        "  pull_policy: build\n\nx-management-image",
        "tools/tests/test_a_release_runs_from_the_registry.py",
    ),
    Mutation(
        "IM9",
        "the integrated composition starts none of the deployment's infrastructure",
        "deploy/compose/docker-compose.external.yml",
        "services:\n  # ---- one-shot setup",
        "services:\n  postgres:\n    image: postgres:17-alpine\n\n  # ---- one-shot setup",
        "tools/tests/test_a_release_runs_from_the_registry.py",
    ),
    Mutation(
        "IM10",
        "a replaced dependency replaces rather than merges, so the infra ones go",
        "deploy/compose/docker-compose.external.yml",
        "    # `keycloak` and `redis` go; the schema this process reads does not.\n    depends_on: !override",
        "    # `keycloak` and `redis` go; the schema this process reads does not.\n    depends_on:",
        "tools/tests/test_a_release_runs_from_the_registry.py",
    ),
    Mutation(
        "IM11",
        "an address that cannot be guessed is required, not defaulted to a container name",
        "deploy/compose/docker-compose.external.yml",
        'AIRA_OIDC_JWKS_URI: "${AIRA_OIDC_JWKS_URI:?the JWKS URL this container can reach. Not the issuer: the issuer is what a browser saw, this is what the gateway fetches keys from}"',
        'AIRA_OIDC_JWKS_URI: "${AIRA_OIDC_JWKS_URI:-http://keycloak:8080/realms/aira/protocol/openid-connect/certs}"',
        "tools/tests/test_a_release_runs_from_the_registry.py",
    ),
    Mutation(
        "IM12",
        "a deliberate none stays a valid answer for the two settings that have one",
        "deploy/compose/docker-compose.external.yml",
        '"${AIRA_REDIS_URL?redis://host:port/db',
        '"${AIRA_REDIS_URL:?redis://host:port/db',
        "tools/tests/test_a_release_runs_from_the_registry.py",
    ),
    Mutation(
        "IM19",
        "the integrated composition is the same project the stack's targets name",
        "deploy/compose/docker-compose.external.yml",
        "name: ${AIRA_STACK:-aira}\n\n# The addresses each plane",
        "# The addresses each plane",
        "tools/tests/test_a_release_runs_from_the_registry.py",
    ),
    Mutation(
        "IM13",
        "the applications join the network their addresses resolve on",
        "deploy/compose/docker-compose.external.yml",
        "  aira:\n    name: ${AIRA_STACK:-aira}\n    driver: bridge",
        "  aira:\n    name: aira-apps\n    driver: bridge",
        "tools/tests/test_a_release_runs_from_the_registry.py",
    ),
    Mutation(
        "IM14",
        "creating the topics is opt-in against a broker that is not ours",
        "deploy/compose/docker-compose.external.yml",
        '  kafka-topics:\n    profiles: ["create-topics"]',
        "  kafka-topics:",
        "tools/tests/test_a_release_runs_from_the_registry.py",
    ),
    Mutation(
        "IM15",
        "the topic creator uses the broker the applications were configured with",
        "deploy/compose/docker-compose.apps.yml",
        '--bootstrap-server "$$AIRA_KAFKA_BOOTSTRAP_SERVERS"',
        "--bootstrap-server kafka:9092",
        "tools/tests/test_a_release_runs_from_the_registry.py",
    ),
    Mutation(
        "IM16",
        "running a release drops the --build that would rebuild it from this checkout",
        "Makefile",
        "RELEASE_F := -f $(COMPOSE_DIR)/docker-compose.release.yml\nBUILD_FLAG :=",
        "RELEASE_F := -f $(COMPOSE_DIR)/docker-compose.release.yml",
        "tools/tests/test_a_release_runs_from_the_registry.py",
    ),
    Mutation(
        "IM17",
        "the integrated target starts the applications and not a second infrastructure",
        "Makefile",
        "COMPOSE_INTEGRATED := docker compose $(APPS_F) $(EXTERNAL_F) $(RELEASE_F)",
        "COMPOSE_INTEGRATED := docker compose $(INFRA_F) $(APPS_F) $(EXTERNAL_F) $(RELEASE_F)",
        "tools/tests/test_a_release_runs_from_the_registry.py",
    ),
    Mutation(
        "IM18",
        "the Makefile's own address source runs under the interpreter the Makefile has",
        "tools/compose_files.py",
        "from pathlib import Path\n\nROOT",
        "from pathlib import Path\n\nimport yaml\n\nROOT",
        "tools/tests/test_one_owner_for_the_stack_addresses.py",
    ),
    # ---- a release's notes are the tag's own (2026-10-11, `FRD-137`, `ADR-0030`) ----------------
    #
    # **`v0.1.1` was published carrying a commit message.** `%(contents)` of a ref that points at a
    # commit is that commit's message, not nothing — so rule 1 never ran, `actions/checkout` handed
    # the job a flattened tag, and a plausible description with a subject and a body sailed through
    # all four rules. Every mutation here restores one of the ways that stayed invisible.
    Mutation(
        "RT1",
        "a ref that is not an annotated tag has no annotation to publish",
        "tools/release_notes.py",
        '    kind = _git("for-each-ref", f"refs/tags/{version}", "--format=%(objecttype)").strip()\n    if kind != "tag":\n        return ""',
        '    kind = "tag"\n    if kind != "tag":\n        return ""',
        "tools/tests/test_a_release_says_what_it_is.py",
    ),
    Mutation(
        "RT2",
        "a template nobody filled in is not a description",
        "tools/release_notes.py",
        "    left = next((prompt for prompt in placeholders() if prompt in flattened), None)",
        "    left = None",
        "tools/tests/test_a_release_says_what_it_is.py",
    ),
    Mutation(
        "RT3",
        "the placeholders rule 5 refuses are the template's own, not a list beside it",
        "tools/release_notes.py",
        '    found = re.findall(r"<[^<>]+>", TEMPLATE.read_text(), re.DOTALL)',
        '    found = ["<one line: what this release is>"]',
        "tools/tests/test_a_release_says_what_it_is.py",
    ),
    Mutation(
        "RT4",
        "the release title goes through the rules that check the body",
        "tools/release_notes.py",
        "    return read(annotation(version), version=version).subject",
        '    return _git("for-each-ref", f"refs/tags/{version}", "--format=%(contents:subject)").strip()',
        "tools/tests/test_a_release_says_what_it_is.py",
    ),
    Mutation(
        "RT5",
        "the tag object is restored before the description check reads it",
        ".github/workflows/ci.yml",
        '      - name: Restore the annotated tag object\n        run: |\n          git fetch --force origin "refs/tags/$TAG:refs/tags/$TAG"\n          kind="$(git cat-file -t "refs/tags/$TAG")"\n          if [ "$kind" != tag ]; then\n            echo "::error::refs/tags/$TAG is a $kind, not an annotated tag"\n            exit 1\n          fi\n\n      # The same command `make release-check VERSION=…` runs',
        "      # The same command `make release-check VERSION=…` runs",
        "tools/tests/test_a_release_is_published_only_behind_the_gates.py",
    ),
    Mutation(
        "RT6",
        "a restored tag is asserted to be one, not assumed",
        ".github/workflows/ci.yml",
        '          kind="$(git cat-file -t "refs/tags/$TAG")"\n          if [ "$kind" != tag ]; then\n            echo "::error::refs/tags/$TAG is a $kind, not an annotated tag"\n            exit 1\n          fi\n\n      # The same command `make release-check VERSION=…` runs',
        "      # The same command `make release-check VERSION=…` runs",
        "tools/tests/test_a_release_is_published_only_behind_the_gates.py",
    ),
    Mutation(
        "RT7",
        "the release title comes from the tool and not from a second git call",
        ".github/workflows/ci.yml",
        '--title "$version — $(uv run python tools/release_notes.py --subject "$version")" \\',
        '--title "$version — $(git tag -l --format=\'%(contents:subject)\' "$version")" \\',
        "tools/tests/test_a_release_is_published_only_behind_the_gates.py",
    ),
    Mutation(
        "RT8",
        "the editor opens on the template rather than on an empty buffer",
        "Makefile",
        "\t@sed 's/vX\\.Y\\.Z/$(VERSION)/g' docs/RELEASE-TEMPLATE.txt > .git/AIRA_RELEASE_NOTES\n\t@$${GIT_EDITOR:-$${EDITOR:-$${VISUAL:-vi}}} .git/AIRA_RELEASE_NOTES\n\tgit tag -a \"$(VERSION)\" -F .git/AIRA_RELEASE_NOTES --cleanup=strip",
        '\tgit tag -a "$(VERSION)"',
        "tools/tests/test_a_release_is_published_only_behind_the_gates.py",
    ),
    Mutation(
        "RT9",
        "the template's guidance is stripped and never published as notes",
        "Makefile",
        'git tag -a "$(VERSION)" -F .git/AIRA_RELEASE_NOTES --cleanup=strip',
        'git tag -a "$(VERSION)" -F .git/AIRA_RELEASE_NOTES',
        "tools/tests/test_a_release_is_published_only_behind_the_gates.py",
    ),
    # ---- every model access is a chain (2026-10-11, `FRD-302`, `ADR-0032`) ---------------------
    #
    # **Two model accesses had no chain and the reasons had outlived themselves.** A streamed
    # request resolved one adapter, because the chain returns a finished response; a pipeline step
    # asked the one model its configuration named, so a `pii_filter` whose redactor was down
    # refused every request of its use case. Each mutation here puts one of those back.
    Mutation(
        "MF1",
        "a stream tries the next candidate when one fails before its first chunk",
        "gateway/src/aira_gateway/pipeline/dispatch.py",
        "        except UpstreamError as exc:\n            last_error = exc\n            failed.append(Attempted(model, _why(exc)))\n            await _closed(chunks, model)",
        "        except UpstreamError:\n            raise",
        "gateway/tests/test_a_stream_falls_back_until_a_chunk_is_out.py",
    ),
    Mutation(
        "MF2",
        "a stream that has delivered a chunk is committed to the model that sent it",
        "gateway/src/aira_gateway/pipeline/dispatch.py",
        "    try:\n        yield first\n        async for chunk in rest:\n            yield chunk",
        "    try:\n        yield first\n        async for chunk in rest:\n            yield chunk\n    except UpstreamError:\n        return",
        "gateway/tests/test_a_stream_falls_back_until_a_chunk_is_out.py",
    ),
    Mutation(
        "MF3",
        "the stream of a candidate being passed over is closed",
        "gateway/src/aira_gateway/pipeline/dispatch.py",
        '    close = getattr(chunks, "aclose", None)\n    if close is None:\n        return',
        '    close = getattr(chunks, "aclose", None)\n    if close is not None:\n        return',
        "gateway/tests/test_a_stream_falls_back_until_a_chunk_is_out.py",
    ),
    Mutation(
        "MF4",
        "a stream that ends with no chunk at all has answered",
        "gateway/src/aira_gateway/pipeline/dispatch.py",
        "        except StopAsyncIteration:\n            return Streaming(model, index, _nothing(), skipped, failed, provider, addressed)",
        "        except StopAsyncIteration:\n            continue",
        "gateway/tests/test_a_stream_falls_back_until_a_chunk_is_out.py",
    ),
    Mutation(
        "MF5",
        "every candidate of either chain is asked the conditions",
        "gateway/src/aira_gateway/pipeline/dispatch.py",
        "            refusal = await permits(model)\n            if refusal is not None:",
        "            refusal = await permits(model)\n            if refusal is None:",
        "gateway/tests/test_a_stream_falls_back_until_a_chunk_is_out.py",
    ),
    Mutation(
        "MF6",
        "a streamed answer is recorded under the model that produced it",
        "gateway/src/aira_gateway/api/gemini/routes.py",
        "    served = streamed.model",
        "    served = canonical.model",
        "gateway/tests/test_a_stream_falls_back_until_a_chunk_is_out.py",
    ),
    Mutation(
        "MF7",
        "the predecessor's stream also names the model that answered",
        "gateway/src/aira_gateway/api/kira/routes.py",
        "    served = streamed.model",
        "    served = canonical.model",
        "gateway/tests/test_a_stream_falls_back_until_a_chunk_is_out.py",
    ),
    Mutation(
        "MF8",
        "a candidate that was tried and failed is on the audit row",
        "gateway/src/aira_gateway/audit.py",
        '        for entry in failed:\n            self.decisions.append(\n                {"step": "dispatch", "action": "failed", "to": entry.model, "why": entry.reason}\n            )',
        "        return",
        "gateway/tests/test_a_stream_falls_back_until_a_chunk_is_out.py",
    ),
    Mutation(
        "MF9",
        "an allowance reserved for a request that was never dispatched is given back",
        "gateway/src/aira_gateway/api/serving/accounting.py",
        "    await release_reservation(request)\n    if attribution_of(request) is None:",
        "    if attribution_of(request) is None:",
        "gateway/tests/test_ratelimit_routes.py",
    ),
    Mutation(
        "MF10",
        "a step asks the chain its own configuration names",
        "gateway/src/aira_gateway/pipeline/engine/steps.py",
        '        named = [config.get("model"), *(config.get("fallback_models") or [])]',
        '        named = [config.get("model")]',
        "gateway/tests/test_a_step_asks_a_chain_of_models.py",
    ),
    Mutation(
        "MF11",
        "a step's chain stops at the first model it reached",
        "gateway/src/aira_gateway/pipeline/engine/steps.py",
        "            if answer.reached:\n                break",
        "            if not answer.reached:\n                break",
        "gateway/tests/test_a_step_asks_a_chain_of_models.py",
    ),
    Mutation(
        "MF12",
        "a step's chain is asked once per model, however often it is written",
        "gateway/src/aira_gateway/pipeline/engine/steps.py",
        "            if not model or model in seen:",
        "            if not model:",
        "gateway/tests/test_a_step_asks_a_chain_of_models.py",
    ),
    Mutation(
        "MF13",
        "the gateway re-applies Management's bound on a step's chain",
        "gateway/src/aira_gateway/pipeline/config.py",
        "            str(name)[:MAX_MODEL_LENGTH] for name in chain[:MAX_FALLBACK_MODELS] if name",
        "            str(name) for name in chain if name",
        "gateway/tests/test_a_step_asks_a_chain_of_models.py",
    ),
    Mutation(
        "MF14",
        "every model a step's chain names is checked against the use case's release",
        "management/backend/src/aira_management/apps/pipelines/serializers.py",
        '        for fallback in config.get("fallback_models") or []:\n            if fallback:\n                named.append(str(fallback))',
        "        pass",
        "management/backend/tests/test_pipelines.py tools/tests/test_both_planes_find_the_same_models_in_a_pipeline.py",
    ),
    Mutation(
        "MF15",
        "a streamed chain with nothing to offer is a configuration fault, not an outage",
        "gateway/src/aira_gateway/pipeline/dispatch.py",
        "    return NoCapableModel(skipped)",
        '    return UpstreamError("No provider available.")',
        "gateway/tests/test_a_stream_falls_back_until_a_chunk_is_out.py",
    ),
    Mutation(
        "MF16",
        "a streamed attempt is marked with the model it reached for",
        "gateway/src/aira_gateway/pipeline/dispatch.py",
        '            with model_call_span(model, purpose="serve"):\n                chunks',
        '            with model_call_span(request.model, purpose="serve"):\n                chunks',
        "gateway/tests/test_every_model_call_says_what_it_is.py gateway/tests/test_a_stream_falls_back_until_a_chunk_is_out.py",
    ),
    Mutation(
        "MF17",
        "a caller who stops reading releases the stream that answered them",
        "gateway/src/aira_gateway/pipeline/dispatch.py",
        "    finally:\n        await _closed(rest, model)",
        "    finally:\n        pass",
        "gateway/tests/test_a_stream_falls_back_until_a_chunk_is_out.py gateway/tests/test_upstream_pools_are_closed.py",
    ),
    Mutation(
        "MF18",
        "a dry run asks the installation's approval of the models a step's chain would call",
        "gateway/src/aira_gateway/api/pipeline.py",
        '            named.append(str(config["model"]))\n            named.extend(str(name) for name in config.get("fallback_models") or [] if name)',
        '            named.append(str(config["model"]))',
        "gateway/tests/test_pipeline_dryrun.py",
    ),
    # ---- a use case may withhold its content from itself (2026-10-11, `FRD-622` FR-1b) --------
    #
    # The fourth answer to "who may read a stored prompt": nobody in the use case, its own
    # administrators included. Every mutation here is a way the mode could look configured and
    # read as something weaker — which is the only failure mode that matters for a setting whose
    # whole value is that it holds.
    Mutation(
        "WR1",
        "a use case that leaves content to oversight refuses everybody inside it",
        "gateway/src/aira_gateway/payloads.py",
        "    if visibility == PayloadVisibility.OVERSIGHT_ONLY:\n        return PayloadRefusal.OVERSIGHT_ONLY",
        "    if visibility is None:\n        return PayloadRefusal.OVERSIGHT_ONLY",
        "gateway/tests/test_payload_access.py",
    ),
    Mutation(
        "WR2",
        "the oversight mode is asked before the use case's own administrator is",
        "gateway/src/aira_gateway/payloads.py",
        '    if visibility == PayloadVisibility.OVERSIGHT_ONLY:\n        return PayloadRefusal.OVERSIGHT_ONLY\n\n    role = await grant_role_in(session, principal, row.use_case)\n    if role == GrantRole.ADMIN.value:\n        return "use_case_admin"',
        '    role = await grant_role_in(session, principal, row.use_case)\n    if role == GrantRole.ADMIN.value:\n        return "use_case_admin"\n\n    if visibility == PayloadVisibility.OVERSIGHT_ONLY:\n        return PayloadRefusal.OVERSIGHT_ONLY',
        "gateway/tests/test_payload_access.py",
    ),
    Mutation(
        "WR3",
        "the mode hides the content and leaves the use case its own rows",
        "gateway/src/aira_gateway/payloads.py",
        # Re-anchored when `people_restricted_use_cases` arrived and the walk became shared: the
        # property is now **which visibilities each question narrows for**, and this is the one
        # that must narrow for `own` alone.
        "    return await _narrowed(\n        session,\n        principal,\n        (PayloadVisibility.OWN_REQUESTS_ONLY,),",
        "    return await _narrowed(\n        session,\n        principal,\n        (PayloadVisibility.OWN_REQUESTS_ONLY, PayloadVisibility.OVERSIGHT_ONLY),",
        "gateway/tests/test_payload_access.py",
    ),
    Mutation(
        "WR4",
        "an event from an older Management is still read through its boolean",
        "gateway/src/aira_gateway/consumer/apply.py",
        '    if payload.get("restrict_members_to_own_requests"):\n        return PayloadVisibility.OWN_REQUESTS_ONLY.value',
        "    if False:\n        return PayloadVisibility.OWN_REQUESTS_ONLY.value",
        "gateway/tests/test_consumer_apply.py",
    ),
    Mutation(
        "WR5",
        "a visibility this gateway does not know is read as the narrowest of the three",
        "gateway/src/aira_gateway/consumer/apply.py",
        "        return PayloadVisibility.OVERSIGHT_ONLY.value",
        "        return PayloadVisibility.EVERY_MEMBER.value",
        "gateway/tests/test_consumer_apply.py",
    ),
    Mutation(
        "WR6",
        "a use case nobody configured is readable by its members, not narrower",
        "gateway/src/aira_gateway/db/models/read_models.py",
        '    payload_visibility: Mapped[str] = mapped_column(String(16), default="members")',
        '    payload_visibility: Mapped[str] = mapped_column(String(16), default="oversight")',
        "gateway/tests/test_content_is_erased_whoever_may_read_it.py",
    ),
    Mutation(
        "WR7",
        "erasure does not look at who may read it",
        "gateway/src/aira_gateway/retention.py",
        "        return {\n            slug: (None if not store else max(1, days or self._default_retention_days))\n            for slug, days, store in result.all()\n        }",
        '        rows = result.all()\n        hidden = set(\n            (\n                await session.execute(\n                    select(UseCaseRead.slug).where(UseCaseRead.payload_visibility == "oversight")\n                )\n            )\n            .scalars()\n            .all()\n        )\n        return {\n            slug: (\n                None\n                if not store\n                else 3650\n                if slug in hidden\n                else max(1, days or self._default_retention_days)\n            )\n            for slug, days, store in rows\n        }',
        "gateway/tests/test_content_is_erased_whoever_may_read_it.py",
    ),
    Mutation(
        "WR8",
        "the register names which of the three answers a use case chose",
        "gateway/src/aira_gateway/reporting/register.py",
        "            content_visibility=effective_visibility(\n"
        "                row.payload_visibility, strict_data=self._strict_data\n"
        "            ),",
        '            content_visibility="members",',
        "gateway/tests/test_register.py",
    ),
    Mutation(
        "WR9",
        "a use case's content setting narrows its figures per person",
        "gateway/src/aira_gateway/api/reporting/spend.py",
        "    limited = await _people_limited_to(request, principal, scope)",
        "    limited = None",
        "gateway/tests/test_whose_figures_a_member_sees.py",
    ),
    Mutation(
        "WR10",
        "an administrator of a use case on `own` still sees everybody's figures",
        "gateway/src/aira_gateway/payloads.py",
        "        session, principal, (PayloadVisibility.OWN_REQUESTS_ONLY,), administrators_exempt=True",
        "        session, principal, (PayloadVisibility.OWN_REQUESTS_ONLY,), administrators_exempt=False",
        "gateway/tests/test_whose_figures_a_member_sees.py",
    ),
    Mutation(
        "WR11",
        "`oversight` withholds the figures from the use case's own administrators too",
        "gateway/src/aira_gateway/payloads.py",
        "        session, principal, (PayloadVisibility.OVERSIGHT_ONLY,), administrators_exempt=False",
        "        session, principal, (PayloadVisibility.OVERSIGHT_ONLY,), administrators_exempt=True",
        "gateway/tests/test_whose_figures_a_member_sees.py",
    ),
    Mutation(
        "WR12",
        "withholding who spent it does not change what the use case spent",
        "gateway/src/aira_gateway/reporting/service.py",
        "            totals = await self._totals(session, scope, start, end, person)",
        "            totals = await self._totals(session, scope, start, end, person or people_limited_to)",
        "gateway/tests/test_whose_figures_a_member_sees.py",
    ),
    Mutation(
        "WR13",
        "the narrowing covers both splits keyed on a human, not one of them",
        "gateway/src/aira_gateway/reporting/service.py",
        '                    ("member", RequestLog.subject, True),',
        '                    ("member", RequestLog.subject, False),',
        "gateway/tests/test_whose_figures_a_member_sees.py",
    ),
    Mutation(
        "WR14",
        "the report says whether it withheld anybody, so the console does not guess",
        "gateway/src/aira_gateway/api/reporting/spend.py",
        '    report["people_limited"] = limited is not None',
        '    report["people_limited"] = False',
        "gateway/tests/test_whose_figures_a_member_sees.py",
    ),
    # ---- a caller sees their own allowance (2026-10-11, `FRD-139`) ----------------------------
    #
    # Agentic coding spends money in a loop, and the only figure worth showing a developer is the
    # one that will refuse them. Each mutation here is a way the answer could look right and be
    # about somebody else, about nothing, or about a limit nobody enforces.
    Mutation(
        "AL1",
        "a caller's allowance is their own counter, not the row an unbound reader gets",
        "gateway/src/aira_gateway/api/usage.py",
        '    allowances = await service.usage(use_case or "", subject=person) if use_case else []',
        '    allowances = await service.usage(use_case or "", subject=None) if use_case else []',
        "gateway/tests/test_a_caller_sees_their_own_allowance.py",
    ),
    Mutation(
        "AL2",
        "a dimension a budget does not cap is absent rather than zero",
        "gateway/src/aira_gateway/budgets/service.py",
        "    return None if limit is None else limit - used",
        "    return 0 if limit is None else limit - used",
        "gateway/tests/test_a_caller_sees_their_own_allowance.py",
    ),
    Mutation(
        "AL3",
        "a settle past the limit is reported as it is, not clamped to nothing left",
        "gateway/src/aira_gateway/budgets/service.py",
        "    return None if limit is None else limit - used",
        "    return None if limit is None else max(0, limit - used)",
        "gateway/tests/test_a_caller_sees_their_own_allowance.py",
    ),
    Mutation(
        "AL4",
        "a period ends at the next period's first moment, which is when the counter resets",
        "gateway/src/aira_gateway/budgets/keys.py",
        "        return (moment + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)",
        "        return moment.replace(hour=23, minute=59, second=59, microsecond=0)",
        "gateway/tests/test_a_caller_sees_their_own_allowance.py",
    ),
    Mutation(
        "AL7",
        "the use case an allowance is about comes from the surface's own resolver",
        "gateway/src/aira_gateway/api/usage.py",
        "    use_case = attribution.use_case or None",
        "    use_case = None",
        "gateway/tests/test_a_caller_sees_their_own_allowance.py",
    ),
    Mutation(
        "AL8",
        "the amounts carry the unit the installation accounts in",
        "gateway/src/aira_gateway/api/usage.py",
        '            "currency": settings_of(request).currency,',
        '            "currency": "USD",',
        "gateway/tests/test_a_caller_sees_their_own_allowance.py",
    ),
    Mutation(
        "AL5",
        "the answer says whether a limit is enforced at all",
        "gateway/src/aira_gateway/api/usage.py",
        # Anchored with the line above it: the console's budget view now carries the same field in
        # the same words, and an ambiguous anchor stops the harness rather than mutating one of two.
        '            "person": person,\n            "enforced": settings_of(request).enforce_budgets,',
        '            "enforced": True,',
        "gateway/tests/test_a_caller_sees_their_own_allowance.py",
    ),
    Mutation(
        "AL6",
        "the figures beside the allowance are the caller's own, not the use case's",
        "gateway/src/aira_gateway/api/usage.py",
        '(use_case,) if use_case else (), start, end, person=person or ""',
        '(use_case,) if use_case else (), start, end, person=""',
        "gateway/tests/test_a_caller_sees_their_own_allowance.py",
    ),
    # ---- the `/budget` command ships as a prompt (2026-10-11, `FRD-139` §5) -------------------
    #
    # A command file is text an agent reads, so nothing in the compiler or the type checker is
    # watching it. Both mutations are what rot looks like: a route renamed elsewhere, and the one
    # instruction that stops a model answering from the template instead of from the gateway.
    Mutation(
        "CMD1",
        "the shipped command names a route this gateway serves",
        "tools/opencode/command/budget.md",
        "/v1beta/me/budget",
        "/v1beta/me/allowance",
        "tools/tests/test_the_opencode_command_asks_for_a_real_endpoint.py",
    ),
    Mutation(
        "CMD2",
        "the command orders a refusal, because a model that cannot run it will otherwise invent",
        "tools/opencode/command/budget.md",
        "`I could not read the budget.`",
        "whatever you can determine.",
        "tools/tests/test_the_opencode_command_asks_for_a_real_endpoint.py",
    ),
    # ---- unpriced is not unlimited (2026-10-12, `FRD-610` §3.2) ------------------------------
    #
    # A cost limit cannot bound what it cannot count. Each mutation is a way the rule could be
    # absent, too broad, or scoped to the wrong budgets — and the broad ones matter as much as the
    # absent one: a model refused where no money is capped is a model taken away for nothing.
    Mutation(
        "UP1",
        "an unpriced model is refused where a cost limit binds the request",
        "gateway/src/aira_gateway/requirements.py",
        "        if await self._pricing.price_for(model) is not None:\n            return None",
        "        if True:\n            return None",
        "gateway/tests/test_an_unpriced_model_under_a_cost_limit.py",
    ),
    Mutation(
        "UP2",
        "the refusal needs a cost limit; an unpriced model is otherwise none of its business",
        "gateway/src/aira_gateway/requirements.py",
        "        if not await self._cost_limited():\n            return None",
        "        if False:\n            return None",
        "gateway/tests/test_an_unpriced_model_under_a_cost_limit.py",
    ),
    Mutation(
        "UP3",
        "a *cost* limit binds it, not any limit — tokens and requests count unpriced traffic",
        "gateway/src/aira_gateway/budgets/service.py",
        "        return any(budget.enabled and budget.limit_cost_nanos is not None for budget in budgets)",
        "        return any(budget.enabled for budget in budgets)",
        "gateway/tests/test_an_unpriced_model_under_a_cost_limit.py",
    ),
    Mutation(
        "UP4",
        "with enforcement off nothing is capped, so nothing is refused for being uncountable",
        "gateway/src/aira_gateway/budgets/service.py",
        "        if not self._enforce:\n            return False",
        "        if False:\n            return False",
        "gateway/tests/test_an_unpriced_model_under_a_cost_limit.py",
    ),
    Mutation(
        "UP5",
        "the switch lets an installation accept the gap knowingly",
        "gateway/src/aira_gateway/api/serving/conditions.py",
        "    if settings.enforce_priced_models:",
        "    if True:",
        "gateway/tests/test_an_unpriced_model_under_a_cost_limit.py",
    ),
    Mutation(
        "UP6",
        "the mock is not a model, so governing its price is theatre",
        "gateway/src/aira_gateway/requirements.py",
        "        if _is_test_double(self._registry, model):\n            return None\n        if await self._pricing.price_for(model) is not None:",
        "        if False:\n            return None\n        if await self._pricing.price_for(model) is not None:",
        "gateway/tests/test_an_unpriced_model_under_a_cost_limit.py",
    ),
    # ---- a limit that stops nothing says so (2026-10-11, `FRD-610` §3.3) ---------------------
    #
    # Enforcement off leaves every figure, bar and warning in place and refuses nothing, which is
    # the shape of a working control. Both console-facing answers carry the fact, from the plane
    # that enforces — Management has a copy with no authority over it.
    Mutation(
        "EN1",
        "the console's budget view is told whether budgets refuse at all",
        "gateway/src/aira_gateway/api/usage.py",
        '            "usage": figures,\n            "enforced": settings_of(request).enforce_budgets,',
        '            "usage": figures,\n            "enforced": True,',
        "gateway/tests/test_budget_routes.py",
    ),
    Mutation(
        "EN2",
        "the reporting page is told too, because the installation's own budget is edited there",
        "gateway/src/aira_gateway/api/reporting/spend.py",
        '    report["enforced"] = settings_of(request).enforce_budgets',
        '    report["enforced"] = True',
        "gateway/tests/test_reporting.py",
    ),
    # ---- the budget plugin, which shows the figure unasked (2026-10-11, `FRD-139` §5) ---------
    #
    # JavaScript running inside a third-party client, so neither mypy nor `ng build` looks at it.
    # Each property below is a way the display could be reassuring and wrong, or correct and
    # unbearable. They are noticed by `node --test`, bridged into pytest by
    # `test_the_opencode_plugin_holds_its_properties.py` so this harness can reach them at all.
    Mutation(
        "OB1",
        "the first reading is always shown, so nobody has to know the figure exists",
        "tools/opencode/plugin/aira-budget.js",
        "  if (!previous) return true;",
        "  if (!previous) return false;",
        "tools/tests/test_the_opencode_plugin_holds_its_properties.py",
    ),
    Mutation(
        "OB2",
        "a dimension with no limit, or a limit of zero, is not a limit",
        "tools/opencode/plugin/aira-budget.js",
        "      if (limit === null || limit <= 0) continue;",
        "      if (limit === null) continue;",
        "tools/tests/test_the_opencode_plugin_holds_its_properties.py",
    ),
    Mutation(
        "OB3",
        "the dimension shown is the one that will refuse first, not the first one listed",
        "tools/opencode/plugin/aira-budget.js",
        "  return all.reduce((worst, one) =>\n    one.fraction > worst.fraction ? one : worst,\n  );",
        "  return all[0];",
        "tools/tests/test_the_opencode_plugin_holds_its_properties.py",
    ),
    Mutation(
        "OB4",
        "the warning arrives with a fifth of the allowance left, not at the refusal",
        "tools/opencode/plugin/aira-budget.js",
        "const WARN_AT = 0.8;",
        "const WARN_AT = 0.999;",
        "tools/tests/test_the_opencode_plugin_holds_its_properties.py",
    ),
    Mutation(
        "OB5",
        "a limit that stops nothing is labelled as not enforced",
        "tools/opencode/plugin/aira-budget.js",
        '    enforced ? null : "not enforced",',
        "    null,",
        "tools/tests/test_the_opencode_plugin_holds_its_properties.py",
    ),
    Mutation(
        "OB6",
        "a broken setup is said once, not after every turn",
        "tools/opencode/plugin/aira-budget.js",
        "    if (complaint === reason) return;",
        "    if (false) return;",
        "tools/tests/test_the_opencode_plugin_holds_its_properties.py",
    ),
    Mutation(
        "OB8",
        "the shipped plugin exports one thing, because every export is called as a plugin",
        "tools/opencode/plugin/aira-budget.js",
        "export const server = async ({ client }) => {",
        'export const id = "aira-budget";\nexport const server = async ({ client }) => {',
        "tools/tests/test_the_opencode_plugin_holds_its_properties.py",
    ),
    Mutation(
        "OB9",
        "loading the plugin cannot block OpenCode's bootstrap",
        "tools/opencode/plugin/aira-budget.js",
        "  void show(true).catch(() => {});",
        "  await show(true);",
        "tools/tests/test_the_opencode_plugin_holds_its_properties.py",
    ),
    Mutation(
        "OB10",
        "a new session is shown the figure even when it has not changed",
        "tools/opencode/plugin/aira-budget.js",
        '      if (event?.type === "session.created") {\n        await show(true);\n        return;\n      }',
        '      if (event?.type === "session.created") {\n        await show(false);\n        return;\n      }',
        "tools/tests/test_the_opencode_plugin_holds_its_properties.py",
    ),
    Mutation(
        "OB7",
        "a budget read cannot hang the session it is decorating",
        "tools/opencode/plugin/aira-budget.js",
        "      signal: AbortSignal.timeout(TIMEOUT_MS),",
        "",
        "tools/tests/test_the_opencode_plugin_holds_its_properties.py",
    ),
    # ---- the refactoring: every test read against its code --------------------------------
    Mutation(
        "F4-10",
        "a refusal is never counted as a failed request",
        "gateway/src/aira_gateway/reporting/service.py",
        "        func.sum(case((RequestLog.outcome.in_(sorted(FAILURES)), 1), else_=0)).label(",
        "        func.sum(case((RequestLog.status >= 400, 1), else_=0)).label(",
        "gateway/tests/test_reporting.py",
    ),
    Mutation(
        "F4-22",
        "a stop on a whole use case scoped to another one is refused",
        "gateway/src/aira_gateway/api/incidents/suspensions.py",
        "    if target == RuleTarget.USE_CASE.value and scope and scope != value:",
        "    if False:",
        "gateway/tests/test_suspensions.py",
    ),
    Mutation(
        "F4-30",
        "a finding names the rule that wrote it by id",
        "gateway/src/aira_gateway/api/reporting/anomalies.py",
        '        "rule_id": row.rule_id,',
        "",
        "gateway/tests/test_anomaly_engine.py",
    ),
    Mutation(
        "FX-F12-1",
        "a use case's anomaly rule is deleted at the address the router serves, trailing slash included",
        "management/frontend/src/app/core/api/clients/incidents.ts",
        "      return this.http.delete<void>(`${this.base}${seg(slug)}/anomaly-rules/${id}/`);",
        "      return this.http.delete<void>(`${this.base}${seg(slug)}/anomaly-rules/${id}`);",
        "management/frontend/src/app/core/api/use-case.service.spec.ts",
    ),
    Mutation(
        "FX-F12-10",
        "a recovered live load withdraws only its own failure from the page banner",
        "management/frontend/src/app/core/ui/page-feedback.ts",
        "    if (this.error() === message) this.error.set(null);",
        "    this.error.set(null);",
        "management/frontend/src/app/core/ui/page-feedback.spec.ts",
    ),
    Mutation(
        "FX-F12-101",
        "the console renews a session with the refresh token, not through a hidden iframe the content policy refuses",
        "management/frontend/src/app/core/auth/auth.config.ts",
        "  useSilentRefresh: false,",
        "  useSilentRefresh: true,",
        "tools/tests/test_the_console_policy_admits_its_issuer.py management/frontend/src/app/core/auth/auth.service.spec.ts",
    ),
    Mutation(
        "FX-F12-102",
        "only an answer from the plane that refused ends a login loop",
        "management/frontend/src/app/core/auth/auth.service.ts",
        "    if (recorded && (recorded.refusedBy === null || recorded.refusedBy === answeredBy)) {",
        "    if (recorded) {",
        "management/frontend/src/app/core/auth/auth.service.spec.ts",
    ),
    Mutation(
        "FX-F12-103",
        "a refusal after the loop guard tripped is not counted as another sign-in",
        "management/frontend/src/app/core/auth/auth.service.ts",
        "    const attempts = (previous?.count ?? 0) + 1;",
        "    const attempts = (previous?.count ?? 0) + 1; this.writeReauthAttempts({ count: attempts, first: previous?.first ?? now, refusedBy });",
        "management/frontend/src/app/core/auth/auth.service.spec.ts",
    ),
    Mutation(
        "FX-F12-104",
        "an answer is reported as coming from the plane that gave it",
        "management/frontend/src/app/core/auth/auth.interceptor.ts",
        "        auth.noteFirstPartySuccess(plane);",
        "        auth.noteFirstPartySuccess('/api/');",
        "management/frontend/src/app/core/auth/auth.interceptor.spec.ts",
    ),
    Mutation(
        "FX-F12-105",
        "an undecodable state in the address does not stop the console from starting",
        "management/frontend/src/app/core/auth/auth.service.ts",
        "    const stored = this.oauth.state;",
        "    const stored = this.oauth.state; decodeURIComponent(stored ?? '');",
        "management/frontend/src/app/core/auth/auth.service.spec.ts",
    ),
    Mutation(
        "FX-F12-106",
        "a login started by the guard comes back to the route being entered",
        "management/frontend/src/app/core/auth/auth.guard.ts",
        "  auth.login(state.url);",
        "  auth.login();",
        "management/frontend/src/app/core/auth/auth.guard.spec.ts",
    ),
    Mutation(
        "FX-F12-107",
        "a login named a target comes back to it rather than to the address bar",
        "management/frontend/src/app/core/auth/auth.service.ts",
        "    this.oauth.initCodeFlow(target ?? this.currentPath());",
        "    this.oauth.initCodeFlow(this.currentPath());",
        "management/frontend/src/app/core/auth/auth.service.spec.ts",
    ),
    Mutation(
        "FX-F12-108",
        "the runtime configuration, rewritten at every start under the same name, is never cached",
        "management/frontend/deploy/default.conf.template",
        "    location = /runtime-config.js {",
        "    location = /runtime-config.json {",
        "tools/tests/test_the_console_runtime_config_is_never_cached.py",
    ),
    Mutation(
        "FX-F12-109",
        "the policy compiled into index.html lets the console reach a loopback development issuer",
        "management/frontend/src/index.html",
        "               connect-src 'self' http: https: ws: wss:;",
        "               connect-src 'self' https: ws: wss:;",
        "tools/tests/test_the_console_policy_admits_its_issuer.py",
    ),
    Mutation(
        "FX-F12-11",
        "an answer about another request is never placed under the open row",
        "management/frontend/src/app/features/use-cases/traces-tab.ts",
        "        if (body.id === row.id && this.openRow() === row.id) this.payload.set(body);",
        "        this.payload.set(body);",
        "management/frontend/src/app/features/use-cases/traces-tab.spec.ts",
    ),
    Mutation(
        "FX-F12-110",
        "the console's header policy frames nothing",
        "management/frontend/deploy/aira-headers.inc.template",
        "connect-src ${AIRA_CSP_CONNECT_SRC}; frame-src 'none';",
        "connect-src ${AIRA_CSP_CONNECT_SRC}; frame-src 'self';",
        "tools/tests/test_the_console_policy_admits_its_issuer.py",
    ),
    Mutation(
        "FX-F12-111",
        "a role chip carries no description of the role that the server did not give",
        "management/frontend/src/app/app.html",
        '        <span class="aira-user__role" [attr.data-role]="role.slug">{{ role.label }}</span>',
        '        <span class="aira-user__role" [attr.data-role]="role.slug" title="Sees every use case for security oversight — its configuration, not its content.">{{ role.label }}</span>',
        "management/frontend/src/app/app.spec.ts",
    ),
    Mutation(
        "FX-F12-12",
        "opening another row drops the content read still in flight",
        "management/frontend/src/app/features/use-cases/traces-tab.ts",
        "    this.payloadRequest?.unsubscribe();\n    this.payloadRequest = null;",
        "    this.payloadRequest = null;",
        "management/frontend/src/app/features/use-cases/traces-tab.spec.ts",
    ),
    Mutation(
        "FX-F12-13",
        "every pause in typing that changed the API-key or address filter asks the server again",
        "management/frontend/src/app/features/use-cases/traces-tab.ts",
        "    return `${this.credential().trim()}\\n${this.sourceIp().trim()}`;",
        "    return '';",
        "management/frontend/src/app/features/use-cases/traces-tab.spec.ts",
    ),
    Mutation(
        "FX-F12-14",
        "a failed requests load is reported in the page banner instead of loading for ever",
        "management/frontend/src/app/features/use-cases/traces-tab.ts",
        "        this.liveFailure = this.feedback.fail(response, 'Could not load the requests.');",
        "        this.liveFailure = null;",
        "management/frontend/src/app/features/use-cases/traces-tab.spec.ts",
    ),
    Mutation(
        "FX-F12-15",
        "an older page still on its way is dropped when the filter changes",
        "management/frontend/src/app/features/use-cases/traces-tab.ts",
        "    this.moreRequest?.unsubscribe();\n    this.moreRequest = null;\n    this.loadingMore.set(false);",
        "    this.moreRequest = null;\n    this.loadingMore.set(false);",
        "management/frontend/src/app/features/use-cases/traces-tab.spec.ts",
    ),
    Mutation(
        "FX-F12-16",
        "clicking a request's row opens its metadata and makes no recorded content read",
        "management/frontend/src/app/features/use-cases/traces-tab.ts",
        "      this.openDetail(row);\n    }\n  }",
        "      this.openDetail(row);\n      this.readPayload(row);\n    }\n  }",
        "management/frontend/src/app/features/use-cases/traces-tab.spec.ts",
    ),
    Mutation(
        "FX-F12-17",
        "a cheap request's cost is never printed as zero",
        "management/frontend/src/app/features/use-cases/traces-tab.ts",
        "    const amount = displayAmount(row.cost_nanos, COST_PLACES);",
        "    const amount = (row.cost_nanos / 1_000_000_000).toFixed(COST_PLACES);",
        "management/frontend/src/app/features/use-cases/traces-tab.spec.ts",
    ),
    Mutation(
        "FX-F12-18",
        "an unknown token count reads as unknown, never as 0",
        "management/frontend/src/app/features/use-cases/traces-tab.ts",
        "    return count === null ? '—' : displayCount(count);",
        "    return count === null ? '0' : displayCount(count);",
        "management/frontend/src/app/features/use-cases/traces-tab.spec.ts",
    ),
    Mutation(
        "FX-F12-19",
        "budget figures the gateway did not answer are drawn as unknown, not as zero",
        "management/frontend/src/app/features/use-cases/budgets-tab.ts",
        "    if (this.usageUnavailable()) return false;",
        "",
        "management/frontend/src/app/features/use-cases/budgets-tab.spec.ts",
    ),
    Mutation(
        "FX-F12-2",
        "a new live question drops the load still in flight for the previous one",
        "management/frontend/src/app/core/ui/live.ts",
        "    this.stop();\n    this.job = ",
        "    this.timer?.unsubscribe();\n    this.job = ",
        "management/frontend/src/app/core/ui/live.spec.ts management/frontend/src/app/features/use-cases/traces-tab.spec.ts",
    ),
    Mutation(
        "FX-F12-20",
        "a catalogue that did not load never reads as released models withdrawn",
        "management/frontend/src/app/features/use-cases/model-release-panel.html",
        "    } @else if (catalogMissing()) {",
        "    } @else if (false) {",
        "management/frontend/src/app/features/use-cases/model-release-panel.spec.ts management/frontend/src/app/features/use-cases/use-case-detail.spec.ts",
    ),
    Mutation(
        "FX-F12-201",
        "A description's inline markup is read in time linear in the line's length.",
        "management/frontend/src/app/core/ui/markdown.ts",
        "  return (from) => (from >= length ? length : table[from]);",
        "  return (from) => { for (let at = from; at < length; at += 1) if (holds(at)) return at; return length; };",
        "management/frontend/src/app/core/ui/markdown.spec.ts",
    ),
    Mutation(
        "FX-F12-202",
        "A period whose requests were all unpriced is not reported as one in which nothing was used.",
        "management/frontend/src/app/core/ui/charts/usage-series.ts",
        "    empty: requests === 0 && total === 0,",
        "    empty: total === 0,",
        "management/frontend/src/app/core/ui/charts/usage-series.spec.ts management/frontend/src/app/core/ui/charts/usage-chart.spec.ts",
    ),
    Mutation(
        "FX-F12-203",
        "A period's spend that no request could price is shown as unknown, never 0.00.",
        "management/frontend/src/app/core/ui/charts/usage-series.ts",
        "    display: costUnknown ? UNKNOWN : formatMetric(total, metric),",
        "    display: formatMetric(total, metric),",
        "management/frontend/src/app/core/ui/charts/usage-series.spec.ts management/frontend/src/app/core/ui/charts/usage-chart.spec.ts",
    ),
    Mutation(
        "FX-F12-204",
        "A server-paged list moves its page only when that page's rows arrive.",
        "management/frontend/src/app/core/ui/server-table-view.ts",
        "    if (target === (this.pending ?? this.page())) return;\n    this.fetch(target);",
        "    if (target === (this.pending ?? this.page())) return;\n    this.page.set(target), this.fetch(target);",
        "management/frontend/src/app/core/ui/server-table-view.spec.ts",
    ),
    Mutation(
        "FX-F12-205",
        "A page that no longer exists (404 past page 1) is replaced by the first page, not left stale under a banner.",
        "management/frontend/src/app/core/ui/server-table-view.ts",
        "        if ((response as { status?: number } | null)?.status === 404 && page > 1) {",
        "        if (false) {",
        "management/frontend/src/app/core/ui/server-table-view.spec.ts",
    ),
    Mutation(
        "FX-F12-206",
        "A cursor-paged list extends its trail only when the next page arrives.",
        "management/frontend/src/app/core/ui/cursor-table-view.ts",
        "    this.fetch([...this.trail(), cursor]);",
        "    this.trail.update((trail) => [...trail, cursor]), this.fetch(this.trail());",
        "management/frontend/src/app/core/ui/cursor-table-view.spec.ts",
    ),
    Mutation(
        "FX-F12-207",
        "Previous steps back from the page on screen after a live list shrank under it.",
        "management/frontend/src/app/core/ui/table-view.ts",
        "    this.go(this.current() - 1);",
        "    this.go(this.page() - 1);",
        "management/frontend/src/app/core/ui/table-view.spec.ts",
    ),
    Mutation(
        "FX-F12-208",
        "Enter in the multi-select never submits the form it sits in.",
        "management/frontend/src/app/core/ui/multi-select.ts",
        "      // Never submits the form it sits in, or choosing an option would save the page.\n      event.preventDefault();",
        "      // Never submits the form it sits in, or choosing an option would save the page.",
        "management/frontend/src/app/core/ui/multi-select.spec.ts",
    ),
    Mutation(
        "FX-F12-209",
        "A server-paged list names the page the server says it answered, not the one it asked for.",
        "management/frontend/src/app/core/ui/server-table-view.ts",
        "        this.page.set(answer.page);",
        "        this.page.set(page);",
        "management/frontend/src/app/core/ui/server-table-view.spec.ts",
    ),
    Mutation(
        "FX-F12-21",
        "the Gemini example follows the catalogue, so a failed KIRA listing costs only the KIRA tab",
        "management/frontend/src/app/features/use-cases/connection-panel.ts",
        "        generates: own ? own.includes('generate') : null,",
        "        generates: capabilities.includes('CHAT'),",
        "management/frontend/src/app/features/use-cases/connection-panel.spec.ts",
    ),
    Mutation(
        "FX-F12-210",
        "An hour bucket's full label names the UTC clock it is counted on.",
        "management/frontend/src/app/core/ui/charts/usage-series.ts",
        "  return granularity === 'hour' ? `${bucket.slice(0, 10)} ${bucket.slice(11, 13)}:00 UTC` : bucket;",
        "  return granularity === 'hour' ? `${bucket.slice(0, 10)} ${bucket.slice(11, 13)}:00` : bucket;",
        "management/frontend/src/app/core/ui/charts/usage-series.spec.ts management/frontend/src/app/core/ui/charts/usage-days.spec.ts",
    ),
    Mutation(
        "FX-F12-211",
        "An hourly usage chart calls its rows hours, not days.",
        "management/frontend/src/app/core/ui/charts/usage-chart.ts",
        "    this.series()?.granularity === 'hour' ? 'hour' : 'day',",
        "    'day',",
        "management/frontend/src/app/core/ui/charts/usage-chart.spec.ts",
    ),
    Mutation(
        "FX-F12-212",
        "A count axis draws each labelled tick at the height of the number it carries.",
        "management/frontend/src/app/core/ui/charts/usage-histogram.ts",
        "        .map((value) => ({ at: (value / model.peak) * 100, label: displayCount(value) }));",
        "        .map((value, index) => ({ at: GRID_FRACTIONS[index] * 100, label: displayCount(value) }));",
        "management/frontend/src/app/core/ui/charts/usage-histogram.spec.ts",
    ),
    Mutation(
        "FX-F12-213",
        "A present band below 0.05 % reads <0.1%, never 0.0%.",
        "management/frontend/src/app/core/ui/charts/composition-bar.ts",
        "    return band.share > 0 && band.share < SMALLEST_SHOWN ? '<0.1%' : `${band.share.toFixed(1)}%`;",
        "    return `${band.share.toFixed(1)}%`;",
        "management/frontend/src/app/core/ui/charts/composition-bar.spec.ts",
    ),
    Mutation(
        "FX-F12-214",
        "A privacy-notice load that worked clears the failure of the previous one.",
        "management/frontend/src/app/core/privacy/privacy-gate.ts",
        "        if (!keepMessage) this.acknowledgeError.set(null);",
        "",
        "management/frontend/src/app/core/privacy/privacy-gate.spec.ts",
    ),
    Mutation(
        "FX-F12-215",
        "After a refused (409) acknowledgement the reloaded notice keeps saying why it changed.",
        "management/frontend/src/app/core/privacy/privacy-gate.ts",
        "          this.load(notice.language, true);",
        "          this.load(notice.language);",
        "management/frontend/src/app/core/privacy/privacy-gate.spec.ts",
    ),
    Mutation(
        "FX-F12-216",
        "The multi-select's list closes when focus leaves the control.",
        "management/frontend/src/app/core/ui/multi-select.ts",
        "    '(focusout)': 'onFocusOut($event)',",
        "",
        "management/frontend/src/app/core/ui/multi-select.spec.ts",
    ),
    Mutation(
        "FX-F12-217",
        "The multi-select's options are outside the tab order.",
        "management/frontend/src/app/core/ui/multi-select.html",
        '              tabindex="-1"',
        "",
        "management/frontend/src/app/core/ui/multi-select.spec.ts",
    ),
    Mutation(
        "FX-F12-218",
        "The multi-select's active option is reset whenever the list of matches changes.",
        "management/frontend/src/app/core/ui/multi-select.ts",
        "  protected readonly active = linkedSignal({ source: this.matches, computation: () => 0 });",
        "  protected readonly active = signal(0);",
        "management/frontend/src/app/core/ui/multi-select.spec.ts",
    ),
    Mutation(
        "FX-F12-219",
        "A destroyed info hint does not stay pinned at module level.",
        "management/frontend/src/app/core/ui/info-hint.ts",
        "      if (pinnedHint() === this) pinnedHint.set(null);",
        "      if (false) pinnedHint.set(null);",
        "management/frontend/src/app/core/ui/info-hint.spec.ts",
    ),
    Mutation(
        "FX-F12-22",
        "an access change re-reads the keys it may have revoked",
        "management/frontend/src/app/features/use-cases/use-case-detail.ts",
        "    this.reloadMembers();\n    this.loadKeys();\n  }",
        "    this.reloadMembers();\n  }",
        "management/frontend/src/app/features/use-cases/use-case-detail.spec.ts",
    ),
    Mutation(
        "FX-F12-220",
        "Escape closes an info hint, pinned or hovered.",
        "management/frontend/src/app/core/ui/info-hint.ts",
        "    '(document:keydown.escape)': 'dismiss()',",
        "",
        "management/frontend/src/app/core/ui/info-hint.spec.ts",
    ),
    Mutation(
        "FX-F12-221",
        "A click elsewhere lets go of a pinned info hint.",
        "management/frontend/src/app/core/ui/info-hint.ts",
        "    if (this.pinned() && !this.host.nativeElement.contains(event.target as Node)) {",
        "    if (false) {",
        "management/frontend/src/app/core/ui/info-hint.spec.ts",
    ),
    Mutation(
        "FX-F12-222",
        "An open info-hint panel follows its trigger through a scroll.",
        "management/frontend/src/app/core/ui/info-hint.ts",
        "      document.addEventListener('scroll', follow, { capture: true, passive: true });",
        "",
        "management/frontend/src/app/core/ui/info-hint.spec.ts",
    ),
    Mutation(
        "FX-F12-23",
        "the Rules badge is counted before its tab is opened",
        "management/frontend/src/app/features/use-cases/use-case-detail.ts",
        "    this.loadRules();\n    this.loadWarnings();",
        "    this.loadWarnings();",
        "management/frontend/src/app/features/use-cases/use-case-detail.spec.ts",
    ),
    Mutation(
        "FX-F12-24",
        "the Warnings badge is counted before its tab is opened",
        "management/frontend/src/app/features/use-cases/use-case-detail.ts",
        "    this.loadRules();\n    this.loadWarnings();",
        "    this.loadRules();",
        "management/frontend/src/app/features/use-cases/use-case-detail.spec.ts",
    ),
    Mutation(
        "FX-F12-25",
        "rules that could not be loaded are not reported as nothing watched",
        "management/frontend/src/app/features/use-cases/rules-tab.html",
        "  @if (failed()) {",
        "  @if (false) {",
        "management/frontend/src/app/features/use-cases/rules-tab.spec.ts management/frontend/src/app/features/use-cases/use-case-detail.spec.ts",
    ),
    Mutation(
        "FX-F12-26",
        "the people panel says the window it reads did not arrive, even when the other one did",
        "management/frontend/src/app/features/use-cases/people-panel.ts",
        "    this.perHead()?.period === 'day' ? this.todayUnavailable() : this.monthUnavailable(),",
        "    this.todayUnavailable() && this.monthUnavailable(),",
        "management/frontend/src/app/features/use-cases/people-panel.spec.ts management/frontend/src/app/features/use-cases/use-case-detail.spec.ts",
    ),
    Mutation(
        "FX-F12-27",
        "the usage chart draws the period that is chosen, whichever answer arrives last",
        "management/frontend/src/app/features/use-cases/use-case-detail.ts",
        "    this.chartRequest?.unsubscribe();",
        "    void this.chartRequest;",
        "management/frontend/src/app/features/use-cases/use-case-detail.spec.ts",
    ),
    Mutation(
        "FX-F12-28",
        "a lapsed API key reads as expired, never as active",
        "management/frontend/src/app/features/use-cases/api-keys-panel.ts",
        "    return !!key.expires_at && new Date(key.expires_at).getTime() <= Date.now();",
        "    return false;",
        "management/frontend/src/app/features/use-cases/api-keys-panel.spec.ts",
    ),
    Mutation(
        "FX-F12-29",
        "the OpenCode file names only models released to the use case, approved and able to call tools",
        "management/frontend/src/app/features/use-cases/api-keys-panel.ts",
        "        released.has(m.name) && m.approved !== false && (m.capabilities ?? []).includes('tools'),",
        "        (m.capabilities ?? []).includes('tools'),",
        "management/frontend/src/app/features/use-cases/api-keys-panel.spec.ts management/frontend/src/app/features/use-cases/use-case-detail.spec.ts",
    ),
    Mutation(
        "FX-F12-3",
        '"refresh now" runs now, replacing a tick still in flight',
        "management/frontend/src/app/core/ui/live.ts",
        "    this.cancel();\n    this.run();\n  }",
        "    if (this.inFlight) return;\n    this.run();\n  }",
        "management/frontend/src/app/core/ui/live.spec.ts",
    ),
    Mutation(
        "FX-F12-30",
        "a refused key issue is said inside the window",
        "management/frontend/src/app/features/use-cases/api-keys-panel.html",
        '    [error]="feedback.error()"',
        "",
        "management/frontend/src/app/features/use-cases/api-keys-panel.spec.ts",
    ),
    Mutation(
        "FX-F12-31",
        "a window opens without an earlier page failure that is not its own",
        "management/frontend/src/app/features/use-cases/api-keys-panel.ts",
        "    this.feedback.clear();\n    this.showIssueKey.set(true);",
        "    this.showIssueKey.set(true);",
        "management/frontend/src/app/features/use-cases/api-keys-panel.spec.ts",
    ),
    Mutation(
        "FX-F12-32",
        "a refused budget save is said inside the window",
        "management/frontend/src/app/features/use-cases/budgets-tab.html",
        '    [error]="feedback.error()"',
        "",
        "management/frontend/src/app/features/use-cases/budgets-tab.spec.ts",
    ),
    Mutation(
        "FX-F12-33",
        "a refused rate-limit save is said inside the window",
        "management/frontend/src/app/features/use-cases/rate-limits-tab.html",
        '    [error]="feedback.error()"',
        "",
        "management/frontend/src/app/features/use-cases/rate-limits-tab.spec.ts",
    ),
    Mutation(
        "FX-F12-34",
        "a refused new rule is said inside the window",
        "management/frontend/src/app/features/use-cases/rules-tab.html",
        '    [error]="feedback.error()"',
        "",
        "management/frontend/src/app/features/use-cases/rules-tab.spec.ts",
    ),
    Mutation(
        "FX-F12-35",
        "a refused rule edit is said beside the form",
        "management/frontend/src/app/features/use-cases/rules-tab.html",
        "                  @if (feedback.error(); as message) {",
        "                  @if (null; as message) {",
        "management/frontend/src/app/features/use-cases/rules-tab.spec.ts",
    ),
    Mutation(
        "FX-F12-36",
        "a refused use-case creation is said inside the window",
        "management/frontend/src/app/features/use-cases/use-case-list.html",
        '  [error]="createError()"',
        "",
        "management/frontend/src/app/features/use-cases/use-case-list.spec.ts",
    ),
    Mutation(
        "FX-F12-37",
        "period windows are the UTC days the gateway and the budget counters use",
        "management/frontend/src/app/core/ui/periods.ts",
        "  const day = (offset: number) => utcDay(new Date(Date.UTC(year, month, date + offset)));",
        "  const day = (offset: number) => isoDay(new Date(now.getFullYear(), now.getMonth(), now.getDate() + offset));",
        "management/frontend/src/app/core/ui/periods.spec.ts management/frontend/src/app/features/use-cases/use-case-detail.spec.ts",
    ),
    Mutation(
        "FX-F12-38",
        "a new rule named like an existing one is confirmed as a replacement",
        "management/frontend/src/app/features/use-cases/rules-tab.ts",
        "      ? (this.rules() ?? []).find((rule) => rule.name === changes.name)",
        "      ? (this.rules() ?? []).find((rule) => rule.name === `${changes.name}?`)",
        "management/frontend/src/app/features/use-cases/rules-tab.spec.ts",
    ),
    Mutation(
        "FX-F12-39",
        "a new budget for a scope and period that has one is confirmed as a replacement",
        "management/frontend/src/app/features/use-cases/budgets-tab.ts",
        "    const replaced = this.replaces(this.budgetScope(), this.budgetPeriod());",
        "    const replaced = this.replaces(this.budgetScope(), `${this.budgetPeriod()}?`);",
        "management/frontend/src/app/features/use-cases/budgets-tab.spec.ts",
    ),
    Mutation(
        "FX-F12-4",
        'a live load that completes without an answer does not stay "refreshing" or block later ticks',
        "management/frontend/src/app/core/ui/live.ts",
        "      complete: settle,",
        "      complete: () => undefined,",
        "management/frontend/src/app/core/ui/live.spec.ts",
    ),
    Mutation(
        "FX-F12-40",
        "a new rate limit for a scope that has one is confirmed as a replacement",
        "management/frontend/src/app/features/use-cases/rate-limits-tab.ts",
        "    const replaced = this.limits().find((row) => row.scope === this.rlScope() && !row.subject);",
        "    const replaced = this.limits().find((row) => row.scope === `${this.rlScope()}?` && !row.subject);",
        "management/frontend/src/app/features/use-cases/rate-limits-tab.spec.ts",
    ),
    Mutation(
        "FX-F12-41",
        "a stop that runs out while the warnings tab is open stops being shown as in force",
        "management/frontend/src/app/features/use-cases/warnings-tab.ts",
        "    const now = this.live.now().getTime();",
        "    const now = Date.now();",
        "management/frontend/src/app/features/use-cases/warnings-tab.spec.ts",
    ),
    Mutation(
        "FX-F12-42",
        "the Warnings badge counts recent findings, not all history",
        "management/frontend/src/app/features/use-cases/warnings-tab.ts",
        "  const count = page.events.filter((event) => new Date(event.created_at).getTime() >= since).length;",
        "  const count = page.events.length;",
        "management/frontend/src/app/features/use-cases/warnings-tab.spec.ts management/frontend/src/app/features/use-cases/use-case-detail.spec.ts",
    ),
    Mutation(
        "FX-F12-43",
        'a directory search cut short does not keep saying "Searching…"',
        "management/frontend/src/app/features/use-cases/access-panel.ts",
        "      // The search in flight was just dropped, so nothing is being searched any more.\n      this.searching.set(false);",
        "",
        "management/frontend/src/app/features/use-cases/access-panel.spec.ts",
    ),
    Mutation(
        "FX-F12-44",
        "a failed directory search does not claim nothing matches",
        "management/frontend/src/app/features/use-cases/access-panel.html",
        "    } @else if (query().trim().length >= 2 && !searching() && !searchFailed()) {",
        "    } @else if (query().trim().length >= 2 && !searching()) {",
        "management/frontend/src/app/features/use-cases/access-panel.spec.ts",
    ),
    Mutation(
        "FX-F12-45",
        "a typed key lifetime is sent, 0 included, so the server can refuse it by name",
        "management/frontend/src/app/core/api/clients/access.ts",
        "      if (expiresInDays !== null && expiresInDays !== undefined) {",
        "      if (expiresInDays) {",
        "management/frontend/src/app/core/api/use-case.service.spec.ts",
    ),
    Mutation(
        "FX-F12-46",
        "the keys form sends whatever lifetime was typed",
        "management/frontend/src/app/features/use-cases/api-keys-panel.ts",
        "    const days = typed === null || String(typed).trim() === '' ? null : Number(typed);",
        "    const days = Number(typed) > 0 ? Number(typed) : null;",
        "management/frontend/src/app/features/use-cases/api-keys-panel.spec.ts",
    ),
    Mutation(
        "FX-F12-47",
        'a key issued for somebody else names them, not "you", as accountable',
        "management/frontend/src/app/features/use-cases/api-keys-panel.ts",
        "  protected readonly ownerToBe = computed(() => this.keyOwner().trim());",
        "  protected readonly ownerToBe = computed(() => '');",
        "management/frontend/src/app/features/use-cases/api-keys-panel.spec.ts",
    ),
    Mutation(
        "FX-F12-48",
        "deleting stored content by a retention change is asked before it is saved",
        "management/frontend/src/app/features/use-cases/data-protection-panel.ts",
        "    if (deletes && !this.confirmService.ask(deletes)) {",
        "    if (false) {",
        "management/frontend/src/app/features/use-cases/data-protection-panel.spec.ts",
    ),
    Mutation(
        "FX-F12-49",
        "only a shorter retention period is asked about",
        "management/frontend/src/app/features/use-cases/data-protection-panel.ts",
        "    if (days != null && before != null && days < before) {",
        "    if (days != null && before != null && days > before) {",
        "management/frontend/src/app/features/use-cases/data-protection-panel.spec.ts",
    ),
    Mutation(
        "FX-F12-5",
        "switching live off drops a refresh in flight",
        "management/frontend/src/app/core/ui/live.ts",
        "    this.enabled.set(false);\n    if (this.settledOnce) this.cancel();",
        "    this.enabled.set(false);",
        "management/frontend/src/app/core/ui/live.spec.ts",
    ),
    Mutation(
        "FX-F12-50",
        "a budget limit is written as every amount here, not as stored",
        "management/frontend/src/app/features/use-cases/budgets-tab.html",
        "                {{ limitText(budget.limit_cost) }}",
        "                {{ budget.limit_cost }}",
        "management/frontend/src/app/features/use-cases/budgets-tab.spec.ts",
    ),
    Mutation(
        "FX-F12-51",
        "a per-person remainder is rounded half-up on the digits",
        "management/frontend/src/app/features/use-cases/people-panel.ts",
        "  return displayAmount(nanos, Math.min(places, MAX_PLACES));",
        "  return (nanos / 1_000_000_000).toFixed(Math.min(places, MAX_PLACES));",
        "management/frontend/src/app/features/use-cases/people-panel.spec.ts",
    ),
    Mutation(
        "FX-F12-52",
        "a token or request limit below 1 is refused before it is sent",
        "management/frontend/src/app/features/use-cases/budgets-tab.ts",
        "    if (!isWholeAtLeastOne(this.budgetRequests())) {",
        "    if (false) {",
        "management/frontend/src/app/features/use-cases/budgets-tab.spec.ts",
    ),
    Mutation(
        "FX-F12-53",
        "a limit of zero is drawn full",
        "management/frontend/src/app/features/use-cases/budgets-tab.ts",
        "    if (limit === 0) return 100;",
        "",
        "management/frontend/src/app/features/use-cases/budgets-tab.spec.ts",
    ),
    Mutation(
        "FX-F12-54",
        "revoking a group does not relabel the Grant button",
        "management/frontend/src/app/features/use-cases/access-panel.html",
        "        {{ granting() ? 'Granting…' : 'Grant' }}",
        "        {{ feedback.busy() ? 'Granting…' : 'Grant' }}",
        "management/frontend/src/app/features/use-cases/access-panel.spec.ts",
    ),
    Mutation(
        "FX-F12-55",
        "one overview form's save does not relabel the other",
        "management/frontend/src/app/features/use-cases/data-protection-panel.html",
        "            {{ saving() ? 'Saving…' : 'Save storage settings' }}",
        "            {{ feedback.busy() ? 'Saving…' : 'Save storage settings' }}",
        "management/frontend/src/app/features/use-cases/use-case-detail.spec.ts",
    ),
    Mutation(
        "FX-F12-56",
        "a description being edited survives a look at another tab",
        "management/frontend/src/app/features/use-cases/use-case-detail.html",
        '      <app-about-panel\n        [shown]="onOverview()"\n        [slug]="slug()"\n        [canManage]="canManage()"\n        [useCase]="useCase()"\n        (saved)="useCase.set($event)"\n      />\n      @if (onOverview()) {\n        <div class="tiles">',
        '      @if (onOverview()) {\n        <app-about-panel\n          [slug]="slug()"\n          [canManage]="canManage()"\n          [useCase]="useCase()"\n          (saved)="useCase.set($event)"\n        />\n        <div class="tiles">',
        "management/frontend/src/app/features/use-cases/use-case-detail.spec.ts",
    ),
    Mutation(
        "FX-F12-57",
        "a slug generated from a long name never ends in a hyphen",
        "management/frontend/src/app/features/use-cases/use-case-slug.ts",
        "    .slice(0, 60)\n    .replace(/-+$/, '');",
        "    .slice(0, 60);",
        "management/frontend/src/app/features/use-cases/use-case-list.spec.ts",
    ),
    Mutation(
        "FX-F12-58",
        "the plaintext key is not inside an assertive live region",
        "management/frontend/src/app/features/use-cases/api-keys-panel.html",
        '    <div class="callout callout--warning stack" style="margin-top: 1rem">',
        '    <div class="callout callout--warning stack" style="margin-top: 1rem" role="alert">',
        "management/frontend/src/app/features/use-cases/api-keys-panel.spec.ts",
    ),
    Mutation(
        "FX-F12-59",
        "the page behind an open window is inert",
        "management/frontend/src/app/core/ui/modal.ts",
        "        sibling.setAttribute('inert', '');",
        "        void sibling;",
        "management/frontend/src/app/core/ui/modal.spec.ts",
    ),
    Mutation(
        "FX-F12-6",
        "every failed live load is handed to the caller to report",
        "management/frontend/src/app/core/ui/live.ts",
        "        job.onError(response);",
        "        void response;",
        "management/frontend/src/app/core/ui/live.spec.ts",
    ),
    Mutation(
        "FX-F12-60",
        "Tab wraps at a window's edges instead of leaving it",
        "management/frontend/src/app/core/ui/modal.ts",
        "    } else if (!event.shiftKey && active === last) {\n      event.preventDefault();\n      first.focus();",
        "    } else if (!event.shiftKey && active === last) {\n      event.preventDefault();",
        "management/frontend/src/app/core/ui/modal.spec.ts",
    ),
    Mutation(
        "FX-F12-61",
        "closing a window returns the keyboard to whatever opened it",
        "management/frontend/src/app/core/ui/modal.ts",
        "    if (opener?.isConnected) opener.focus();",
        "    void opener;",
        "management/frontend/src/app/core/ui/modal.spec.ts",
    ),
    Mutation(
        "FX-F12-62",
        "a window releases only the inert it set",
        "management/frontend/src/app/core/ui/modal.ts",
        "        if (sibling.hasAttribute('inert')) continue;",
        "        if (sibling.hasAttribute('inert')) void 0;",
        "management/frontend/src/app/core/ui/modal.spec.ts",
    ),
    Mutation(
        "FX-F12-63",
        "a refused download's reason is read out of the blob it arrives as",
        "management/frontend/src/app/core/api/clients/base.ts",
        "    if (!(response instanceof HttpErrorResponse) || !(response.error instanceof Blob)) {",
        "    if (!(response instanceof HttpErrorResponse) || !(response.error instanceof Blob) || response.status > 0) {",
        "management/frontend/src/app/core/api/use-case.service.spec.ts",
    ),
    Mutation(
        "FX-F12-64",
        "a 2xx that is not JSON is named as not the API, never as a parser message",
        "management/frontend/src/app/core/api/error-message.ts",
        "    return NOT_THE_API;",
        "    return fallback;",
        "management/frontend/src/app/core/api/error-message.spec.ts",
    ),
    Mutation(
        "FX-F12-65",
        "an Error object is never read as the server's envelope",
        "management/frontend/src/app/core/api/error-message.ts",
        "  return !!value && typeof value === 'object' && !Array.isArray(value) && !(value instanceof Error);",
        "  return !!value && typeof value === 'object' && !Array.isArray(value);",
        "management/frontend/src/app/core/api/error-message.spec.ts",
    ),
    Mutation(
        "FX-F12-66",
        "nested validation details are spelled out, never [object Object]",
        "management/frontend/src/app/core/api/error-message.ts",
        "      .flatMap((item) => flatten(item, field));",
        "      .flatMap((item) => [labelled(field, String(item))]);",
        "management/frontend/src/app/core/api/error-message.spec.ts",
    ),
    Mutation(
        "FX-F12-67",
        "the pipeline test stores the answer without the model's reasoning",
        "management/frontend/src/app/core/api/clients/smoke-tests.ts",
        "              .filter((part) => !part.thought)",
        "              .filter(() => true)",
        "management/frontend/src/app/core/api/use-case.service.spec.ts",
    ),
    Mutation(
        "FX-F12-68",
        "/v1/me is asked once and shared by the shell and every screen",
        "management/frontend/src/app/core/api/me.service.ts",
        "      shareReplay({ bufferSize: 1, refCount: false }),",
        "",
        "management/frontend/src/app/core/api/me.service.spec.ts",
    ),
    Mutation(
        "FX-F12-69",
        "a /v1/me request in flight is shared, not repeated",
        "management/frontend/src/app/core/api/me.service.ts",
        "    this.pending ??= this.http.get<Me>(`${API}/v1/me`).pipe(",
        "    this.pending = this.http.get<Me>(`${API}/v1/me`).pipe(",
        "management/frontend/src/app/core/api/me.service.spec.ts",
    ),
    Mutation(
        "FX-F12-7",
        'the "updated N ago" stamp ages while nothing refreshes',
        "management/frontend/src/app/core/ui/live.ts",
        "      if (!this.hidden()) this.clock.set(new Date());",
        "      if (!this.hidden()) return;",
        "management/frontend/src/app/core/ui/live.spec.ts management/frontend/src/app/features/use-cases/traces-tab.spec.ts management/frontend/src/app/features/use-cases/warnings-tab.spec.ts",
    ),
    Mutation(
        "FX-F12-70",
        "a spike threshold in the rules table reads as the multiple it means",
        "management/frontend/src/app/features/use-cases/rules-tab.ts",
        "    return thresholdText(rule.kind, rule.threshold);",
        "    return `${rule.threshold}`;",
        "management/frontend/src/app/features/use-cases/rules-tab.spec.ts",
    ),
    Mutation(
        "FX-F12-8",
        "the page stays busy until every overlapping mutation has settled",
        "management/frontend/src/app/core/ui/page-feedback.ts",
        "      this.busy.set(this.running > 0);",
        "      this.busy.set(false);",
        "management/frontend/src/app/core/ui/page-feedback.spec.ts",
    ),
    Mutation(
        "FX-F12-9",
        "a mutation that completes without an answer does not leave the page busy",
        "management/frontend/src/app/core/ui/page-feedback.ts",
        "      complete: settle,",
        "      complete: () => undefined,",
        "management/frontend/src/app/core/ui/page-feedback.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-10",
        "the model panel shows every declared field, cache prices and regions included",
        "management/frontend/src/app/features/models/catalog-display.ts",
        "      key: 'cached_input_price',",
        "      key: 'cached_input_price_gone',",
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-11",
        "a step's stored model outside the release is shown as itself and marked, not rendered blank",
        "management/frontend/src/app/features/pipelines/pipeline-editor.ts",
        "    if (!model || this.released().includes(model)) return null;",
        "    if (!model || this.released().length >= 0) return null;",
        "management/frontend/src/app/features/pipelines/pipeline-editor.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-12",
        "'no model is released' is said only about a use case that answered",
        "management/frontend/src/app/features/pipelines/pipeline-editor.ts",
        "    () => this.releaseKnown() && this.released().length === 0,",
        "    () => this.released().length === 0,",
        "management/frontend/src/app/features/pipelines/pipeline-editor.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-13",
        "a role's permissions are listed by name when the catalogue cannot be read",
        "management/frontend/src/app/features/platform/roles-page.html",
        "        @if (catalogueFailed() && role.permissions.length) {",
        "        @if (false && role.permissions.length) {",
        "management/frontend/src/app/features/platform/roles-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-14",
        "importing a served model copies the region its adapter names",
        "management/frontend/src/app/features/models/model-editor.ts",
        "      region: model.airaRegion ?? '',",
        "      region: '',",
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-15",
        "a model with modes and no level words can still be asked about its modes",
        "management/frontend/src/app/features/models/model-editor.html",
        '[disabled]="checkingLevels() || !canAskModel()"',
        '[disabled]="checkingLevels() || !thinkingLevels().length || !name().trim()"',
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-16",
        "a router that matched nothing is not described as a category called null",
        "management/frontend/src/app/features/pipelines/pipeline-steps.ts",
        "        : `No category matched — sent to the default model ${text(detail, 'to')}.`;",
        "        : `Classified as “${category}” → ${text(detail, 'to')}.`;",
        "management/frontend/src/app/features/pipelines/pipeline-editor.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-17",
        "the preview's note for the personal-data filter describes that step",
        "management/frontend/src/app/features/pipelines/pipeline-steps.ts",
        "  pii_filter: 'a model replaces the personal data — decided at request time',",
        "  pii_filter: 'LLM picks a category — decided at request time',",
        "management/frontend/src/app/features/pipelines/pipeline-editor.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-17b",
        "the preview caps custom patterns on their own, as the gateway does",
        "management/frontend/src/app/features/pipelines/pipeline-steps.ts",
        "    const patterns = [...(step.config.use_builtins !== false ? BUILTIN_REGEXES : []), ...custom];",
        "    const patterns = [...(step.config.use_builtins !== false ? BUILTIN_REGEXES : []), ...custom].slice(0, MAX_CUSTOM_PATTERNS);",
        "management/frontend/src/app/features/pipelines/pipeline-editor.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-17c",
        "a preview that scanned only the start of the sample says so",
        "management/frontend/src/app/features/pipelines/pipeline-steps.ts",
        "      scanned.length > PREVIEW_MAX_CHARS",
        "      false",
        "management/frontend/src/app/features/pipelines/pipeline-editor.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-18-reads",
        "a successful content-read load takes an earlier failure off the page",
        "management/frontend/src/app/features/platform/content-reads-page.ts",
        "        tap(() => this.feedback.clear()),",
        "        tap(() => undefined),",
        "management/frontend/src/app/features/platform/content-reads-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-18a",
        "a later successful row check clears the earlier check's failure",
        "management/frontend/src/app/features/models/model-catalog.ts",
        "    this.feedback.clear();\n    this.checking.set(true);",
        "    this.checking.set(true);",
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-18b",
        "a later successful editor check clears the earlier check's failure",
        "management/frontend/src/app/features/models/model-editor.ts",
        "    this.feedback.clear();\n    this.checking.set(true);",
        "    this.checking.set(true);",
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-18c",
        "a later successful discovery clears the earlier failure",
        "management/frontend/src/app/features/models/served-models.ts",
        "    this.feedback.clear();",
        "",
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-19",
        "a gateway that serves nothing is not reported as a catalog that has everything",
        "management/frontend/src/app/features/models/served-models.html",
        "  @if (!list.length) {",
        "  @if (false) {",
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-1a",
        "a model cannot be added under a name the catalog already holds",
        "management/frontend/src/app/features/models/model-editor.ts",
        "    if (this.alreadyCatalogued()) {",
        "    if (false) {",
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-1b",
        "a served model the catalog already holds is not offered for import again",
        "management/frontend/src/app/features/models/served-models.ts",
        "      (model) => model.airaDeclared === false && !this.catalogued().has(model.name),",
        "      (model) => model.airaDeclared === false,",
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-1c",
        "importing a served model the catalog took in meanwhile opens the stored declaration",
        "management/frontend/src/app/features/models/model-catalog.ts",
        "    if (existing) {\n      this.editor().edit(existing);\n      return;\n    }\n    this.editor().importServed(model);",
        "    this.editor().importServed(model);",
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-2",
        "the pipeline cannot be saved before it was read or after reading it failed",
        "management/frontend/src/app/features/pipelines/pipeline-editor.ts",
        "    () => this.canManage() && !this.loading() && !this.loadFailed(),",
        "    () => this.canManage(),",
        "management/frontend/src/app/features/pipelines/pipeline-editor.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-20a",
        "Escape closes the browse window",
        "management/frontend/src/app/features/models/model-catalog.html",
        '  (closed)="closeBrowse()"',
        "",
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-20b",
        "the model editor keeps the steady window layout",
        "management/frontend/src/app/features/models/model-editor.html",
        '  panelClass="modal--steady"',
        "",
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-21a",
        "a reader is not offered the add-model entrance",
        "management/frontend/src/app/features/models/model-catalog.html",
        '  @if (canEdit()) {\n    <div class="card">',
        '  @if (true) {\n    <div class="card">',
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-21b",
        "a model without a provider shows a dash in its provider cell",
        "management/frontend/src/app/features/models/model-catalog.html",
        "<td>{{ model.provider || '—' }}</td>",
        "<td>{{ model.provider || '' }}</td>",
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-21c",
        "a permitted region raises no form error",
        "management/frontend/src/app/features/models/model-editor.ts",
        "    if (forbidden.length) {",
        "    if (this.regions().length) {",
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-21d",
        "the editor's name box shows the model being edited",
        "management/frontend/src/app/features/models/model-editor.html",
        '[ngModel]="name()"',
        '[ngModel]="displayName()"',
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-21e",
        "a served model without a region shows a dash in its region cell",
        "management/frontend/src/app/features/models/served-models.html",
        "<td>{{ model.airaRegion || '—' }}</td>",
        "<td>{{ model.airaRegion }}</td>",
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-22-reads",
        "typing a use case into the content-read filter asks the server again",
        "management/frontend/src/app/features/platform/content-reads-page.ts",
        "    this.useCase.set(value);\n    this.typed.next();",
        "    this.useCase.set(value);",
        "management/frontend/src/app/features/platform/content-reads-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-22-reads-b",
        "typing a reader into the content-read filter asks the server again",
        "management/frontend/src/app/features/platform/content-reads-page.ts",
        "    this.reader.set(value);\n    this.typed.next();",
        "    this.reader.set(value);",
        "management/frontend/src/app/features/platform/content-reads-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-23",
        "the custom-patterns textarea writes the step's patterns",
        "management/frontend/src/app/features/pipelines/pipeline-editor.html",
        "(ngModelChange)=\"setListField(selectedIndex(), 'patterns', $event)\"",
        "(ngModelChange)=\"setListField(selectedIndex(), 'unused', $event)\"",
        "management/frontend/src/app/features/pipelines/pipeline-editor.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-24",
        "the dispatch sentence has no space before its comma",
        "management/frontend/src/app/features/pipelines/pipeline-steps.ts",
        "  return models.length ? `, falling back to ${models.join(', ')}` : '';",
        "  return models.length ? ` , falling back to ${models.join(', ')}` : '';",
        "management/frontend/src/app/features/pipelines/pipeline-editor.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-24b",
        "a reader sees a step's chain read-only, without remove buttons",
        "management/frontend/src/app/features/pipelines/pipeline-editor.html",
        '                  testid="pii-chain"\n                  placeholder="type part of a model name…"\n                  emptyHint="No model is released to this use case yet."\n                  [options]="modelChoices()"\n                  [selected]="step.config.fallback_models || []"\n                  [disabled]="!canManage()"',
        '                  testid="pii-chain"\n                  placeholder="type part of a model name…"\n                  emptyHint="No model is released to this use case yet."\n                  [options]="modelChoices()"\n                  [selected]="step.config.fallback_models || []"',
        "management/frontend/src/app/features/pipelines/pipeline-editor.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-25a",
        "a price with a seventh decimal is refused by field and limit",
        "management/frontend/src/app/features/models/model-editor.ts",
        "  if (fraction.length > PRICE_DIGITS) {",
        "  if (false) {",
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-25b",
        "an unreadable embedding width is refused rather than dropped",
        "management/frontend/src/app/features/models/model-editor.ts",
        "      if (unreadable.length) {",
        "      if (false) {",
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-25c",
        "an output cap above the context window is refused before saving",
        "management/frontend/src/app/features/models/model-editor.ts",
        "    if (max != null && window != null && max > window) {",
        "    if (false) {",
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-26",
        "a dry run's 401 names an expired session as well as a gateway without OIDC",
        "management/frontend/src/app/features/pipelines/pipeline-test-panel.ts",
        "If your session has expired, reload the page to sign in again. If it keeps happening, the gateway may not have OIDC enabled (AIRA_OIDC_ENABLED), so it cannot verify the same Keycloak token.",
        "Enable OIDC on the gateway (AIRA_OIDC_ENABLED) so it can verify the same Keycloak token.",
        "management/frontend/src/app/features/pipelines/pipeline-editor.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-27",
        "the LLM classifier's empty option points at nothing that is not on screen",
        "management/frontend/src/app/features/pipelines/pipeline-editor.html",
        '<option value="">— none: the heuristic patterns decide —</option>',
        '<option value="">— none: use the patterns below —</option>',
        "management/frontend/src/app/features/pipelines/pipeline-editor.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-28",
        "an edit keeps the addressing keys the form does not show",
        "management/frontend/src/app/features/models/model-editor.ts",
        "    const rest = { ...(this.storedAddressing() ?? {}) };",
        "    const rest: Record<string, unknown> = {};",
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-2b",
        "a pipeline that could not be read renders no editable builder",
        "management/frontend/src/app/features/pipelines/pipeline-editor.html",
        "  } @else if (loadFailed()) {",
        "  } @else if (false) {",
        "management/frontend/src/app/features/pipelines/pipeline-editor.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-3",
        "a level word chosen as the thinking default is sent as that word",
        "management/frontend/src/app/features/models/model-editor.ts",
        "          default: chosen ? { mode: chosen } : null,",
        "          default: chosen && this.thinkingModes().includes(chosen as ThinkingModeName) ? { mode: chosen } : null,",
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-4-role",
        "a refused role save or delete is shown inside the open window",
        "management/frontend/src/app/features/platform/role-editor.html",
        "  @if (feedback.error(); as error) {",
        "  @if (feedback.notice(); as error) {",
        "management/frontend/src/app/features/platform/roles-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-4-role-b",
        "a refusal is said once, not again under the window's backdrop",
        "management/frontend/src/app/features/platform/roles-page.html",
        "  @if (!editor() && feedback.error(); as error) {",
        "  @if (feedback.error(); as error) {",
        "management/frontend/src/app/features/platform/roles-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-4-role-c",
        "closing the role window takes its refusal with it",
        "management/frontend/src/app/features/platform/roles-page.ts",
        "    this.editor.set(null);\n    this.feedback.clear();",
        "    this.editor.set(null);",
        "management/frontend/src/app/features/platform/roles-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-4a",
        "a refused save is shown inside the model editor's window",
        "management/frontend/src/app/features/models/model-editor.html",
        '  [error]="feedback.error()"',
        '  [error]="null"',
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-4b",
        "the page banner does not repeat the editor's refusal behind its backdrop",
        "management/frontend/src/app/features/models/model-catalog.html",
        "    @if (!modelEditor.showAdd()) {",
        "    @if (true) {",
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-4c",
        "closing the editor takes its refusal with it",
        "management/frontend/src/app/features/models/model-editor.ts",
        "    this.reset();\n    this.feedback.clear();\n  }",
        "    this.reset();\n  }",
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-5a",
        "a failed providers answer is not remembered, so the next look asks again",
        "management/frontend/src/app/features/models/gateway-providers.ts",
        "        this.settled.next(null);",
        "        this.list.set([]);\n        this.settled.next(null);",
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-5b",
        "the browse window offers to ask the gateway again after a failure",
        "management/frontend/src/app/features/models/model-catalog.html",
        '          (click)="askProviders()"',
        '          (click)="closeBrowse()"',
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-6a",
        "one provider's listing never lands under another provider",
        "management/frontend/src/app/features/models/model-catalog.ts",
        "    this.offeringsRequest?.unsubscribe();",
        "",
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-6b",
        "a row's reachability verdict never lands under another row",
        "management/frontend/src/app/features/models/model-catalog.ts",
        "    this.openModel.set(this.openModel() === model.name ? null : model.name);\n    this.checkRequest?.unsubscribe();",
        "    this.openModel.set(this.openModel() === model.name ? null : model.name);",
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-6c",
        "a question in flight when the editor closes changes nothing in the next window",
        "management/frontend/src/app/features/models/model-editor.ts",
        "    this.forgetAnswers();\n    this.editorTab.set('identity');",
        "    this.editorTab.set('identity');",
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-6d",
        "changing the provider forgets a verdict given about the previous one",
        "management/frontend/src/app/features/models/model-editor.ts",
        "    this.forgetAnswers();\n    if (value === this.OTHER) {",
        "    if (value === this.OTHER) {",
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-7",
        "the remove confirmation names the refusals and the lost releases",
        "management/frontend/src/app/features/models/model-catalog.ts",
        "      `Remove ${model.name} from the catalog? Every request for it will be refused, and every use ` +",
        "      `Remove ${model.name} from the catalog? Requests for it will no longer be priced. ` +",
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-8",
        "an edit cannot clear a catalogued model's KIRA id",
        "management/frontend/src/app/features/models/model-editor.ts",
        "    if (this.editing() && storedId !== null && this.kiraId() === null) {",
        "    if (false) {",
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-9a",
        "adding a model does not wait on a check its author may not run",
        "management/frontend/src/app/features/models/model-editor.ts",
        "    return this.canDiagnose() && !this.editing() && this.checkedName() !== this.name().trim();",
        "    return !this.editing() && this.checkedName() !== this.name().trim();",
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "FX-F34-F3-9b",
        "the reachability check is offered only to whoever may diagnose",
        "management/frontend/src/app/features/models/model-catalog.html",
        "                        @if (canDiagnose()) {",
        "                        @if (true) {",
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-10a",
        "Refused counts only the outcomes that are refusals, never everything but served",
        "management/frontend/src/app/features/reporting/reporting-page.ts",
        "  protected readonly refused = computed(() => this.outcomes(REFUSALS));",
        "  protected readonly refused = computed(() => (this.report()?.by_outcome ?? []).filter((row) => row.key !== 'served').reduce((sum, row) => sum + row.requests, 0));",
        "management/frontend/src/app/features/reporting/reporting-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-10b",
        "Failed does not count refusals a second time",
        "management/frontend/src/app/features/reporting/reporting-page.ts",
        "        value: displayCount(this.failed()),",
        "        value: displayCount(totals.failed_requests),",
        "management/frontend/src/app/features/reporting/reporting-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-11a",
        "a period in which nothing that ran could be priced shows its spend as unknown, not 0.00",
        "management/frontend/src/app/features/reporting/reporting-page.ts",
        "        value: this.spendUnknown() ? '—' : totals.cost,",
        "        value: totals.cost,",
        "management/frontend/src/app/features/reporting/reporting-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-11b",
        "an unmetered request is never sent to Models & prices",
        "management/frontend/src/app/features/reporting/reporting-page.html",
        "          @if (unpriced() - unmetered() > 0) {",
        "          @if (unpriced() > 0) {",
        "management/frontend/src/app/features/reporting/reporting-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-11c",
        "a row's tooltip counts unmetered requests apart from those with no price on file",
        "management/frontend/src/app/features/reporting/breakdown-table.ts",
        "    const noPrice = row.unpriced_requests - unmetered;",
        "    const noPrice = row.unpriced_requests;",
        "management/frontend/src/app/features/reporting/breakdown-table.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-13a",
        "an expired suspension leaves the in-force list once the page next reads the suspensions",
        "management/frontend/src/app/features/security/security-page.ts",
        "    this.checkedAt.set(Date.now());",
        "",
        "management/frontend/src/app/features/security/security-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-13b",
        "the live tick re-reads the suspensions, not only the findings",
        "management/frontend/src/app/features/security/security-page.ts",
        "    return forkJoin({ page: this.service.anomalies(), suspensions: this.readSuspensions() });",
        "    return forkJoin({ page: this.service.anomalies(), suspensions: this.loading() ? this.readSuspensions() : of<SuspensionsRead>({ rows: this.suspensions() }) });",
        "management/frontend/src/app/features/security/security-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-14a",
        "Escape closes the rating window",
        "management/frontend/src/app/features/smoketests/rating-window.html",
        '  <app-modal [open]="true" [title]="row.topic" testid="rate-window" (closed)="closeRating()">',
        '  <app-modal [open]="true" [title]="row.topic" testid="rate-window">',
        "management/frontend/src/app/features/smoketests/smoke-tests.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-14b",
        "Escape closes the question editor",
        "management/frontend/src/app/features/smoketests/question-catalogue.html",
        '    (closed)="cancelCase()"',
        "",
        "management/frontend/src/app/features/smoketests/smoke-tests.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-14c",
        "a refused question is shown inside the window, and only once a save was tried there",
        "management/frontend/src/app/features/smoketests/question-catalogue.html",
        "      @if (tried() && feedback.error(); as error) {",
        "      @if (feedback.error(); as error) {",
        "management/frontend/src/app/features/smoketests/smoke-tests.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-14d",
        "a rating that could not be saved is shown inside the rating window",
        "management/frontend/src/app/features/smoketests/rating-window.html",
        "      @if (tried() && feedback.error(); as error) {",
        "      @if (false && feedback.error(); as error) {",
        "management/frontend/src/app/features/smoketests/smoke-tests.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-15",
        "leaving the page stops a run before its next question",
        "management/frontend/src/app/features/smoketests/smoke-tests.ts",
        "        if (this.destroyRef.destroyed) return;",
        "",
        "management/frontend/src/app/features/smoketests/smoke-tests.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-16a",
        "one verdict is stored however often it is clicked while it is being saved",
        "management/frontend/src/app/features/smoketests/rating-window.ts",
        "    if (index === null || !result || this.saving() || neverAsked(result)) return;",
        "    if (index === null || !result || neverAsked(result)) return;",
        "management/frontend/src/app/features/smoketests/smoke-tests.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-16b",
        "a typed note survives stepping to another answer and back",
        "management/frontend/src/app/features/smoketests/rating-window.ts",
        "    this.note.set((row && this.drafts()[row.id]) ?? row?.note ?? '');",
        "    this.note.set(row?.note ?? '');",
        "management/frontend/src/app/features/smoketests/smoke-tests.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-16c",
        "the run opened last is the one shown, whichever answers arrive first",
        "management/frontend/src/app/features/smoketests/rating-window.ts",
        "    this.inFlight = this.service.runResults(run.id).subscribe({",
        "    this.service.runResults(run.id).subscribe({",
        "management/frontend/src/app/features/smoketests/smoke-tests.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-16d",
        "a late save does not step on a run opened since",
        "management/frontend/src/app/features/smoketests/rating-window.ts",
        "        if (this.current()?.id !== saved.id) return;",
        "",
        "management/frontend/src/app/features/smoketests/smoke-tests.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-17-sec-a",
        "the stop form's submit reaches the kill switch",
        "management/frontend/src/app/features/security/suspensions-panel.html",
        '(ngSubmit)="stop()"',
        '(ngSubmit)="showStop.set(false)"',
        "management/frontend/src/app/features/security/security-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-17-sec-b",
        "an edited rule's form is wired to that rule's update",
        "management/frontend/src/app/features/security/security-rules-panel.html",
        '(saved)="saveRule(rule, $event)"',
        '(saved)="cancelEdit()"',
        "management/frontend/src/app/features/security/security-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-17-sec-c",
        "Restore is wired to lifting the suspension, behind a confirmation",
        "management/frontend/src/app/features/security/suspensions-panel.html",
        '(click)="lift(row)"',
        '(click)="changed.emit()"',
        "management/frontend/src/app/features/security/security-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-17-sec-d",
        "Delete is wired to deleting the rule, behind a confirmation",
        "management/frontend/src/app/features/security/security-rules-panel.html",
        '(click)="removeRule(rule)"',
        '(click)="startEdit(rule)"',
        "management/frontend/src/app/features/security/security-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-17-sec-e",
        "the new-global-rule window's form is wired to creating the rule",
        "management/frontend/src/app/features/security/security-rules-panel.html",
        '(saved)="createRule($event)"',
        '(saved)="cancelCreate()"',
        "management/frontend/src/app/features/security/security-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-17-smoke-a",
        "a run row shows its failures apart from its unrated answers",
        "management/frontend/src/app/features/smoketests/smoke-tests.html",
        '                        <span class="badge badge--danger">{{ run.counts.fail }} failed</span>',
        '                        <span class="badge badge--warning">{{ run.counts.fail }} failed</span>',
        "management/frontend/src/app/features/smoketests/smoke-tests.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-17-smoke-b",
        "a run says how far it has got",
        "management/frontend/src/app/features/smoketests/smoke-tests.ts",
        "        this.progress.set(`${index + 1} of ${results.length}`);",
        "",
        "management/frontend/src/app/features/smoketests/smoke-tests.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-19",
        "the installation budget is stated in AIRA_CURRENCY, never in a symbol of the console's own",
        "management/frontend/src/app/features/reporting/installation-budget-card.ts",
        "    return currency ? `${shown} ${currency}` : `spend ${shown}`;",
        "    return `$${limit}`;",
        "management/frontend/src/app/features/reporting/installation-budget-card.spec.ts management/frontend/src/app/features/reporting/reporting-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-20",
        "a half-typed rule edit survives a look at another tab",
        "management/frontend/src/app/features/security/security-rules-panel.html",
        "@if (editing() !== rule.id) {",
        "@if (editing() !== rule.id || !shown()) {",
        "management/frontend/src/app/features/security/security-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-21a",
        "loading older findings drops a refresh already in flight",
        "management/frontend/src/app/features/security/security-page.ts",
        "    this.live.pause();",
        "    this.live.enabled.set(false);",
        "management/frontend/src/app/features/security/security-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-21b",
        "the findings count says when more pages exist",
        "management/frontend/src/app/features/security/security-page.html",
        "{{ moreEvents() ? '+' : '' }}",
        "",
        "management/frontend/src/app/features/security/security-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-22a",
        "a whole use case is stopped with no scope, so the stop can match a request",
        "management/frontend/src/app/features/security/suspensions-panel.ts",
        "    if (target === 'use_case') this.scope.set('');",
        "",
        "management/frontend/src/app/features/security/security-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-22b",
        "a whole-use-case stop offers no Where picker",
        "management/frontend/src/app/features/security/suspensions-panel.html",
        "@if (target() !== 'use_case') {",
        "@if (true) {",
        "management/frontend/src/app/features/security/security-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-22c",
        "a manual throttle is reported as held to a rate, not as stopped",
        "management/frontend/src/app/features/security/suspensions-panel.ts",
        "        ? `is held to ${this.throttleRpm()} requests a minute`",
        "        ? 'is stopped'",
        "management/frontend/src/app/features/security/security-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-23",
        "a past decision shows when it started as well as when it ended",
        "management/frontend/src/app/features/security/suspensions-panel.html",
        "{{ row.created_at ? (row.created_at | date: 'yyyy-MM-dd HH:mm') : '—' }}",
        "—",
        "management/frontend/src/app/features/security/security-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-24",
        "a refused run history is the screen withheld, not a red banner",
        "management/frontend/src/app/features/smoketests/smoke-tests.ts",
        "        this.refusedOr(response, 'Could not load the runs.');",
        "        this.feedback.fail(response, 'Could not load the runs.');",
        "management/frontend/src/app/features/smoketests/smoke-tests.spec.ts",
    ),
    Mutation(
        "SMOKE-ANSWER-1",
        "no tab is shown before the server has said whether the screen is the reader's",
        "management/frontend/src/app/features/smoketests/smoke-tests.html",
        "  } @else if (!answered()) {",
        "  } @else if (false) {",
        "management/frontend/src/app/features/smoketests/smoke-tests.spec.ts",
    ),
    Mutation(
        "SMOKE-ANSWER-2",
        "the first answer that is not a refusal shows the tabs, whichever read gives it",
        "management/frontend/src/app/features/smoketests/smoke-tests.ts",
        "        this.attributionKnown.set(true);\n        this.answered.set(true);",
        "        this.attributionKnown.set(true);",
        "management/frontend/src/app/features/smoketests/smoke-tests.spec.ts",
    ),
    Mutation(
        "SMOKE-ANSWER-3",
        "an ordinary failure is an answer: the screen stays, with the reason",
        "management/frontend/src/app/features/smoketests/smoke-tests.ts",
        "    this.answered.set(true);\n    this.feedback.fail(response, failure);",
        "    this.feedback.fail(response, failure);",
        "management/frontend/src/app/features/smoketests/smoke-tests.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-25a",
        "a later successful register load takes the failed load's banner down",
        "management/frontend/src/app/features/governance/register-page.ts",
        "    this.feedback.clear();",
        "",
        "management/frontend/src/app/features/governance/register-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-25b",
        "a new reporting period re-asks a budget card whose load failed instead of leaving it blank",
        "management/frontend/src/app/features/reporting/reporting-page.ts",
        "    this.budgetCard()?.retryIfFailed();",
        "",
        "management/frontend/src/app/features/reporting/reporting-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-26b",
        "the register's Show is disabled for a window that ends before it starts",
        "management/frontend/src/app/features/governance/register-page.html",
        '        <button type="submit" class="btn btn--primary" [disabled]="!canLoad()">Show</button>',
        '        <button type="submit" class="btn">Show</button>',
        "management/frontend/src/app/features/governance/register-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-26c",
        "the register never asks the gateway for a reversed window",
        "management/frontend/src/app/features/governance/register-page.ts",
        "    if (this.validationError()) {",
        "    if (!this.from() || !this.to()) {",
        "management/frontend/src/app/features/governance/register-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-27cs",
        "a period reading names the UTC clock its days are on",
        "management/frontend/src/app/core/ui/periods.ts",
        "  return window ? `${window.from} to ${lastDayOf(window.to)} (${PERIOD_CLOCK})` : '';",
        "  return window ? `${window.from} to ${lastDayOf(window.to)}` : '';",
        "management/frontend/src/app/core/ui/periods.spec.ts management/frontend/src/app/features/reporting/reporting-page.spec.ts management/frontend/src/app/features/governance/register-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-28a",
        "a failed use-case list is said at the scope picker",
        "management/frontend/src/app/features/security/security-page.ts",
        "        this.useCasesError.set(errorMessage(response, 'The use cases could not be loaded.')),",
        "        this.useCasesError.set(null),",
        "management/frontend/src/app/features/security/security-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-28b",
        "a reader whose permissions could not be read is not told who else stops traffic",
        "management/frontend/src/app/features/security/security-page.ts",
        "        this.permissionsKnown.set(false);",
        "",
        "management/frontend/src/app/features/security/security-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-28c",
        "a findings list narrowed to the reader's use cases says so",
        "management/frontend/src/app/features/security/security-page.html",
        "@if (eventsScope() === 'use_cases') {",
        "@if (false) {",
        "management/frontend/src/app/features/security/security-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-29a",
        "a rule's sentence says its threshold is reached, not exceeded",
        "management/frontend/src/app/features/security/rule-language.ts",
        "    `At ${thresholdText(rule.kind, rule.threshold)} or more, ${consequence(rule)}.${sample}`",
        "    `Above ${thresholdText(rule.kind, rule.threshold)}, ${consequence(rule)}.${sample}`",
        "management/frontend/src/app/features/security/rule-language.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-29b",
        "the threshold field is labelled as reached, not exceeded",
        "management/frontend/src/app/features/security/rule-form.ts",
        "          <label [attr.for]=\"id('threshold')\">At or above</label>",
        "          <label [attr.for]=\"id('threshold')\">Above</label>",
        "management/frontend/src/app/features/security/rule-form.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-2a",
        "every question of a run is asked through the use case and model the run recorded, not wherever the pickers point now",
        "management/frontend/src/app/features/smoketests/smoke-tests.ts",
        "        this.service.askModel(run.model, result.prompt, run.use_case),",
        "        this.service.askModel(this.startModel(), result.prompt, this.useCase()),",
        "management/frontend/src/app/features/smoketests/smoke-tests.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-2b",
        "the use-case picker is not offered while a run is asking",
        "management/frontend/src/app/features/smoketests/smoke-tests.html",
        '                  (ngModelChange)="chooseUseCase($event)"\n                  [disabled]="running()"',
        '                  (ngModelChange)="chooseUseCase($event)"',
        "management/frontend/src/app/features/smoketests/smoke-tests.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-30",
        "a finding with no use case is explained by a global rule, never by a use-case rule of the same name",
        "management/frontend/src/app/features/security/security-page.ts",
        "    if (event.use_case === null) return named.find((rule) => rule.is_global);",
        "    if (event.use_case === null) return named[0];",
        "management/frontend/src/app/features/security/security-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-31a",
        "the register asks Management for its catalogue once, not per period",
        "management/frontend/src/app/features/governance/register-page.ts",
        "    if (this.authored() || this.catalogueRequest?.closed === false) {",
        "    if (this.catalogueRequest?.closed === false) {",
        "management/frontend/src/app/features/governance/register-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-31b",
        "a catalogue comparison that could not be made is said, not shown as agreement",
        "management/frontend/src/app/features/governance/register-page.html",
        "  } @else if (seesEverything() && catalogueError(); as reason) {",
        "  } @else if (false && catalogueError(); as reason) {",
        "management/frontend/src/app/features/governance/register-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-32-rep-a",
        "the report file is named by the last day it includes",
        "management/frontend/src/app/features/reporting/reporting-page.ts",
        "        saveBlob(blob, `aira-usage_${breakdown}_${shown.from}_${lastDayOf(shown.to)}.csv`);",
        "        saveBlob(blob, `aira-usage_${breakdown}_${shown.from}_${shown.to}.csv`);",
        "management/frontend/src/app/features/reporting/reporting-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-32-rep-b",
        "the register file is named by the last day it includes",
        "management/frontend/src/app/features/governance/register-page.ts",
        "        saveBlob(blob, `aira-register_${shown.from}_${lastDayOf(shown.to)}.csv`);",
        "        saveBlob(blob, `aira-register_${shown.from}_${shown.to}.csv`);",
        "management/frontend/src/app/features/governance/register-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-32-smoke-a",
        "a failed request is stored in the words of either error envelope",
        "management/frontend/src/app/features/smoketests/run-results.ts",
        "  return errorMessage(error, fallback).slice(0, FAILURE_NOTE_MAX);",
        "  return ((error as { error?: { error?: { message?: string } } })?.error?.error?.message ?? fallback).slice(0, FAILURE_NOTE_MAX);",
        "management/frontend/src/app/features/smoketests/smoke-tests.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-32-smoke-b",
        "a run's export names the run it holds",
        "management/frontend/src/app/features/smoketests/run-results.ts",
        "  return `aira-smoketest_${useCase}_run-${run.id}_${day}.csv`;",
        "  return `aira-smoketest_${useCase}_${day}.csv`;",
        "management/frontend/src/app/features/smoketests/smoke-tests.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-32-smoke-c",
        "a run is exported once however often its button is pressed",
        "management/frontend/src/app/features/smoketests/smoke-tests.ts",
        "    if (this.exporting() !== null) return;",
        "",
        "management/frontend/src/app/features/smoketests/smoke-tests.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-33-rep-a",
        "a breakdown row's request count is grouped like every other count",
        "management/frontend/src/app/features/reporting/breakdown-table.html",
        "                  {{ count(row.requests) }}",
        "                  {{ row.requests }}",
        "management/frontend/src/app/features/reporting/breakdown-table.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-33-rep-b",
        "the register's erasure counts are grouped",
        "management/frontend/src/app/features/governance/register-page.html",
        "          {{ count(erasure.payloads_cleared) }} payload(s) cleared,",
        "          {{ erasure.payloads_cleared }} payload(s) cleared,",
        "management/frontend/src/app/features/governance/register-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-33-sec",
        "a card-header create action is the small button size on every security tab",
        "management/frontend/src/app/features/security/security-rules-panel.html",
        '        class="btn btn--sm btn--primary"',
        '        class="btn btn--primary"',
        "management/frontend/src/app/features/security/security-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-33-smoke-load",
        "a failed run-history load stops saying it is loading",
        "management/frontend/src/app/features/smoketests/smoke-tests.ts",
        "        this.runsKnown.set(true);\n        this.refusedOr",
        "        this.refusedOr",
        "management/frontend/src/app/features/smoketests/smoke-tests.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-34-rep-a",
        "the breakdown search names its row with the right article",
        "management/frontend/src/app/features/reporting/breakdown-table.html",
        "        <span>Find {{ item() }}</span>",
        "        <span>Find a {{ label().toLowerCase() }}</span>",
        "management/frontend/src/app/features/reporting/breakdown-table.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-34-rep-b",
        "another breakdown opens unfiltered",
        "management/frontend/src/app/features/reporting/breakdown-table.ts",
        "      this.view.search('');",
        "",
        "management/frontend/src/app/features/reporting/breakdown-table.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-34-sec",
        "a rule's sentence states how long its automatic action lasts",
        "management/frontend/src/app/features/security/rule-language.ts",
        "        ? `traffic is ${effect} for ${rule.action_minutes} minutes`",
        "        ? `traffic is ${effect}`",
        "management/frontend/src/app/features/security/rule-language.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-3a",
        "a new question is placed after the highest active position, never at count + 1",
        "management/frontend/src/app/features/smoketests/question-catalogue.ts",
        "      position: draft.id ? (draft.position ?? 0) : nextPosition(this.cases()),",
        "      position: draft.id ? (draft.position ?? 0) : this.cases().length + 1,",
        "management/frontend/src/app/features/smoketests/smoke-tests.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-3b",
        "a question is posted once however often Save is pressed while it is on its way",
        "management/frontend/src/app/features/smoketests/question-catalogue.ts",
        "    if (!draft || this.feedback.busy()) return;",
        "    if (!draft) return;",
        "management/frontend/src/app/features/smoketests/smoke-tests.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-4a",
        "a run opens on an answered question, never on one the run did not ask",
        "management/frontend/src/app/features/smoketests/rating-window.ts",
        "          (row) => row.verdict === 'unrated' && !row.error && !neverAsked(row),",
        "          (row) => row.verdict === 'unrated' && !row.error,",
        "management/frontend/src/app/features/smoketests/smoke-tests.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-4b",
        "no verdict is stored on a question the run never asked",
        "management/frontend/src/app/features/smoketests/rating-window.ts",
        "    if (index === null || !result || this.saving() || neverAsked(result)) return;",
        "    if (index === null || !result || this.saving()) return;",
        "management/frontend/src/app/features/smoketests/smoke-tests.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-4c",
        "a run that never finished says so on its row",
        "management/frontend/src/app/features/smoketests/smoke-tests.html",
        "                      @if (run.finished_at === null) {",
        "                      @if (false) {",
        "management/frontend/src/app/features/smoketests/smoke-tests.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-5cs-a",
        "a failed live findings load is reported and ends the loading state",
        "management/frontend/src/app/features/security/security-page.ts",
        "      (response) => this.reportFindingsFailure(response),",
        "      () => undefined,",
        "management/frontend/src/app/features/security/security-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-5cs-b",
        "a findings banner is taken down once the findings load again",
        "management/frontend/src/app/features/security/security-page.ts",
        "    if (failure && this.feedback.error() === failure) this.feedback.error.set(null);",
        "",
        "management/frontend/src/app/features/security/security-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-6a",
        "a spike threshold stored in percent is shown as the multiple it means, never as the stored number",
        "management/frontend/src/app/features/security/rule-language.ts",
        "  if (RATIO_KINDS.has(kind)) return `${multiple(value)} the previous window`;",
        "",
        "management/frontend/src/app/features/security/rule-language.spec.ts management/frontend/src/app/features/security/security-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-6b",
        "the rules table prints a threshold through the one formatter, not as a bare stored number",
        "management/frontend/src/app/features/security/security-rules-panel.html",
        "                {{ threshold(rule) }}",
        "                {{ rule.threshold }}",
        "management/frontend/src/app/features/security/security-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-6c",
        "a finding's measurement and threshold are shown in the unit they mean",
        "management/frontend/src/app/features/security/security-page.ts",
        "    return figureText(event.kind, value);",
        "    return String(value);",
        "management/frontend/src/app/features/security/security-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-6d",
        "the threshold field's hint reads the typed percent back as a multiple",
        "management/frontend/src/app/features/security/rule-form.ts",
        "  protected readonly hint = computed(() => thresholdHint(this.kind(), this.threshold()));",
        "  protected readonly hint = computed(() => thresholdHint(this.kind(), null));",
        "management/frontend/src/app/features/security/rule-form.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-7a",
        "an automatic action without a 1-10080 minute duration cannot be saved",
        "management/frontend/src/app/features/security/rule-form.ts",
        "    if (this.action() !== 'alert' && !whole(this.minutes(), 1, MAX_ACTION_MINUTES)) {",
        "    if (false) {",
        "management/frontend/src/app/features/security/rule-form.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-7b",
        "a rate or a ratio is never judged on fewer than one request",
        "management/frontend/src/app/features/security/rule-form.ts",
        "    if (needsSample(kind) && !whole(this.sample(), 1)) {",
        "    if (needsSample(kind) && !whole(this.sample(), 0)) {",
        "management/frontend/src/app/features/security/rule-form.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-7c",
        "an event kind is offered no smallest sample the server overrides",
        "management/frontend/src/app/features/security/rule-form.ts",
        "        @if (needsSample(kind())) {",
        "        @if (true) {",
        "management/frontend/src/app/features/security/rule-form.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-7d",
        "a size rule cannot be saved without its byte figure",
        "management/frontend/src/app/features/security/rule-form.ts",
        "    if (kind === 'payload_size' && !whole(this.parameter(), 1)) {",
        "    if (false) {",
        "management/frontend/src/app/features/security/rule-form.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-7e",
        "a throttle rule cannot be saved without its rate",
        "management/frontend/src/app/features/security/rule-form.ts",
        "    if (this.action() === 'throttle' && !whole(this.rpm(), 1)) {",
        "    if (false) {",
        "management/frontend/src/app/features/security/rule-form.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-7f",
        "a rule whose automatic action names no duration is not described as stopping traffic",
        "management/frontend/src/app/features/security/rule-language.ts",
        "        : `traffic would be ${effect}, but the rule names no duration, so nothing is enforced — ` +",
        "        : `traffic is ${effect} until somebody lifts it` +",
        "management/frontend/src/app/features/security/rule-language.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-7g",
        "a spike threshold at or below 100 percent cannot be saved",
        "management/frontend/src/app/features/security/rule-form.ts",
        "    if (isMultiple(kind) && threshold! <= MAX_SHARE) {",
        "    if (false) {",
        "management/frontend/src/app/features/security/rule-form.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-7h",
        "a share threshold above 100 percent cannot be saved",
        "management/frontend/src/app/features/security/rule-form.ts",
        "    if (isShare(kind) && threshold! > MAX_SHARE) {",
        "    if (false) {",
        "management/frontend/src/app/features/security/rule-form.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-8a",
        "a report answer that arrives after a newer request is dropped, not shown under the newer period",
        "management/frontend/src/app/features/reporting/reporting-page.ts",
        "    this.request?.unsubscribe();",
        "",
        "management/frontend/src/app/features/reporting/reporting-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-8b",
        "a register answer that arrives after a newer request is dropped",
        "management/frontend/src/app/features/governance/register-page.ts",
        "    this.request?.unsubscribe();",
        "",
        "management/frontend/src/app/features/governance/register-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-9a",
        "the report CSV is the period on screen, not the date fields",
        "management/frontend/src/app/features/reporting/reporting-page.ts",
        "    this.service.reportCsv(shown.from, shown.to, breakdown).subscribe({",
        "    this.service.reportCsv(this.from(), this.to(), breakdown).subscribe({",
        "management/frontend/src/app/features/reporting/reporting-page.spec.ts",
    ),
    Mutation(
        "FX-F34-F4-9b",
        "the register CSV is the period on screen, not the date fields",
        "management/frontend/src/app/features/governance/register-page.ts",
        "    this.service.registerCsv(shown.from, shown.to).subscribe({",
        "    this.service.registerCsv(this.from(), this.to()).subscribe({",
        "management/frontend/src/app/features/governance/register-page.spec.ts",
    ),
    Mutation(
        "FX-F34-H1",
        "a window takes a modifier class without losing its own",
        "management/frontend/src/app/core/ui/modal.html",
        '    [class]="panelClass()"\n',
        "",
        "management/frontend/src/app/core/ui/modal.spec.ts",
    ),
    Mutation(
        "FX-F34-H2",
        "a saved blob's object URL is released only after the click",
        "management/frontend/src/app/core/ui/save-blob.ts",
        "  link.click();\n  URL.revokeObjectURL(url);",
        "  URL.revokeObjectURL(url);\n  link.click();",
        "management/frontend/src/app/core/ui/save-blob.spec.ts",
    ),
    Mutation(
        "FX-G1-1",
        "a query-string credential reaches no attribute of the inbound server span",
        "gateway/src/aira_gateway/app.py",
        "    redact_span_urls(span)",
        "    pass",
        "gateway/tests/test_hardening.py",
    ),
    Mutation(
        "FX-G1-10",
        "a key set that answers but cannot be read is the provider, logged as such, not a 500",
        "libs/src/aira_common/oidc.py",
        "        except (ValueError, OSError, http.client.HTTPException) as exc:",
        "        except (ZeroDivisionError,) as exc:",
        "libs/tests/test_when_the_identity_provider_is_gone.py",
    ),
    Mutation(
        "FX-G1-11",
        "a gateway without Kafka is ready on Postgres alone",
        "gateway/src/aira_gateway/config.py",
        "        return parse_bootstrap_servers(self.kafka_bootstrap_servers)\n",
        '        return parse_bootstrap_servers(self.kafka_bootstrap_servers) or (("localhost", 9092),)\n',
        "gateway/tests/test_health.py",
    ),
    Mutation(
        "FX-G1-11b",
        "an IPv6 broker is dialled without its brackets",
        # The gateway's copy of the parser is gone; the property lives in the one both planes read.
        "libs/src/aira_common/kafka.py",
        '        host, closed, rest = entry[1:].partition("]")',
        '        host, closed, rest = entry.partition("]")',
        "gateway/tests/test_health.py gateway/tests/test_config.py",
    ),
    Mutation(
        "FX-G1-12",
        "the database URL carries the credentials exactly",
        "gateway/src/aira_gateway/config.py",
        '        return URL.create(\n            "postgresql+psycopg",\n            username=self.postgres_user,\n            password=self.postgres_password,\n            host=self.postgres_host,\n            port=self.postgres_port,\n            database=self.postgres_db,\n        ).render_as_string(hide_password=False)',
        '        return (\n            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"\n            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"\n        )',
        "gateway/tests/test_config.py",
    ),
    Mutation(
        "FX-G1-13",
        "a new comparison against a stored identity cannot be added unnamed",
        "gateway/src/aira_gateway/auth/dependencies.py",
        '    if principal.method == "oidc" and use_case not in principal.use_cases:',
        '    if principal.method == "oidc" and use_case not in principal.use_cases and use_case != principal.subject:',
        "gateway/tests/test_one_identity_across_credentials.py",
    ),
    Mutation(
        "FX-G1-14",
        "the KIRA 429 from the failed-authentication bound says when to come back",
        "gateway/src/aira_gateway/exception_handlers.py",
        "        return surface_refusal(request, exc.code, code, exc.message, headers=exc.headers)",
        "        return surface_refusal(request, exc.code, code, exc.message)",
        "gateway/tests/test_kira_envelope_everywhere.py",
    ),
    Mutation(
        "FX-G1-17",
        "a 405 keeps its Allow header on every surface",
        "gateway/src/aira_gateway/exception_handlers.py",
        "    headers = dict(exc.headers or {})",
        "    headers: dict[str, str] = {}",
        "gateway/tests/test_error_handling.py",
    ),
    Mutation(
        "FX-G1-18",
        "every attributed row carries the realm that minted the token",
        "gateway/src/aira_gateway/auth/attribution.py",
        "            issuer=principal.issuer,",
        "            issuer=None,",
        "gateway/tests/test_model_check.py gateway/tests/test_attribution.py",
    ),
    Mutation(
        "FX-G1-19",
        "the KIRA health endpoint names upstreams to an operator only",
        "gateway/src/aira_gateway/api/kira/info_routes.py",
        "    listed = checks if await may_see_diagnosis(request) else checks[:1]",
        "    listed = checks",
        "gateway/tests/test_readyz_diagnosis_is_for_operators.py",
    ),
    Mutation(
        "FX-G1-2",
        "the published demo key is a credential only while the deployment declares demo mode",
        "gateway/src/aira_gateway/auth/service.py",
        "        if prefix == keys.DEMO_KEY_PREFIX and not self._demo_mode:",
        "        if False:",
        "gateway/tests/test_auth_routes.py gateway/tests/test_auth_service.py",
    ),
    Mutation(
        "FX-G1-20",
        "callers finding the grant cache expired read the tables once",
        "gateway/src/aira_gateway/auth/grants.py",
        "            if not self._settled(now):",
        "            if True:",
        "gateway/tests/test_group_grants.py",
    ),
    Mutation(
        "FX-G1-21",
        "an unhandled error is readable cross-origin",
        "gateway/src/aira_gateway/exception_handlers.py",
        "    await apply_cors(request, response)",
        "    pass",
        "gateway/tests/test_diagnostics.py",
    ),
    Mutation(
        "FX-G1-22",
        "a deployment does not publish its own API map",
        "gateway/src/aira_gateway/app.py",
        "    documented = is_local_or_demo(settings)",
        "    documented = True",
        "gateway/tests/test_app.py",
    ),
    Mutation(
        "FX-G1-23",
        "a request ceiling that refuses everything refuses to start",
        "gateway/src/aira_gateway/config.py",
        "    max_request_bytes: int = Field(default=8 * 1024 * 1024, gt=0)",
        "    max_request_bytes: int = 8 * 1024 * 1024",
        "gateway/tests/test_config.py",
    ),
    Mutation(
        "FX-G1-25",
        "a credential type with no rule of its own is admitted to no use case",
        "gateway/src/aira_gateway/auth/dependencies.py",
        "    elif principal.method not in CREDENTIAL_METHODS:",
        "    elif False:",
        "gateway/tests/test_selector_never_grants.py",
    ),
    Mutation(
        "FX-G1-27",
        "a name longer than the column is not cut into somebody else's",
        "gateway/src/aira_gateway/auth/oidc.py",
        "            (name if len(name) <= _MAX_USERNAME else str(subject)[:_MAX_USERNAME])",
        "            name[:_MAX_USERNAME]",
        "gateway/tests/test_the_identity_boundary_takes_any_value.py",
    ),
    Mutation(
        "FX-G1-28",
        "the missing-audience warning is given",
        "gateway/src/aira_gateway/auth/oidc.py",
        "    if any(not audience for _, audience, _ in configured):",
        "    if False:",
        "gateway/tests/test_auth_oidc.py",
    ),
    Mutation(
        "FX-G1-29",
        "the clock skew setting reaches the verifier",
        "gateway/src/aira_gateway/auth/oidc.py",
        "        clock_skew_seconds=settings.oidc_clock_skew_seconds,",
        "",
        "gateway/tests/test_auth_oidc.py",
    ),
    Mutation(
        "FX-G1-29b",
        "a token cannot choose a symmetric or no algorithm",
        "libs/src/aira_common/oidc.py",
        "        self._algorithms = list(algorithms)",
        '        self._algorithms = [*algorithms, "HS256", "none"]',
        "gateway/tests/test_auth_oidc.py",
    ),
    Mutation(
        "FX-G1-2b",
        "a deployment that is no longer a demo revokes the demo key it inherited",
        "gateway/src/aira_gateway/app.py",
        "        elif not is_local_environment(settings):",
        "        elif False:",
        "gateway/tests/test_auth_routes.py",
    ),
    Mutation(
        "FX-G1-3",
        "a caller's field names reach the response header only Latin-1-safe and bounded",
        "gateway/src/aira_gateway/api/kira/headers.py",
        "        **({UNMODELLED_HEADER: unmodelled_header(unmodelled)} if unmodelled else {}),",
        '        **({UNMODELLED_HEADER: ", ".join(unmodelled)} if unmodelled else {}),',
        "gateway/tests/test_no_silent_drop.py",
    ),
    Mutation(
        "FX-G1-30",
        "local is one decision however it is spelled",
        "gateway/src/aira_gateway/app.py",
        "    if is_local_or_demo(settings):\n        providers.append(MockProvider())",
        '    if settings.environment == "local" or settings.demo_mode:\n        providers.append(MockProvider())',
        "gateway/tests/test_app.py",
    ),
    Mutation(
        "FX-G1-31",
        "a failed probe reports its type, never its message",
        "gateway/src/aira_gateway/diagnostics.py",
        "                describe_failure(exc),",
        '                f"{type(exc).__name__}: {exc}"[:120],',
        "gateway/tests/test_diagnostics.py",
    ),
    Mutation(
        "FX-G1-4",
        "a credential refused by /readyz counts against the failed-authentication bound",
        "gateway/src/aira_gateway/routes/health.py",
        "            await record_failed_authentication(request)",
        "            pass",
        "gateway/tests/test_auth_attempt_bound.py",
    ),
    Mutation(
        "FX-G1-5",
        "a key prefix no key could have never reaches the database",
        "libs/src/aira_common/apikeys.py",
        "    return parts[1] if _PREFIX_SHAPE.fullmatch(parts[1]) else None",
        "    return parts[1]",
        "gateway/tests/test_auth_routes.py gateway/tests/test_a_credential_is_answered_for.py gateway/tests/test_auth_keys.py",
    ),
    Mutation(
        "FX-G1-6",
        "the break-glass key is bounded like every key: 1 to AIRA_API_KEY_MAX_DAYS days",
        "gateway/src/aira_gateway/auth/service.py",
        "        check_key_lifetime(days, _max_key_days())",
        "        pass",
        "gateway/tests/test_cli.py gateway/tests/test_auth_service.py",
    ),
    Mutation(
        "FX-G1-7",
        "the CLI builds no schema beside the migrations on a real database",
        "gateway/src/aira_gateway/cli.py",
        '        if engine.dialect.name == "sqlite":',
        "        if True:",
        "gateway/tests/test_cli.py",
    ),
    Mutation(
        "FX-G1-7b",
        "create_all refuses a database the migrations own",
        "gateway/src/aira_gateway/db/base.py",
        '    if engine.dialect.name != "sqlite":',
        "    if False:",
        "gateway/tests/test_cli.py",
    ),
    Mutation(
        "FX-G1-8",
        "an unhandled error carries the trace id its log line carries",
        "gateway/src/aira_gateway/exception_handlers.py",
        '        response.headers.setdefault(TraceIdMiddleware.HEADER.decode("ascii"), trace_id)',
        "        pass",
        "gateway/tests/test_diagnostics.py",
    ),
    Mutation(
        "FX-G1-9",
        "an oversized bearer is refused before anything parses it",
        "gateway/src/aira_gateway/auth/dependencies.py",
        "    if len(token) > MAX_TOKEN_LENGTH:",
        "    if False:",
        "gateway/tests/test_auth_routes.py",
    ),
    Mutation(
        "FX-G1-9b",
        "a payload nested past the JSON parser is a rejected token, not a 500",
        "gateway/src/aira_gateway/auth/oidc.py",
        "        except jwt.PyJWTError, ValueError, RecursionError:",
        "        except jwt.PyJWTError:",
        "gateway/tests/test_auth_oidc.py",
    ),
    Mutation(
        "FX-G1-9c",
        "a header nested past the JSON parser is a rejected token, not an escaped exception",
        "libs/src/aira_common/oidc.py",
        "        except RecursionError as exc:",
        "        except ZeroDivisionError as exc:",
        "libs/tests/test_when_the_identity_provider_is_gone.py",
    ),
    Mutation(
        "FX-G3-10",
        "what a turn says beside its tool results reaches an OpenAI-dialect model",
        "gateway/src/aira_gateway/upstreams/openai/mapping/request.py",
        "        if results and said:",
        "        if False:",
        "gateway/tests/test_openai_dialect.py",
    ),
    Mutation(
        "FX-G3-11",
        "every system message reaches Gemini's systemInstruction",
        "gateway/src/aira_gateway/upstreams/gemini_mapping.py",
        "            system_parts.extend(_wire_parts(message))",
        "            system_parts[:] = _wire_parts(message)",
        "gateway/tests/test_gemini_upstream_mapping.py",
    ),
    Mutation(
        "FX-G3-12a",
        "a streamed refusal is read for its reason on every transport (re-anchors TC6)",
        "gateway/src/aira_gateway/upstreams/exchange.py",
        "                    await response.aread()",
        "                    pass",
        "gateway/tests/test_an_upstream_failure_is_never_a_silent_end.py gateway/tests/test_openai_dialect.py",
    ),
    Mutation(
        "FX-G3-12b",
        "a refusal carries the provider's reason for a 400 (re-anchors TC36)",
        "gateway/src/aira_gateway/upstreams/exchange.py",
        '    detail = upstream_reason(response) if response.status_code == 400 else ""',
        '    detail = ""',
        "gateway/tests/test_an_upstream_failure_is_never_a_silent_end.py gateway/tests/test_vertex.py",
    ),
    Mutation(
        "FX-G3-12c",
        "and only for a 400, never a credential error (re-anchors TC37 and Z22)",
        "gateway/src/aira_gateway/upstreams/exchange.py",
        '    detail = upstream_reason(response) if response.status_code == 400 else ""',
        "    detail = upstream_reason(response)",
        "gateway/tests/test_vertex.py gateway/tests/test_openai_dialect.py",
    ),
    Mutation(
        "FX-G3-13a",
        "a blocked prompt is a refusal, never an empty answer that finished",
        "gateway/src/aira_gateway/upstreams/gemini_mapping.py",
        '            model=model, text="", finish_reason=FinishReason.REFUSAL, usage=_usage_of(data)',
        '            model=model, text="", finish_reason=FinishReason.STOP, usage=_usage_of(data)',
        "gateway/tests/test_gemini_upstream_mapping.py",
    ),
    Mutation(
        "FX-G3-13b",
        "an answer with no choice is the upstream's fault",
        "gateway/src/aira_gateway/upstreams/openai/mapping/response.py",
        '    if not choices:\n        raise UpstreamError("The upstream answered with no choice.", 502)',
        '    if False:\n        raise UpstreamError("The upstream answered with no choice.", 502)',
        "gateway/tests/test_openai_dialect.py",
    ),
    Mutation(
        "FX-G3-14",
        "an Entra token that cannot be had is an upstream 503, not a 500",
        "gateway/src/aira_gateway/upstreams/foundry/transport.py",
        "                raise UpstreamError(CREDENTIALS_UNAVAILABLE, 503) from exc",
        "                raise",
        "gateway/tests/test_foundry.py",
    ),
    Mutation(
        "FX-G3-15",
        "Azure gets the output cap in max_completion_tokens",
        "gateway/src/aira_gateway/upstreams/foundry/routes.py",
        '        return "max_completion_tokens"',
        '        return "max_tokens"',
        "gateway/tests/test_foundry.py",
    ),
    Mutation(
        "FX-G3-16a",
        "a region named twice is asked once",
        "gateway/src/aira_gateway/api/incidents/diagnostics.py",
        "    asked = list(dict.fromkeys(part for part in named if part))",
        "    asked = [part for part in named if part]",
        "gateway/tests/test_model_check.py",
    ),
    Mutation(
        "FX-G3-16b",
        "more regions than one press may ask are refused by name",
        "gateway/src/aira_gateway/api/incidents/diagnostics.py",
        "    if len(asked) > MAX_REGIONS_PER_CHECK:",
        "    if False:",
        "gateway/tests/test_model_check.py",
    ),
    Mutation(
        "FX-G3-17a",
        "an AI Studio model name cannot escape its path segment",
        "gateway/src/aira_gateway/upstreams/gemini.py",
        "    return f\"/models/{quote(model, safe='@')}:{method}\"",
        '    return f"/models/{model}:{method}"',
        "gateway/tests/test_gemini_upstream.py",
    ),
    Mutation(
        "FX-G3-17b",
        "the AI Studio key travels in a header, never in a URL",
        "gateway/src/aira_gateway/upstreams/gemini.py",
        '        return {"x-goog-api-key": self._api_key}',
        "        return {}",
        "gateway/tests/test_gemini_upstream.py",
    ),
    Mutation(
        "FX-G3-18a",
        "a Vertex probe asks the region it reports on",
        "gateway/src/aira_gateway/upstreams/vertex/adapters.py",
        "        asked = _declared_regions(addressing or {})",
        "        asked = ()",
        "gateway/tests/test_vertex.py",
    ),
    Mutation(
        "FX-G3-18b",
        "a thinking verdict names the region that answered when it was not the one asked",
        "gateway/src/aira_gateway/api/incidents/thinking_check.py",
        "    if region and served and served != region:",
        "    if False:",
        "gateway/tests/test_model_check.py",
    ),
    Mutation(
        "FX-G3-19",
        "connecting is bounded apart from answering",
        "gateway/src/aira_gateway/upstreams/exchange.py",
        "    return httpx.Timeout(seconds, connect=min(seconds, CONNECT_TIMEOUT_SECONDS))",
        "    return httpx.Timeout(seconds)",
        "gateway/tests/test_an_upstream_failure_is_never_a_silent_end.py gateway/tests/test_gemini_upstream.py",
    ),
    Mutation(
        "FX-G3-1a",
        "an Anthropic error event inside a 200 stream is raised as the upstream failing, so the chain moves on",
        "gateway/src/aira_gateway/upstreams/vertex/anthropic_mapping/response.py",
        '        if kind == "error":',
        '        if kind == "never":',
        "gateway/tests/test_vertex.py gateway/tests/test_an_upstream_failure_is_never_a_silent_end.py",
    ),
    Mutation(
        "FX-G3-1b",
        "a Gemini error payload inside a 200 stream is raised as the upstream failing",
        "gateway/src/aira_gateway/upstreams/gemini_mapping.py",
        '    if data.get("error") is not None:',
        "    if False:",
        "gateway/tests/test_gemini_upstream_mapping.py gateway/tests/test_an_upstream_failure_is_never_a_silent_end.py",
    ),
    Mutation(
        "FX-G3-1c",
        "an OpenAI-dialect error payload inside a 200 stream is raised as the upstream failing",
        "gateway/src/aira_gateway/upstreams/openai/mapping/response.py",
        '    if data.get("error") is not None:',
        "    if False:",
        "gateway/tests/test_openai_dialect.py gateway/tests/test_an_upstream_failure_is_never_a_silent_end.py",
    ),
    Mutation(
        "FX-G3-20",
        "thinking switched off is said to Anthropic, not left to the model's default",
        "gateway/src/aira_gateway/upstreams/vertex/anthropic_mapping/request.py",
        '        body["thinking"] = {"type": "disabled"}',
        "        pass",
        "gateway/tests/test_vertex.py",
    ),
    Mutation(
        "FX-G3-21a",
        "a service-account key that cannot sign is refused at startup",
        "gateway/src/aira_gateway/upstreams/vertex/auth.py",
        '            jwt.encode({"aud": account.token_uri}, account.private_key, algorithm="RS256")',
        "            pass",
        "gateway/tests/test_vertex.py",
    ),
    Mutation(
        "FX-G3-21b",
        "service-account JSON that is not an object is refused by name",
        "gateway/src/aira_gateway/upstreams/vertex/auth.py",
        "        if not isinstance(data, dict):",
        "        if False:",
        "gateway/tests/test_vertex.py",
    ),
    Mutation(
        "FX-G3-22",
        "a credential failure's cause is the operator's, not the caller's",
        "gateway/src/aira_gateway/upstreams/vertex/transport.py",
        "            raise UpstreamError(CREDENTIALS_UNAVAILABLE, 503) from exc",
        '            raise UpstreamError(f"Vertex credentials unavailable: {exc}", 503) from exc',
        "gateway/tests/test_vertex.py",
    ),
    Mutation(
        "FX-G3-23a",
        "AI Studio's reachability check looks the model up in its listing",
        "gateway/src/aira_gateway/upstreams/gemini.py",
        "        if model not in {entry.name for entry in offered}:",
        "        if False:",
        "gateway/tests/test_gemini_upstream.py",
    ),
    Mutation(
        "FX-G3-23b",
        "a self-hosted server's reachability check looks the model up in its listing",
        "gateway/src/aira_gateway/upstreams/openai/adapter.py",
        '        if model not in {str(entry.get("id")) for entry in entries}:',
        "        if False:",
        "gateway/tests/test_openai_dialect.py",
    ),
    Mutation(
        "FX-G3-23c",
        "an unlisted model is reported unreachable with the gateway's reason",
        "gateway/src/aira_gateway/api/incidents/model_check.py",
        "    except ModelNotListed as exc:",
        "    except ZeroDivisionError as exc:",
        "gateway/tests/test_model_check.py",
    ),
    Mutation(
        "FX-G3-25",
        "a fallback chain cannot reach a model withheld from the use case once the primary fails",
        "gateway/src/aira_gateway/requirements.py",
        "        if model in self._released:\n            return None",
        "        if True:\n            return None",
        "gateway/tests/test_use_case_model_release.py",
    ),
    Mutation(
        "FX-G3-26",
        "the mock speaks for at most a minute however long the prompt",
        "gateway/src/aira_gateway/upstreams/mock.py",
        "        seconds = min(max(1, len(text.split())) * _SPEECH_SECONDS_PER_WORD, _SPEECH_MAX_SECONDS)",
        "        seconds = max(1, len(text.split())) * _SPEECH_SECONDS_PER_WORD",
        "gateway/tests/test_speech.py",
    ),
    Mutation(
        "FX-G3-2a",
        "a chain skips a candidate whose dialect cannot carry the request instead of aborting",
        "gateway/src/aira_gateway/pipeline/dispatch.py",
        "        except DialectUnsupported as exc:\n            unsayable.append(exc)\n            skipped.append(Skipped(model, str(exc)))\n        else:\n            return Dispatched(",
        "        except ZeroDivisionError as exc:\n            unsayable.append(exc)\n            skipped.append(Skipped(model, str(exc)))\n        else:\n            return Dispatched(",
        "gateway/tests/test_vertex.py",
    ),
    Mutation(
        "FX-G3-2b",
        "a thinking budget that does not fit the output cap is a named refusal, not a 500",
        "gateway/src/aira_gateway/upstreams/vertex/anthropic_mapping/request.py",
        '        raise DialectUnsupported(\n            f"A thinking budget of {budget} tokens',
        '        raise ValueError(\n            f"A thinking budget of {budget} tokens',
        "gateway/tests/test_vertex.py",
    ),
    Mutation(
        "FX-G3-2c",
        "a chain whose every candidate cannot carry the request answers with the dialect's refusal, not 'no model'",
        "gateway/src/aira_gateway/pipeline/dispatch.py",
        "    if unsayable and len(unsayable) == len(skipped):",
        "    if False:",
        "gateway/tests/test_vertex.py gateway/tests/test_a_dialect_that_cannot_say_it.py",
    ),
    Mutation(
        "FX-G3-3",
        "streamed Anthropic usage is a running total held whole, priced exactly as the unstreamed answer",
        "gateway/src/aira_gateway/upstreams/vertex/anthropic_mapping/response.py",
        "                self._usage[field] = value",
        "                self._usage[field] = self._usage.get(field, 0) + value",
        "gateway/tests/test_vertex.py",
    ),
    Mutation(
        "FX-G3-4",
        "the first chunk of a Vertex stream names the region that opened it",
        "gateway/src/aira_gateway/upstreams/vertex/regions.py",
        '                        chunk = chunk.model_copy(update={"served_region": region})',
        "                        chunk = chunk",
        "gateway/tests/test_region_failover.py",
    ),
    Mutation(
        "FX-G3-5",
        "a tool result without the call's id is paired with the call of the same name, on the id dialects",
        "gateway/src/aira_gateway/core/canonical.py",
        "                    answered = next((call for call in open_calls if call.name == part.name), None)",
        "                    answered = None",
        "gateway/tests/test_tool_calling.py",
    ),
    Mutation(
        "FX-G3-6a",
        "a tool schema strict mode would change is sent as declared, without strict",
        "gateway/src/aira_gateway/upstreams/vertex/anthropic_mapping/schema.py",
        "    if schema_refusal(parameters) is None and _qualified(parameters):",
        "    if True:",
        "gateway/tests/test_vertex.py",
    ),
    Mutation(
        "FX-G3-6b",
        "a strict tool schema is closed as strict mode requires",
        "gateway/src/aira_gateway/upstreams/vertex/anthropic_mapping/schema.py",
        "        return schema_for_anthropic(parameters), True",
        "        return to_json_schema(parameters), True",
        "gateway/tests/test_vertex.py",
    ),
    Mutation(
        "FX-G3-6c",
        "the OpenAI strict response schema closes every object",
        "gateway/src/aira_gateway/upstreams/openai/mapping/request.py",
        '        out["additionalProperties"] = False',
        "        pass",
        "gateway/tests/test_openai_dialect.py",
    ),
    Mutation(
        "FX-G3-6d",
        "the OpenAI strict response schema requires every property",
        "gateway/src/aira_gateway/upstreams/openai/mapping/request.py",
        '        out["required"] = list(out.get("properties") or {})',
        "        pass",
        "gateway/tests/test_openai_dialect.py",
    ),
    Mutation(
        "FX-G3-6e",
        "a platform that validates strict schemas skips a schema strict mode cannot express, by name",
        "gateway/src/aira_gateway/upstreams/openai/adapter.py",
        "        if not self._routes.validates_strict_schemas():",
        "        if True:",
        "gateway/tests/test_foundry.py",
    ),
    Mutation(
        "FX-G3-7a",
        "a thought signature goes back to Gemini on the call it was issued with",
        "gateway/src/aira_gateway/upstreams/gemini_mapping.py",
        "                call[_SIGNATURE_FIELD] = part.signature",
        "                pass",
        "gateway/tests/test_gemini_upstream_mapping.py",
    ),
    Mutation(
        "FX-G3-7b",
        "the Gemini surface returns a call's thought signature to the caller",
        "gateway/src/aira_gateway/api/gemini/mapping.py",
        "        thoughtSignature=call.signature,",
        "        thoughtSignature=None,",
        "gateway/tests/test_tool_calling.py",
    ),
    Mutation(
        "FX-G3-7c",
        "a replayed thought signature reaches the canonical call",
        "gateway/src/aira_gateway/api/gemini/mapping.py",
        "                    signature=part.thoughtSignature,",
        "                    signature=None,",
        "gateway/tests/test_tool_calling.py",
    ),
    Mutation(
        "FX-G3-7d",
        "a thought signature is not stored with the answer",
        "gateway/src/aira_gateway/api/gemini/routes.py",
        "        stored = without_signatures(payload)",
        "        stored = payload",
        "gateway/tests/test_tool_calling.py",
    ),
    Mutation(
        "FX-G3-7e",
        "thinking on Claude cannot continue a call it has no thinking block for, and says so by name",
        "gateway/src/aira_gateway/upstreams/vertex/anthropic_mapping/request.py",
        "    if _continues_a_tool_call(request):",
        "    if False:",
        "gateway/tests/test_vertex.py",
    ),
    Mutation(
        "FX-G3-8a",
        "a 200 whose body is not JSON is the upstream's failure, so the chain moves on",
        "gateway/src/aira_gateway/upstreams/exchange.py",
        '    except ValueError:\n        raise UpstreamError(f"{name} answered with a body that is not JSON.", BAD_ANSWER) from None',
        '    except ZeroDivisionError:\n        raise UpstreamError(f"{name} answered with a body that is not JSON.", BAD_ANSWER) from None',
        "gateway/tests/test_an_upstream_failure_is_never_a_silent_end.py",
    ),
    Mutation(
        "FX-G3-8b",
        "a stream line that is not JSON is the upstream's failure, so the chain moves on",
        "gateway/src/aira_gateway/upstreams/exchange.py",
        '            except ValueError:\n                raise UpstreamError(\n                    f"{name} sent a stream event that is not JSON.", BAD_ANSWER',
        '            except ZeroDivisionError:\n                raise UpstreamError(\n                    f"{name} sent a stream event that is not JSON.", BAD_ANSWER',
        "gateway/tests/test_an_upstream_failure_is_never_a_silent_end.py",
    ),
    Mutation(
        "FX-G3-8c",
        "a connection lost while a stream is read is the upstream's failure, on every transport",
        "gateway/src/aira_gateway/upstreams/exchange.py",
        "            if isinstance(parsed, dict):\n                yield parsed\n    except httpx.HTTPError as exc:",
        "            if isinstance(parsed, dict):\n                yield parsed\n    except ZeroDivisionError as exc:",
        "gateway/tests/test_an_upstream_failure_is_never_a_silent_end.py",
    ),
    Mutation(
        "FX-G3-9a",
        "a turn that ends in a function call is a complete structured answer",
        "gateway/src/aira_gateway/api/serving/answers.py",
        "    if response.finish_reason == FinishReason.TOOL_USE and response.tool_calls:",
        "    if False:",
        "gateway/tests/test_tool_calling.py gateway/tests/test_vertex.py",
    ),
    Mutation(
        "FX-G3-9b",
        "a function call leaves the Gemini surface as Google's STOP",
        "gateway/src/aira_gateway/api/gemini/mapping.py",
        '    FinishReason.TOOL_USE: "STOP",',
        '    FinishReason.TOOL_USE: "TOOL_USE",',
        "gateway/tests/test_tool_calling.py",
    ),
    Mutation(
        "FX-G3-9c",
        "an unknown finish word is OTHER, never STOP",
        "gateway/src/aira_gateway/upstreams/vertex/anthropic_mapping/response.py",
        "    return _STOP_REASONS.get(str(stop_reason), FinishReason.OTHER)",
        "    return _STOP_REASONS.get(str(stop_reason), FinishReason.STOP)",
        "gateway/tests/test_vertex.py",
    ),
    Mutation(
        "FX-G3-9d",
        "Google's policy stops are one refusal word",
        "gateway/src/aira_gateway/upstreams/gemini_mapping.py",
        '    "RECITATION": FinishReason.REFUSAL,',
        '    "RECITATION": FinishReason.STOP,',
        "gateway/tests/test_gemini_upstream_mapping.py",
    ),
    Mutation(
        "FX-G4-1",
        "two repeats are compared by the characters they match, not by their spelling",
        "libs/src/aira_common/patterns.py",
        "            return _finder(other).search(text) is not None",
        "            return False",
        "libs/tests/test_patterns.py",
    ),
    Mutation(
        "FX-G4-10",
        "the heuristic reads both ends of a long text, not the head alone",
        "gateway/src/aira_gateway/pipeline/classifiers/injection.py",
        "    return text[:half], text[-half:]",
        "    return (text[:MAX_SCANNED_CHARS],)",
        "gateway/tests/test_pipeline_classifiers.py gateway/tests/test_hardening.py",
    ),
    Mutation(
        "FX-G4-11",
        "a pattern past a bound is dropped with a line saying so",
        "gateway/src/aira_gateway/pipeline/classifiers/injection.py",
        '                _dropped(pattern, f"longer than {MAX_PATTERN_LENGTH} characters")',
        "                pass",
        "gateway/tests/test_pipeline_classifiers.py",
    ),
    Mutation(
        "FX-G4-12",
        "a boundary is never one the text already holds",
        "gateway/src/aira_gateway/pipeline/classifiers/prompts.py",
        "            if boundary.open not in text and boundary.close not in text:",
        "            if True:",
        "gateway/tests/test_the_redactor_length_guard.py",
    ),
    Mutation(
        "FX-G4-13",
        "a classifier is given the caller's text as data between markers",
        "gateway/src/aira_gateway/pipeline/classifiers/prompts.py",
        "            CanonicalMessage(role=Role.USER, text=boundary.wrap(text)),",
        "            CanonicalMessage(role=Role.USER, text=text),",
        "gateway/tests/test_pipeline_classifiers.py",
    ),
    Mutation(
        "FX-G4-14",
        "a rewrite cut off before its end is not applied",
        "gateway/src/aira_gateway/pipeline/classifiers/redaction.py",
        "        if response.finish_reason is not FinishReason.STOP:",
        "        if False:",
        "gateway/tests/test_the_redactor_length_guard.py",
    ),
    Mutation(
        "FX-G4-15",
        "a rewrite far longer than its input is not applied",
        "gateway/src/aira_gateway/pipeline/classifiers/redaction.py",
        "        if len(rewritten) > len(given) * self.MAX_GROWTH + self.GROWTH_ALLOWANCE:",
        "        if False:",
        "gateway/tests/test_the_redactor_length_guard.py",
    ),
    Mutation(
        "FX-G4-16",
        "an action the filter does not know blocks",
        "gateway/src/aira_gateway/pipeline/engine/steps.py",
        '    if config.get("action") == FilterAction.FLAG:',
        '    if config.get("action", "block") != "block":',
        "gateway/tests/test_pipeline_engine.py",
    ),
    Mutation(
        "FX-G4-17",
        "a scope the filter does not know reads every message",
        "gateway/src/aira_gateway/pipeline/engine/steps.py",
        "    if scope == ScanScope.USER:\n        return request.last_user_text()\n    return _system_and_user(request)",
        '    if scope == "system_user":\n        return _system_and_user(request)\n    return request.last_user_text()',
        "gateway/tests/test_pipeline_engine.py",
    ),
    Mutation(
        "FX-G4-18",
        "only a real false turns the built-in phrasings off",
        "gateway/src/aira_gateway/pipeline/engine/steps.py",
        '            extras, use_builtins=config.get("use_builtins", True) is not False',
        '            extras, use_builtins=bool(config.get("use_builtins", True))',
        "gateway/tests/test_pipeline_engine.py",
    ),
    Mutation(
        "FX-G4-19",
        "a heuristic verdict standing in for an llm step says so on the row",
        "gateway/src/aira_gateway/pipeline/engine/steps.py",
        '        why = f"{verdict} (by the heuristic)" if fell_back else str(verdict)',
        "        why = str(verdict)",
        "gateway/tests/test_a_step_asks_a_chain_of_models.py",
    ),
    Mutation(
        "FX-G4-2",
        "a required character both repeats can consume does not separate them",
        "libs/src/aira_common/patterns.py",
        "            if self.overlaps(opened.loop.chars, chars)",
        "            if False",
        "libs/tests/test_patterns.py",
    ),
    Mutation(
        "FX-G4-20",
        "a router with no model to ask is told apart from one whose model failed",
        "gateway/src/aira_gateway/pipeline/engine/steps.py",
        '            reason = "classifier_failed" if routed.classifier else "no_classifier"',
        '            reason = "classifier_failed"',
        "gateway/tests/test_pipeline_engine.py",
    ),
    Mutation(
        "FX-G4-21",
        "the parser reads an unknown word the way that refuses or reads more",
        "gateway/src/aira_gateway/pipeline/config.py",
        "            typed[key] = str(unknown)",
        "            pass",
        "gateway/tests/test_pipeline_bounds.py",
    ),
    Mutation(
        "FX-G4-22",
        "a category that is not an object is left out, not carried into the router",
        "gateway/src/aira_gateway/pipeline/config.py",
        "            if _is_category(category, step)",
        "            if True",
        "gateway/tests/test_pipeline_bounds.py",
    ),
    Mutation(
        "FX-G4-23",
        "a stored steps value that is not a list parses to no steps",
        "gateway/src/aira_gateway/pipeline/config.py",
        "        if not isinstance(raw_steps, list):",
        "        if False:",
        "gateway/tests/test_pipeline_bounds.py",
    ),
    Mutation(
        "FX-G4-24",
        "a malformed dry-run pipeline is a 400 naming the field",
        "gateway/src/aira_gateway/api/pipeline.py",
        "    problem = problem_in(payload.pipeline)",
        "    problem = None",
        "gateway/tests/test_pipeline_dryrun.py",
    ),
    Mutation(
        "FX-G4-25",
        "only an author of the use case may try an unsaved pipeline",
        "gateway/src/aira_gateway/api/pipeline.py",
        "    if authors:\n        return None",
        "    if True:\n        return None",
        "gateway/tests/test_pipeline_dryrun.py",
    ),
    Mutation(
        "FX-G4-26",
        "a member's trace carries no words a model wrote",
        "gateway/src/aira_gateway/api/pipeline.py",
        "                        if model_words",
        "                        if True",
        "gateway/tests/test_pipeline_dryrun.py",
    ),
    Mutation(
        "FX-G4-27",
        "a key bound to the use case is never its administrator",
        "gateway/src/aira_gateway/api/pipeline.py",
        '    if principal.method != "oidc":\n        return False',
        "    if False:\n        return False",
        "gateway/tests/test_pipeline_dryrun.py",
    ),
    Mutation(
        "FX-G4-28",
        "with enforcement off a request is still counted",
        "gateway/src/aira_gateway/budgets/service.py",
        "            if not self._enforce:\n                return Reservation(\n"
        "                    budgets=budgets, lifted=lifted, subject=subject, period_keys=periods\n"
        "                )",
        "            if not self._enforce:\n                return Reservation()",
        "gateway/tests/test_budget_service.py gateway/tests/test_installation_budget.py",
    ),
    Mutation(
        "FX-G4-29",
        "with enforcement off nobody is refused, however far over a limit",
        "gateway/src/aira_gateway/budgets/service.py",
        "        if not use_case or not self._enforce:",
        "        if not use_case:",
        "gateway/tests/test_budget_service.py",
    ),
    Mutation(
        "FX-G4-3",
        "a group is walked as the sequence it holds, so a wrapped repeat meets its neighbour",
        "libs/src/aira_common/patterns.py",
        "                run = self.walk(body, (flags | added) & ~removed, run)",
        "                run = ()",
        "libs/tests/test_patterns.py",
    ),
    Mutation(
        "FX-G4-30",
        "a counter's expiry is set when it is built and never extended by a reservation",
        "gateway/src/aira_gateway/budgets/ledger.py",
        "if redis.call('TTL', key) == -1 then\n  redis.call('EXPIRE', key, ARGV[10])\nend",
        "redis.call('EXPIRE', key, ARGV[10])",
        "gateway/tests/test_budget_reservation.py",
    ),
    Mutation(
        "FX-G4-31",
        "a correction does not extend a counter's life",
        "gateway/src/aira_gateway/budgets/ledger.py",
        "if redis.call('TTL', key) == -1 then\n  redis.call('EXPIRE', key, ARGV[4])\nend",
        "redis.call('EXPIRE', key, ARGV[4])",
        "gateway/tests/test_budget_reservation.py",
    ),
    Mutation(
        "FX-G4-32",
        "a negative limit refuses on the counter path as on the Postgres one",
        "gateway/src/aira_gateway/budgets/ledger.py",
        "    return NO_LIMIT if limit is None else max(0, limit)",
        "    return NO_LIMIT if limit is None else limit",
        "gateway/tests/test_budget_reservation.py",
    ),
    Mutation(
        "FX-G4-33",
        "a request is booked in the period it was admitted in",
        "gateway/src/aira_gateway/budgets/service.py",
        "period_key=(periods or {}).get(budget.id)",
        "period_key=None",
        "gateway/tests/test_budget_reservation.py",
    ),
    Mutation(
        "FX-G4-34",
        "a failure part-way through a reservation gives back what it took",
        "gateway/src/aira_gateway/budgets/service.py",
        "                    if not partial.resolved:\n                        await self._release_quietly(partial)",
        "                    if False:\n                        await self._release_quietly(partial)",
        "gateway/tests/test_budget_reservation.py",
    ),
    Mutation(
        "FX-G4-35",
        "a budget of an unknown scope is reported without figures, not a 500",
        "gateway/src/aira_gateway/budgets/service.py",
        "                if scope is None:",
        "                if budget.scope == EACH_MEMBER and not subject:",
        "gateway/tests/test_budget_service.py",
    ),
    Mutation(
        "FX-G4-36",
        "asking a budget about a caller it does not bind is an explicit error",
        "gateway/src/aira_gateway/budgets/keys.py",
        "    if scope is None:\n        raise Unbound(",
        "    if False:\n        raise Unbound(",
        "gateway/tests/test_budget_service.py",
    ),
    Mutation(
        "FX-G4-37",
        "a refusal waits for the slowest refusing bucket",
        "gateway/src/aira_gateway/ratelimit/buckets.py",
        "                wait = max(\n                    wait,",
        "                wait = wait or max(\n                    wait,",
        "gateway/tests/test_ratelimit.py",
    ),
    Mutation(
        "FX-G4-38",
        "a batch that can never fit is a 400 without Retry-After",
        "gateway/src/aira_gateway/ratelimit/errors.py",
        '        super().__init__(400, message, "FAILED_PRECONDITION")',
        '        super().__init__(429, message, "RESOURCE_EXHAUSTED", {"Retry-After": "1"})',
        "gateway/tests/test_ratelimit.py gateway/tests/test_ratelimit_routes.py",
    ),
    Mutation(
        "FX-G4-39",
        "a per-process bucket that is full again is forgotten",
        "gateway/src/aira_gateway/ratelimit/buckets.py",
        "            for key in [key for key, (_, _, full_at) in self._state.items() if full_at <= now]:",
        "            for key in []:",
        "gateway/tests/test_ratelimit.py",
    ),
    Mutation(
        "FX-G4-4",
        "a repeat inside a lookahead meets the repeats open before it",
        "libs/src/aira_common/patterns.py",
        "                self.walk(av[1], flags, run)",
        "                self.walk(av[1], flags, ())",
        "libs/tests/test_patterns.py",
    ),
    Mutation(
        "FX-G4-40",
        "the per-process buckets stay under their ceiling",
        "gateway/src/aira_gateway/ratelimit/buckets.py",
        "        while len(self._state) > MAX_TRACKED_BUCKETS:",
        "        while False:",
        "gateway/tests/test_ratelimit.py",
    ),
    Mutation(
        "FX-G4-41",
        "a batch is billed per model that answered",
        "gateway/src/aira_gateway/pipeline/engine/texts.py",
        "        by_model.setdefault(call.model, []).append(call)",
        "        by_model.setdefault(calls[0].model, []).append(call)",
        "gateway/tests/test_a_redactor_reaches_an_embedding.py",
    ),
    Mutation(
        "FX-G4-42",
        "a batch's row keeps the cache and reasoning figures",
        "gateway/src/aira_gateway/pipeline/engine/texts.py",
        "        cached_input_tokens=sum(call.usage.cached_input_tokens for call in calls),",
        "        cached_input_tokens=0,",
        "gateway/tests/test_a_redactor_reaches_an_embedding.py",
    ),
    Mutation(
        "FX-G4-5",
        "an optional step is a choice of two, and a row of them is refused",
        "libs/src/aira_common/patterns.py",
        "            self._meet(run, choice)",
        "            pass",
        "libs/tests/test_patterns.py",
    ),
    Mutation(
        "FX-G4-6",
        "bounded repeats beside an unbounded one have a bound of their own",
        "libs/src/aira_common/patterns.py",
        "        if ways >= (MAX_AMBIGUITY_BESIDE_A_LOOP if beside_a_loop else MAX_AMBIGUITY):",
        "        if ways >= MAX_AMBIGUITY:",
        "libs/tests/test_patterns.py",
    ),
    Mutation(
        "FX-G4-7",
        "each open loop is counted once in the product, however many paths left it open",
        "libs/src/aira_common/patterns.py",
        "        for earlier in {id(opened.loop): opened.loop for opened in trading}.values():",
        "        for earlier in [opened.loop for opened in trading]:",
        "libs/tests/test_patterns.py",
    ),
    Mutation(
        "FX-G4-8",
        "two loops spelled alike are still two loops",
        "libs/src/aira_common/patterns.py",
        "@dataclass(frozen=True, slots=True, eq=False)\nclass _Loop:",
        "@dataclass(frozen=True, slots=True)\nclass _Loop:",
        "libs/tests/test_patterns.py",
    ),
    Mutation(
        "FX-G4-9",
        "the check bounds its own work",
        "libs/src/aira_common/patterns.py",
        "        self._budget -= characters",
        "        self._budget -= 0",
        "libs/tests/test_patterns.py",
    ),
    Mutation(
        "FX-G5-26",
        "a 413 row records the forwarded client, not the proxy",
        "gateway/src/aira_gateway/middleware.py",
        "                source_ip=client_ip(Request(scope)),",
        '                source_ip=(scope.get("client") or [None])[0],',
        "gateway/tests/test_hardening.py",
    ),
    Mutation(
        "FX-L-1",
        "the settings source matches Vault keys through the one shared rule, not its own",
        "libs/src/aira_common/config.py",
        "            self._named = by_setting(self._secrets(), self._prefix)",
        "            self._named = dict(self._secrets())",
        "libs/tests/test_secrets.py",
    ),
    Mutation(
        "FX-L-10",
        "a percent-encoded credential parameter name is redacted like a plain one",
        "libs/src/aira_common/observability/redaction.py",
        "        if separator and unquote_plus(name).lower() in SENSITIVE_QUERY_PARAMS:",
        "        if separator and name.lower() in SENSITIVE_QUERY_PARAMS:",
        "libs/tests/test_access_log_redaction.py",
    ),
    Mutation(
        "FX-L-11",
        "a string longer than the scan window is still one string",
        "libs/src/aira_common/nesting.py",
        "        in_string ^= len(segments) % 2 == 0",
        "        in_string = False",
        "libs/tests/test_nesting.py",
    ),
    Mutation(
        "FX-L-12",
        "nesting that crosses a scan window is measured whole",
        "libs/src/aira_common/nesting.py",
        "            depth += 2 * steps.count(1) - len(steps)",
        "            depth = 0",
        "libs/tests/test_nesting.py",
    ),
    Mutation(
        "FX-L-13",
        "the nesting scan holds memory in proportion to the body, not to its quotes",
        "libs/src/aira_common/nesting.py",
        "SCAN_WINDOW = 1 << 16",
        "SCAN_WINDOW = 1 << 40",
        "libs/tests/test_nesting.py",
    ),
    Mutation(
        "FX-L-14",
        "a group path with a dot segment is answered without a request to Keycloak",
        "libs/src/aira_common/directory.py",
        '        if not DOT_SEGMENTS.isdisjoint(wanted.split("/")):',
        "        if False:",
        "libs/tests/test_directory.py",
    ),
    Mutation(
        "FX-L-15",
        "a directory client opened for one lookup is closed when it is done",
        "libs/src/aira_common/directory.py",
        "        with httpx.Client(timeout=TIMEOUT_SECONDS) as client:\n            yield client",
        "        yield httpx.Client(timeout=TIMEOUT_SECONDS)",
        "libs/tests/test_directory.py",
    ),
    Mutation(
        "FX-L-16",
        "a typed username is looked up with exact=true",
        "libs/src/aira_common/directory.py",
        '                http, self._token(http), "/users", {"username": wanted, "exact": "true", "max": 2}',
        '                http, self._token(http), "/users", {"username": wanted, "max": 2}',
        "libs/tests/test_directory.py",
    ),
    Mutation(
        "FX-L-17",
        "only the row with exactly the typed username is bound",
        "libs/src/aira_common/directory.py",
        "            if isinstance(found, str) and found == wanted:",
        "            if isinstance(found, str) and wanted in found:",
        "libs/tests/test_directory.py",
    ),
    Mutation(
        "FX-L-18",
        "a declared capability in a shape this version cannot read is dropped, not a failed event",
        "libs/src/aira_common/models.py",
        "        Capability(value) for value in values if isinstance(value, str) and value in known",
        "        Capability(value) for value in values if value in known",
        "libs/tests/test_capability_vocabulary.py",
    ),
    Mutation(
        "FX-L-19",
        "a stored permission that is not a string grants nothing rather than raising",
        "libs/src/aira_common/permissions.py",
        "        Permission(value) for value in values if isinstance(value, str) and value in known",
        "        Permission(value) for value in values if value in known",
        "libs/tests/test_permissions.py",
    ),
    Mutation(
        "FX-L-2",
        "a Vault key written without the AIRA_ prefix still names its setting",
        "libs/src/aira_common/secrets.py",
        '    return name if name.startswith(prefix) else f"{prefix}{name}"',
        "    return name",
        "libs/tests/test_secrets.py",
    ),
    Mutation(
        "FX-L-20",
        "a group that is not a string matches no role definition rather than raising",
        "libs/src/aira_common/permissions.py",
        "    held = {group for group in groups if isinstance(group, str)}",
        "    held = set(groups)",
        "libs/tests/test_permissions.py",
    ),
    Mutation(
        "FX-L-21",
        "a group path that is not a string matches no grant rather than raising",
        "libs/src/aira_common/access.py",
        "    held = {path for path in group_paths if isinstance(path, str)}",
        "    held = set(group_paths)",
        "libs/tests/test_access.py",
    ),
    Mutation(
        "FX-L-22",
        "a group that is not a string confers no role rather than raising",
        "libs/src/aira_common/roles.py",
        "    held = {group for group in groups if isinstance(group, str)}",
        "    held = set(groups)",
        "libs/tests/test_roles.py",
    ),
    Mutation(
        "FX-L-23",
        "no role set or role-name predicate stands in for a permission check",
        "libs/src/aira_common/roles.py",
        "ALL_ROLES: tuple[Role, ...] = tuple(Role)",
        "ALL_ROLES: tuple[Role, ...] = tuple(Role)\nOVERSIGHT_ROLES = frozenset(ALL_ROLES)",
        "libs/tests/test_roles.py",
    ),
    Mutation(
        "FX-L-24",
        "a module aira_common imports by name is a declared dependency too",
        "libs/pyproject.toml",
        '    "opentelemetry-exporter-otlp-proto-common>=1.29",\n',
        "",
        "libs/tests/test_declared_dependencies.py",
    ),
    Mutation(
        "FX-L-25",
        "a failure inside the debug channel costs that line, never the call",
        "libs/src/aira_common/integration_debug.py",
        "    except Exception:  # noqa: BLE001 — never the reason a watched call fails\n        return",
        "    except ZeroDivisionError:\n        return",
        "libs/tests/test_integration_debug.py",
    ),
    Mutation(
        "FX-L-26",
        "an open counter breaker answers without another connection attempt",
        "libs/src/aira_common/counters.py",
        "        if now < self._unavailable_until:",
        "        if False:",
        "libs/tests/test_counters.py",
    ),
    Mutation(
        "FX-L-3",
        "two spellings of one setting with different values refuse to start",
        "libs/src/aira_common/secrets.py",
        "        if name in named and named[name] != value:",
        "        if False:",
        "libs/tests/test_secrets.py",
    ),
    Mutation(
        "FX-L-4",
        "a VAULT_TIMEOUT that is not a positive number of seconds is refused by name",
        "libs/src/aira_common/secrets.py",
        "    if not (math.isfinite(seconds) and seconds > 0):",
        "    if False:",
        "libs/tests/test_secrets.py",
    ),
    Mutation(
        "FX-L-5",
        "/readyz reports a development token only when it is what authenticated",
        "libs/src/aira_common/secrets.py",
        '    return "" if config.role_id else _dev_token()',
        "    return _dev_token()",
        "libs/tests/test_secrets.py",
    ),
    Mutation(
        "FX-L-6",
        "a caller cannot take a request out of the traces by a Host or slug containing a probe's name",
        "libs/src/aira_common/observability/providers.py",
        'HEALTH_PATHS = r"^[a-z]+://[^/]+/(healthz|readyz)\\??$"',
        'HEALTH_PATHS = r"(healthz|readyz)"',
        "libs/tests/test_observability.py",
    ),
    Mutation(
        "FX-L-8",
        "callers queued behind a failed token fetch share its failure instead of fetching in turn",
        "libs/src/aira_common/tokens.py",
        "            if self._fetches != waited_from and self._failure is not None:",
        "            if False:",
        "libs/tests/test_tokens.py",
    ),
    Mutation(
        "FX-L-9",
        "a successful fetch clears the failure, so no caller inherits an older one",
        "libs/src/aira_common/tokens.py",
        "            self._fetches += 1\n            self._failure = None",
        "            self._fetches += 1",
        "libs/tests/test_tokens.py",
    ),
    Mutation(
        "FX-L1-10",
        "a clock tolerance must be a finite number",
        "libs/src/aira_common/oidc.py",
        "    if not math.isfinite(value) or value < 0 or value > MAX_TOLERANCE_SECONDS:",
        "    if value < 0 or value > MAX_TOLERANCE_SECONDS:",
        "libs/tests/test_oidc_clock_tolerance.py",
    ),
    Mutation(
        "FX-L1-5",
        "an unknown kid refetches the key set at most once per interval",
        "libs/src/aira_common/oidc.py",
        "                    refresh = False",
        "                    pass",
        "libs/tests/test_when_the_identity_provider_is_gone.py",
    ),
    Mutation(
        "FX-T2-13",
        "a 413 row records the size the request declared",
        "gateway/src/aira_gateway/middleware.py",
        '            scope.setdefault("state", {})["request_bytes"] = declared',
        "            pass",
        "gateway/tests/test_hardening.py",
    ),
    Mutation(
        "FX-T2-13b",
        "a declared length is recorded at most at what the column holds",
        "gateway/src/aira_gateway/middleware.py",
        "                    min(measured, _MAX_RECORDED_BYTES) if isinstance(measured, int) else None",
        "                    measured if isinstance(measured, int) else None",
        "gateway/tests/test_hardening.py",
    ),
    Mutation(
        "FXG2-1",
        "a stream the caller leaves is settled once, however the server cancels it",
        "gateway/src/aira_gateway/api/serving/accounting.py",
        "            await uninterrupted(\n                _settle_and_record(",
        "            await asyncio.shield(\n                _settle_and_record(",
        "gateway/tests/test_cancelled_requests.py",
    ),
    Mutation(
        "FXG2-10",
        "both surfaces refuse more attachments than a request may carry",
        "gateway/src/aira_gateway/api/serving/prepare.py",
        "        check_attachment_bounds(canonical)",
        "        pass",
        "gateway/tests/test_attachments.py",
    ),
    Mutation(
        "FXG2-11",
        "a refused document books what the model spent",
        "gateway/src/aira_gateway/api/gemini/routes.py",
        "        acct.spent(answer.model, answer.usage)",
        "        pass",
        "gateway/tests/test_serving_options.py",
    ),
    Mutation(
        "FXG2-12",
        "an unservable embedding spends no rate-limit allowance",
        "gateway/src/aira_gateway/api/serving/prepare.py",
        "        check_embedding_shape(embed, embedding_bounds(request))",
        "        pass",
        "gateway/tests/test_serving_options.py",
    ),
    Mutation(
        "FXG2-13",
        "a reservation prices its input at the input rate, so an embedding reserves a cost",
        "gateway/src/aira_gateway/api/serving/controls.py",
        "        tokens - output, price.input_per_million_nanos",
        "        tokens - output, price.output_per_million_nanos",
        "gateway/tests/test_cost_budgets.py",
    ),
    Mutation(
        "FXG2-14",
        "a non-string attachment field on KIRA is refused, never converted",
        "gateway/src/aira_gateway/api/kira/schemas.py",
        "                if has_data and field in part and not isinstance(part[field], str):",
        "                if False:",
        "gateway/tests/test_kira_wire_contract.py",
    ),
    Mutation(
        "FXG2-15",
        "the model's replayed reasoning is not forwarded as something it said",
        "gateway/src/aira_gateway/api/gemini/mapping.py",
        "        if part.text is not None and part.thought:",
        "        if False:",
        "gateway/tests/test_gemini_mapping.py",
    ),
    Mutation(
        "FXG2-16",
        "a KIRA request a model cannot serve is a validation error, not a missing model",
        "gateway/src/aira_gateway/api/kira/refusals.py",
        "        return errors.kira_error_response(400, errors.VALIDATION_ERROR, str(exc))\n    if isinstance(exc, AmbiguousModel | RegionNotAllowed):",
        "        return errors.kira_error_response(400, errors.MODEL_NOT_FOUND, str(exc))\n    if isinstance(exc, AmbiguousModel | RegionNotAllowed):",
        "gateway/tests/test_kira_surface.py gateway/tests/test_a_dialect_that_cannot_say_it.py",
    ),
    Mutation(
        "FXG2-17",
        "a KIRA rate-limit refusal says when to come back",
        "gateway/src/aira_gateway/api/kira/refusals.py",
        '            headers={"Retry-After": exc.retry_after},',
        "            headers=None,",
        "gateway/tests/test_ratelimit_routes.py",
    ),
    Mutation(
        "FXG2-18a",
        "a retired or unknown use case is not recorded as a pipeline block",
        "gateway/src/aira_gateway/api/serving/refusals.py",
        "        # request names a use case that does not serve here — the caller's to correct.\n        return Outcome.INVALID_REQUEST",
        "        # request names a use case that does not serve here — the caller's to correct.\n        return Outcome.BLOCKED_BY_PIPELINE",
        "gateway/tests/test_a_retired_use_case_serves_nothing.py "
        "gateway/tests/test_an_unknown_use_case_serves_nothing.py",
    ),
    Mutation(
        "FXG2-18b",
        "an incomplete document is recorded as the model stopping, not the caller's mistake",
        "gateway/src/aira_gateway/api/serving/refusals.py",
        "    502: Outcome.UPSTREAM_ERROR,",
        "    502: Outcome.INVALID_REQUEST,",
        "gateway/tests/test_serving_options.py",
    ),
    Mutation(
        "FXG2-1b",
        "a reservation is given back even when the block holding it is cancelled",
        "gateway/src/aira_gateway/budgets/service.py",
        "            await uninterrupted(self.release(reservation))",
        "            await self.release(reservation)",
        "gateway/tests/test_budget_reservation.py",
    ),
    Mutation(
        "FXG2-1c",
        "a stream cancelled before its body began is still recorded and released",
        "gateway/src/aira_gateway/api/serving/streaming.py",
        "        if never_started:",
        "        if False:",
        "gateway/tests/test_cancelled_requests.py",
    ),
    Mutation(
        "FXG2-1d",
        "a stream cancelled between two frames is accounted for when its response ends",
        "gateway/src/aira_gateway/api/serving/streaming.py",
        "        await self._body.aclose()",
        "        pass",
        "gateway/tests/test_cancelled_requests.py",
    ),
    Mutation(
        "FXG2-1e",
        "the exit's shielded work finishes before a cancellation is let through",
        "gateway/src/aira_gateway/shielding.py",
        "                cancelled = exc",
        "                raise exc",
        "gateway/tests/test_cancelled_requests.py",
    ),
    Mutation(
        "FXG2-2",
        "a refused KIRA body is logged by its shape, never its content",
        "gateway/src/aira_gateway/api/kira/routes.py",
        '            return f"str({len(value)})"',
        "            return value",
        "gateway/tests/test_kira_surface.py",
    ),
    Mutation(
        "FXG2-20",
        "the span names a schema by the schema's own fingerprint",
        "gateway/src/aira_gateway/api/serving/prepare.py",
        '            attributes["aira.response_schema.digest"] = canonical.response_schema.digest()',
        '            attributes["aira.response_schema.digest"] = canonical.response_schema.digest()[:16]',
        "gateway/tests/test_the_span_says_what_the_request_was.py",
    ),
    Mutation(
        "FXG2-21",
        "a streamed tool call records why the notice was withheld in the buffered answer's words",
        "gateway/src/aira_gateway/api/serving/streaming.py",
        "            self._notice.saw_tool_call()",
        "            pass",
        "gateway/tests/test_notice_reaches_every_exit.py",
    ),
    Mutation(
        "FXG2-22",
        "a WAV labelled as WebP is caught by its signature",
        "gateway/src/aira_gateway/attachments.py",
        '    "image/webp": ((0, b"RIFF"), (8, b"WEBP")),',
        '    "image/webp": ((0, b"RIFF"),),',
        "gateway/tests/test_attachments.py",
    ),
    Mutation(
        "FXG2-3",
        "ki-usage reports its per-user rows rather than failing on model id 0",
        "gateway/src/aira_gateway/api/kira/schemas.py",
        "    model_id: int",
        "    model_id: ModelId",
        "gateway/tests/test_oversight_reads_every_figure.py",
    ),
    Mutation(
        "FXG2-4",
        "an attachment in the URL-safe alphabet, as the google-genai SDK sends it, is read",
        "gateway/src/aira_gateway/attachments.py",
        "    text = raw.translate(_URL_SAFE)",
        "    text = raw",
        "gateway/tests/test_attachments.py gateway/tests/test_google_sdk_speaks_to_us.py",
    ),
    Mutation(
        "FXG2-5",
        "a body in UTF-16 cannot pass the depth bound and take the decoder down",
        "gateway/src/aira_gateway/api/serving/body.py",
        "        return json.loads(text)\n    except RecursionError:",
        "        return json.loads(raw)\n    except ZeroDivisionError:",
        "gateway/tests/test_a_body_cannot_be_nested_out_of_the_audit_trail.py",
    ),
    Mutation(
        "FXG2-6",
        "a thinking budget below one token is refused, whatever the model declares",
        "gateway/src/aira_gateway/thinking.py",
        "    if tokens < 1:",
        "    if False:",
        "gateway/tests/test_thinking.py gateway/tests/test_a_number_cannot_switch_the_budget_off.py",
    ),
    Mutation(
        "FXG2-6b",
        "a reservation is never negative",
        "gateway/src/aira_gateway/api/serving/controls.py",
        "    output = tokens = max(0, tokens)",
        "    output = tokens",
        "gateway/tests/test_cost_budgets.py",
    ),
    Mutation(
        "FXG2-7",
        "an embedding's audit row describes its vectors and keeps none",
        "gateway/src/aira_gateway/api/serving/accounting.py",
        '        self.served(model, usage, {"vectors": describe_vectors(vectors)})',
        '        self.served(model, usage, {"vectors": list(vectors)})',
        "gateway/tests/test_serving_options.py",
    ),
    Mutation(
        "FXG2-8a",
        "a delivered stream is served on both surfaces even without usage",
        "gateway/src/aira_gateway/api/serving/streaming.py",
        "        elif self._delivered:",
        "        elif False:",
        "gateway/tests/test_every_surface_records_alike.py",
    ),
    Mutation(
        "FXG2-8b",
        "a stream the upstream broke off after usage is settled on both surfaces",
        "gateway/src/aira_gateway/api/serving/streaming.py",
        "                acct.served(self.model, self.usage, stored, self._tool_calls)\n            acct.failed(",
        "                pass\n            acct.failed(",
        "gateway/tests/test_every_surface_records_alike.py",
    ),
    Mutation(
        "FXG2-9",
        "a Gemini stream that broke off ends in Google's error element",
        "gateway/src/aira_gateway/api/gemini/routes.py",
        '                yield f"data: {element}\\n\\n" if sse else f"{separator}{element}"',
        "                pass",
        "gateway/tests/test_google_sdk_speaks_to_us.py",
    ),
    Mutation(
        "FXG3-2",
        "a thinking budget that fills the output allowance is refused before dispatch",
        "gateway/src/aira_gateway/api/serving/prepare.py",
        "        check_budget_fits(canonical.thinking, canonical.max_output_tokens)",
        "        pass",
        "gateway/tests/test_a_number_cannot_switch_the_budget_off.py",
    ),
    Mutation(
        "FXG3-4",
        "a streamed row names the region that opened the stream",
        "gateway/src/aira_gateway/api/serving/streaming.py",
        "            self._trail.served_region = chunk.served_region",
        "            pass",
        "gateway/tests/test_the_audit_row_says_where_and_what_it_cost.py",
    ),
    Mutation(
        "FXG4-9",
        "a generation's reservation counts the prompt it will be billed for",
        "gateway/src/aira_gateway/api/serving/controls.py",
        "    tokens += text_tokens(input_texts)",
        "    tokens += 0",
        "gateway/tests/test_budget_routes.py",
    ),
    Mutation(
        "FXM-F1-8",
        "A model whose name contains a slash can be read, changed and removed through the console's URL.",
        "management/backend/src/aira_management/apps/catalog/views.py",
        '    lookup_value_regex = ".+"',
        '    lookup_value_regex = "[^/]+"',
        "management/backend/tests/test_catalog.py",
    ),
    Mutation(
        "FXM-F3-3",
        "A declared level word may be a model's thinking default.",
        "management/backend/src/aira_management/apps/catalog/validation.py",
        "    offered = [mode for mode in modes if isinstance(mode, str)] + levels",
        "    offered = [mode for mode in modes if isinstance(mode, str)]",
        "management/backend/tests/test_catalog_declaration.py",
    ),
    Mutation(
        "FXM-F4-3a",
        "a question created without a position is appended, never stacked at position 0",
        "management/backend/src/aira_management/apps/smoketests/views/catalogue.py",
        "                        serializer.save(position=_next_position())",
        "                        serializer.save()",
        "management/backend/tests/test_smoketests.py",
    ),
    Mutation(
        "FXM-F4-3b",
        "an appended question goes after retired positions too",
        "management/backend/src/aira_management/apps/smoketests/views/catalogue.py",
        '    highest = TestCase.objects.aggregate(highest=Max("position"))["highest"]',
        '    highest = TestCase.objects.filter(retired=False).aggregate(highest=Max("position"))["highest"]',
        "management/backend/tests/test_smoketests.py",
    ),
    Mutation(
        "FXM-F4-4",
        "a run still going or stopped halfway is never a use case's standing",
        "management/backend/src/aira_management/apps/smoketests/views/stats.py",
        "        finished = TestRun.objects.filter(finished_at__isnull=False)",
        "        finished = TestRun.objects.all()",
        "management/backend/tests/test_smoketests.py",
    ),
    Mutation(
        "FXM-G1-11a",
        "Management refuses at startup a Kafka broker nothing can dial.",
        "management/backend/src/aira_management/config/app_settings.py",
        "            parse_bootstrap_servers(value)",
        "            pass",
        "management/backend/tests/test_settings.py",
    ),
    Mutation(
        "FXM-G1-11b",
        "Management's readiness asks no broker when none is configured.",
        "management/backend/src/aira_management/apps/health/views.py",
        "        if not brokers:\n            return database, None",
        "        if False:\n            return database, None",
        "management/backend/tests/test_health.py",
    ),
    Mutation(
        "FXM-G1-11c",
        "An IPv6 broker is dialled without its brackets.",
        "libs/src/aira_common/kafka.py",
        '        host, closed, rest = entry[1:].partition("]")',
        '        host, closed, rest = entry.partition("]")',
        "management/backend/tests/test_settings.py management/backend/tests/test_health.py tools/tests/test_both_planes_read_one_broker_list.py",
    ),
    Mutation(
        "FXM-M1-10",
        "A grantless Global Administrator's own key rests on a recorded membership.",
        "management/backend/src/aira_management/apps/usecases/views/api_keys.py",
        "                    if not holds_a_grant(owner, usecase):",
        "                    if False:",
        "management/backend/tests/test_access_ends_completely.py",
    ),
    Mutation(
        "FXM-M1-11",
        "A detail given as a list reads as its sentence, not its repr.",
        "management/backend/src/aira_management/apps/api/exceptions.py",
        '        return " ".join(str(item) for item in detail)',
        "        return str(detail)",
        "management/backend/tests/test_api.py",
    ),
    Mutation(
        "FXM-M1-12",
        "/me reads the groups claim as authentication does; a malformed claim confers nothing.",
        "management/backend/src/aira_management/apps/api/views.py",
        '                "use_cases": list(usecases_from_group_paths(token_groups(claims))),',
        '                "use_cases": list(usecases_from_group_paths(claims.get("groups") or [])),',
        "management/backend/tests/test_api.py",
    ),
    Mutation(
        "FXM-M1-13",
        "IT Steuerung's first stored row starts from its default permissions.",
        "management/backend/src/aira_management/apps/roles/views.py",
        "                permissions=_ordered(role.permissions),",
        "                permissions=[],",
        "management/backend/tests/test_roles_api.py",
    ),
    Mutation(
        "FXM-M1-14",
        "A role-name clash at save names the label, not the group.",
        "management/backend/src/aira_management/apps/roles/views.py",
        "            if StoredRole.objects.filter(slug=slug).exists():",
        "            if False:",
        "management/backend/tests/test_roles_api.py",
    ),
    Mutation(
        "FXM-M1-15",
        "A use-case reader is refused for who they are before their body is validated.",
        "management/backend/src/aira_management/apps/usecases/views/viewset.py",
        "        if not self._may_admin(self.get_object()):",
        "        if False:",
        "management/backend/tests/test_usecases.py",
    ),
    Mutation(
        "FXM-M1-16",
        "A taken key prefix is drawn again instead of answering 500.",
        "management/backend/src/aira_management/apps/usecases/views/api_keys.py",
        "        for _attempt in range(PREFIX_ATTEMPTS):",
        "        for _attempt in range(1):",
        "management/backend/tests/test_apikeys.py",
    ),
    Mutation(
        "FXM-M1-17",
        "Purging a use case removes the object-permission rows that named it.",
        "management/backend/src/aira_management/apps/usecases/views/lifecycle.py",
        "            model.objects.filter(content_type=content_type, object_pk=str(usecase.pk)).delete()",
        "            model.objects.none().delete()",
        "management/backend/tests/test_soft_delete.py",
    ),
    Mutation(
        "FXM-M1-18",
        "Two concurrent retirements keep the first author and announce once.",
        "management/backend/src/aira_management/apps/usecases/views/lifecycle.py",
        "            UseCase.objects.live()",
        "            UseCase.objects.all()",
        "management/backend/tests/test_soft_delete.py",
    ),
    Mutation(
        "FXM-M1-19a",
        "A throttle rate DRF cannot read refuses the process, local included.",
        "management/backend/src/aira_management/config/security.py",
        "    user_rate = drf_rate_problem(settings.throttle_user)",
        "    user_rate = None",
        "management/backend/tests/test_hardening.py",
    ),
    Mutation(
        "FXM-M1-19b",
        "A default key lifetime outside 1..max refuses the process.",
        "management/backend/src/aira_management/config/security.py",
        "    elif not 1 <= settings.api_key_default_days <= settings.api_key_max_days:",
        "    elif False:",
        "management/backend/tests/test_hardening.py",
    ),
    Mutation(
        "FXM-M1-19c",
        "A deployment without an issuer refuses to start outside local.",
        "management/backend/src/aira_management/config/security.py",
        "    if not settings.oidc_issuer.strip():",
        "    if False:",
        "management/backend/tests/test_hardening.py",
    ),
    Mutation(
        "FXM-M1-1a",
        "A JSON body is decoded only as UTF-8/16/32; a byte transform named as its charset is a 415.",
        "management/backend/src/aira_management/apps/api/parsers.py",
        '        encoding = json_encoding(context.get("encoding", settings.DEFAULT_CHARSET))',
        '        encoding = context.get("encoding", settings.DEFAULT_CHARSET)',
        "management/backend/tests/test_a_callers_value_is_never_a_server_error.py",
    ),
    Mutation(
        "FXM-M1-20",
        "An unexpected exception answers 500 in the JSON envelope, never Django's HTML page.",
        "management/backend/src/aira_management/apps/api/exceptions.py",
        "        return _unexpected(exc, context)",
        "        raise exc",
        "management/backend/tests/test_api.py",
    ),
    Mutation(
        "FXM-M1-21",
        "A working credential never counts against the failed-authentication bound.",
        "management/backend/src/aira_management/apps/api/authentication.py",
        "        user = self._provision_user(subject, claims)",
        "        user = self._provision_user(subject, claims); bound.record_failure(request)",
        "management/backend/tests/test_the_api_is_rate_limited.py",
    ),
    Mutation(
        "FXM-M1-22",
        "A ?q= search never re-admits a retired use case.",
        "management/backend/src/aira_management/pagination.py",
        "    return queryset.filter(condition)",
        "    return queryset.model.objects.filter(condition)",
        "management/backend/tests/test_soft_delete.py",
    ),
    Mutation(
        "FXM-M1-28a",
        "A write to access within a request is seen by the request's next question.",
        "management/backend/src/aira_management/memo.py",
        "        memo.clear()",
        "        pass",
        "management/backend/tests/test_a_request_remembers_its_callers_access.py",
    ),
    Mutation(
        "FXM-M1-28b",
        "A page of use cases costs the same number of queries however many rows it has.",
        "management/backend/src/aira_management/apps/usecases/views/viewset.py",
        "            prefetch_object_permissions(self.request.user, page)",
        "            pass",
        "management/backend/tests/test_usecases.py",
    ),
    Mutation(
        "FXM-M1-2a",
        "A use case's description is bounded where it is written.",
        "management/backend/src/aira_management/apps/usecases/models.py",
        "    description = models.TextField(blank=True, max_length=MAX_PROSE_LENGTH)",
        "    description = models.TextField(blank=True)",
        "management/backend/tests/test_usecases.py",
    ),
    Mutation(
        "FXM-M1-2b",
        "An event larger than the channel carries is refused at write time, inside the change's transaction.",
        "management/backend/src/aira_management/apps/outbox/subscriber.py",
        "    if size > MAX_EVENT_BYTES:",
        "    if False:",
        "management/backend/tests/test_outbox.py",
    ),
    Mutation(
        "FXM-M1-2c",
        "Events sent before a failing send are marked published and never sent again.",
        "management/backend/src/aira_management/apps/outbox/management/commands/relay.py",
        "                run.stopped_at, run.failure = event, exc",
        "                raise",
        "management/backend/tests/test_outbox.py",
    ),
    Mutation(
        "FXM-M1-2d",
        "An event the broker refuses for good is parked and the queue moves on.",
        "management/backend/src/aira_management/apps/outbox/management/commands/relay.py",
        '                run.parked[event.pk] = f"The broker refused it for good: {exc}"',
        "                raise",
        "management/backend/tests/test_outbox.py",
    ),
    Mutation(
        "FXM-M1-2e",
        "An event already larger than the channel carries is parked without being sent.",
        "management/backend/src/aira_management/apps/outbox/management/commands/relay.py",
        "            if size > MAX_EVENT_BYTES:",
        "            if False:",
        "management/backend/tests/test_outbox.py",
    ),
    Mutation(
        "FXM-M1-3a",
        "A NUL in the request path is refused with 400 before any view reads it.",
        "management/backend/src/aira_management/apps/api/middleware.py",
        "    if NUL in request.path_info:",
        "    if False:",
        "management/backend/tests/test_a_callers_value_is_never_a_server_error.py",
    ),
    Mutation(
        "FXM-M1-3b",
        "A NUL anywhere in a JSON body is refused with 400 where the body is parsed.",
        "management/backend/src/aira_management/apps/api/parsers.py",
        "        if holds_nul(document):",
        "        if False:",
        "management/backend/tests/test_a_callers_value_is_never_a_server_error.py",
    ),
    Mutation(
        "FXM-M1-3c",
        "An API key label longer than its column is refused by name.",
        "management/backend/src/aira_management/apps/apikeys/serializers.py",
        '        max_length=ApiKey._meta.get_field("label").max_length,',
        "        max_length=None,",
        "management/backend/tests/test_apikeys.py",
    ),
    Mutation(
        "FXM-M1-3d",
        "A member name longer than the username column is refused by name.",
        "management/backend/src/aira_management/apps/usecases/serializers.py",
        "    username = serializers.CharField(max_length=USERNAME_MAX_LENGTH)",
        "    username = serializers.CharField()",
        "management/backend/tests/test_group_grants.py",
    ),
    Mutation(
        "FXM-M1-3e",
        "A role's group path longer than its column is refused before the directory is asked.",
        "management/backend/src/aira_management/apps/roles/views.py",
        "    if len(path) > GROUP_PATH_MAX:",
        "    if False:",
        "management/backend/tests/test_roles_api.py",
    ),
    Mutation(
        "FXM-M1-4a",
        "A Keycloak group path longer than auth_group.name is mirrored under its digest.",
        "management/backend/src/aira_management/rbac.py",
        "        len(path) <= VERBATIM_PATH_MAX_LENGTH",
        "        True",
        "management/backend/tests/test_group_grants.py management/backend/tests/test_api.py",
    ),
    Mutation(
        "FXM-M1-4b",
        "A digest-named mirror is resolved back to the path a grant names.",
        "management/backend/src/aira_management/rbac.py",
        "        paths.extend(path for path in _digested_paths() if django_group_name(path) in digests)",
        "        pass",
        "management/backend/tests/test_group_grants.py",
    ),
    Mutation(
        "FXM-M1-4c",
        "A stored role is matched through its group's mirror name, digest included.",
        "management/backend/src/aira_management/rbac.py",
        "            else any(django_group_name(path) in mirrors for path in role.group_paths)",
        '            else any(f"kc:{path}" in mirrors for path in role.group_paths)',
        "management/backend/tests/test_group_grants.py",
    ),
    Mutation(
        "FXM-M1-5",
        "The failed-authentication bound reads the proxy-appended X-Forwarded-For entry, not the caller's whole header.",
        "management/backend/src/aira_management/config/settings.py",
        '    "NUM_PROXIES": _settings.trusted_proxy_hops,',
        '    "NUM_PROXIES": None,',
        "management/backend/tests/test_the_api_is_rate_limited.py management/backend/tests/test_hardening.py",
    ),
    Mutation(
        "FXM-M1-6a",
        "The installation-wide anomaly-rule list reaches no retired use case's rules.",
        "management/backend/src/aira_management/apps/anomalies/views.py",
        "        visible = scope_queryset(self.request.user, VIEW, UseCase.objects.live())",
        "        visible = scope_queryset(self.request.user, VIEW, UseCase.objects.all())",
        "management/backend/tests/test_soft_delete.py",
    ),
    Mutation(
        "FXM-M1-6b",
        "Nobody manages a retired use case, whatever guardian rows remain.",
        "management/backend/src/aira_management/apps/usecases/access.py",
        "    return not usecase.is_deleted and _holds(user, MANAGE, usecase)",
        "    return _holds(user, MANAGE, usecase)",
        "management/backend/tests/test_soft_delete.py",
    ),
    Mutation(
        "FXM-M1-6c",
        "Administering only a retired use case opens no directory search.",
        "management/backend/src/aira_management/rbac.py",
        "        return get_objects_for_user(user, MANAGE_PERM, klass=UseCase.objects.live()).exists()",
        "        return get_objects_for_user(user, MANAGE_PERM, klass=UseCase.objects.all()).exists()",
        "management/backend/tests/test_directory.py",
    ),
    Mutation(
        "FXM-M1-8a",
        "A direct membership is refused under a name two people sign in with.",
        "management/backend/src/aira_management/apps/usecases/views/grants.py",
        "    namesakes = sorted(other.get_username() for other in namesakes_of(name))",
        "    namesakes: list[str] = []",
        "management/backend/tests/test_an_identity_is_claimed_once.py",
    ),
    Mutation(
        "FXM-M1-8b",
        "A renamed account is not granted under a name its owner never signs in with.",
        "management/backend/src/aira_management/apps/usecases/views/grants.py",
        "    if signed_in_as is not None:",
        "    if False:",
        "management/backend/tests/test_an_identity_is_claimed_once.py",
    ),
    Mutation(
        "FXM-M1-9",
        "Behind a declared proxy its X-Forwarded-Proto is believed, so HSTS is sent.",
        "management/backend/src/aira_management/config/security.py",
        '    return ("HTTP_X_FORWARDED_PROTO", "https") if settings.trusted_proxy_hops > 0 else None',
        "    return None",
        "management/backend/tests/test_hardening.py",
    ),
    Mutation(
        "FXM-M2-10a",
        "a fresh seed keeps every contribution's use cases, so none is retired and recreated",
        "management/backend/src/aira_management/apps/seed/contributions/showcase.py",
        "    for stale in UseCase.objects.exclude(slug__in=seeded_use_cases()):",
        "    for stale in UseCase.objects.exclude(slug__in=_declared_slugs()):",
        "management/backend/tests/test_seed.py",
    ),
    Mutation(
        "FXM-M2-10b",
        "a fresh seed keeps a person who requested a test run instead of aborting",
        "management/backend/src/aira_management/apps/seed/contributions/roles_and_users.py",
        "            TestRun.objects.filter(requested_by=person).exists()",
        "            False",
        "management/backend/tests/test_seed.py",
    ),
    Mutation(
        "FXM-M2-10c",
        "a fresh seed announces the keys it removes",
        "management/backend/src/aira_management/apps/seed/contributions/roles_and_users.py",
        "                    _revoke_key(key, key.use_case.slug)",
        "                    pass",
        "management/backend/tests/test_seed.py",
    ),
    Mutation(
        "FXM-M2-10d",
        "a fresh seed never revokes a demo key it reissues",
        "management/backend/src/aira_management/apps/seed/contributions/roles_and_users.py",
        "                if key.prefix not in reissued:",
        "                if True:",
        "management/backend/tests/test_seed.py",
    ),
    Mutation(
        "FXM-M2-10e",
        "a fresh seed announces the memberships it removes",
        "management/backend/src/aira_management/apps/seed/contributions/roles_and_users.py",
        "            for membership in UseCaseMembership.objects.filter(user=person).select_related(",
        "            for membership in UseCaseMembership.objects.none().select_related(",
        "management/backend/tests/test_seed.py",
    ),
    Mutation(
        "FXM-M2-11e",
        "the export's file name carries nothing a header cannot",
        "management/backend/src/aira_management/apps/smoketests/views/export.py",
        "        f\"aira-smoketest-{_UNSAFE_IN_A_FILE_NAME.sub('_', run.model)}-{run.started_at.date()}.csv\"",
        '        f"aira-smoketest-{run.model}-{run.started_at.date()}.csv"',
        "management/backend/tests/test_smoketests.py",
    ),
    Mutation(
        "FXM-M2-11n",
        "A model name the gateway could never look up is refused where it is catalogued.",
        "management/backend/src/aira_management/apps/catalog/serializers.py",
        "        problem = name_problem(name)",
        "        problem = None",
        "management/backend/tests/test_catalog.py",
    ),
    Mutation(
        "FXM-M2-12a",
        "A step's own fallback chain is bounded where it is authored.",
        "management/backend/src/aira_management/apps/pipelines/serializers.py",
        "    _check_step_chain(config)",
        "    pass",
        "management/backend/tests/test_pipelines.py",
    ),
    Mutation(
        "FXM-M2-12b",
        "The dispatch chain is bounded in length.",
        "management/backend/src/aira_management/apps/pipelines/serializers.py",
        "        if len(value) > MAX_FALLBACK_MODELS:",
        "        if False:",
        "management/backend/tests/test_pipelines.py",
    ),
    Mutation(
        "FXM-M2-13a",
        "?use_case= lists only that use case's runs",
        "management/backend/src/aira_management/apps/smoketests/views/runs.py",
        "            runs = runs.filter(use_case=use_case)",
        "            pass",
        "management/backend/tests/test_smoketests.py",
    ),
    Mutation(
        "FXM-M2-13b",
        "a run started without a model enters at the first released one",
        "management/backend/src/aira_management/apps/smoketests/views/runs.py",
        "            return released[0]",
        "            return released[-1]",
        "management/backend/tests/test_smoketests.py",
    ),
    Mutation(
        "FXM-M2-13c",
        "a run's answers are read only by somebody who could have started it",
        "management/backend/src/aira_management/apps/smoketests/views/runs.py",
        '        run = self.get_object()\n        rows = run.results.select_related("case", "rated_by").all()',
        '        run = TestRun.objects.get(pk=pk)\n        rows = run.results.select_related("case", "rated_by").all()',
        "management/backend/tests/test_smoketests.py",
    ),
    Mutation(
        "FXM-M2-13d",
        "only the requester finishes a run",
        "management/backend/src/aira_management/apps/smoketests/views/runs.py",
        "        if run.requested_by_id != request.user.pk:",
        "        if False:",
        "management/backend/tests/test_smoketests.py",
    ),
    Mutation(
        "FXM-M2-13e",
        "finishing twice keeps the moment the run ended",
        "management/backend/src/aira_management/apps/smoketests/views/runs.py",
        "        if run.finished_at is None:",
        "        if True:",
        "management/backend/tests/test_smoketests.py",
    ),
    Mutation(
        "FXM-M2-13t",
        "A throttle rule must say its rate.",
        "management/backend/src/aira_management/apps/anomalies/serializers.py",
        '            if not self._effective(attrs, "throttle_rpm"):',
        "            if False:",
        "management/backend/tests/test_anomaly_rules.py",
    ),
    Mutation(
        "FXM-M2-14a",
        "Delivered and parked outbox rows are deleted after KEPT_AFTER_DAYS.",
        "management/backend/src/aira_management/apps/outbox/management/commands/relay.py",
        "    cutoff = now - timedelta(days=KEPT_AFTER_DAYS)",
        "    cutoff = now - timedelta(days=KEPT_AFTER_DAYS * 1000)",
        "management/backend/tests/test_outbox.py",
    ),
    Mutation(
        "FXM-M2-14b",
        "The privacy notice states the outbox retention from the relay's own constant.",
        "management/backend/src/aira_management/apps/privacy/notice.py",
        '    filled["outbox_retention"] = language.values["days"].format(days=KEPT_AFTER_DAYS)',
        '    filled["outbox_retention"] = language.values["days"].format(days=7)',
        "management/backend/tests/test_privacy_notice.py",
    ),
    Mutation(
        "FXM-M2-15",
        "The catalogue migration announces every model with the full event.",
        "management/backend/src/aira_management/apps/catalog/migrations/0009_announce_every_model.py",
        '        "addressing": model.addressing,',
        "",
        "management/backend/tests/test_a_catalog_migration_announces_every_model.py",
    ),
    Mutation(
        "FXM-M2-16b",
        "A budget limit of zero is refused by name.",
        "management/backend/src/aira_management/apps/budgets/serializers.py",
        "    if value is not None and value <= 0:",
        "    if value is not None and value < 0:",
        "management/backend/tests/test_budgets.py",
    ),
    Mutation(
        "FXM-M2-16c",
        "A throttle rate is bounded like a rate limit.",
        "management/backend/src/aira_management/apps/anomalies/models.py",
        "        null=True, blank=True, validators=[MaxValueValidator(MAX_REQUESTS_PER_MINUTE)]",
        "        null=True, blank=True",
        "management/backend/tests/test_anomaly_rules.py",
    ),
    Mutation(
        "FXM-M2-17a",
        "Every event with a builder is built by it, in views and seeds alike.",
        "management/backend/src/aira_management/apps/usecases/views/grants.py",
        '    emit("membership.upserted", _membership_payload(usecase, user.get_username(), role))',
        '    emit("membership.upserted", {"slug": usecase.slug, "username": user.get_username(), "role": role})',
        "management/backend/tests/test_outbox_routing.py",
    ),
    Mutation(
        "FXM-M2-17b",
        "The seed reads AIRA_OPENAI_SERVERS as the gateway does.",
        "management/backend/src/aira_management/apps/seed/contributions/local_models.py",
        '        url = rest.split("|")[0].strip()',
        '        url = rest.split("|")[0]',
        "tools/tests/test_both_planes_read_one_server_list.py",
    ),
    Mutation(
        "FXM-M2-18",
        "An event type no topic carries is an error, not a silent no-op.",
        "management/backend/src/aira_management/apps/outbox/subscriber.py",
        "    if base is None:",
        "    if base is None and False:",
        "management/backend/tests/test_outbox.py",
    ),
    Mutation(
        "FXM-M2-18a",
        "Dropping the member budget scope cannot be rolled back silently.",
        "management/backend/src/aira_management/apps/budgets/migrations/0004_drop_member_scope.py",
        "        migrations.RunPython(drop_member_budgets),",
        "        migrations.RunPython(drop_member_budgets, migrations.RunPython.noop),",
        "management/backend/tests/test_budgets.py",
    ),
    Mutation(
        "FXM-M2-19p",
        "The notice's version is the same whichever language it is read in.",
        "management/backend/src/aira_management/apps/privacy/views.py",
        "        version = notice.version(installation)",
        "        version = notice.version(installation) + code",
        "management/backend/tests/test_privacy_notice.py",
    ),
    Mutation(
        "FXM-M2-19s",
        "the showcase tests leave no event subscriber behind",
        "management/backend/tests/test_showcase_seed.py",
        "        events.unsubscribe(spy)",
        "        pass",
        "management/backend/tests/test_showcase_seed.py",
    ),
    Mutation(
        "FXM-M2-1a",
        "A thinking declaration carrying a key nothing reads is refused by name.",
        "management/backend/src/aira_management/apps/catalog/validation.py",
        '    errors = _unknown_keys(block, THINKING_KEYS, "thinking")',
        "    errors: list[str] = []",
        "management/backend/tests/test_catalog_declaration.py",
    ),
    Mutation(
        "FXM-M2-1d",
        "A platform key in a model's addressing is bounded text.",
        "management/backend/src/aira_management/apps/catalog/serializers.py",
        "            if any(len(text) > MAX_ADDRESSING_TEXT for text in texts):",
        "            if False:",
        "management/backend/tests/test_catalog.py",
    ),
    Mutation(
        "FXM-M2-1e",
        "A pipeline step's config carrying a key the gateway does not read is refused by name.",
        "management/backend/src/aira_management/apps/pipelines/serializers.py",
        '    _check_keys(config, STEP_CONFIG_KEYS[step_type], f"step.config of {step_type}")',
        "    pass",
        "management/backend/tests/test_pipelines.py",
    ),
    Mutation(
        "FXM-M2-2",
        "A relay run that cannot take the lock publishes nothing.",
        "management/backend/src/aira_management/apps/outbox/management/commands/relay.py",
        "            if not acquired:",
        "            if False:",
        "management/backend/tests/test_outbox.py",
    ),
    Mutation(
        "FXM-M2-20a",
        "the standing costs the same number of queries however many use cases there are",
        "management/backend/src/aira_management/apps/smoketests/views/stats.py",
        '            .prefetch_related("results")',
        "            .prefetch_related()",
        "management/backend/tests/test_smoketests.py",
    ),
    Mutation(
        "FXM-M2-20b",
        "listing where the catalogue can run reads releases through the prefetch",
        "management/backend/src/aira_management/apps/smoketests/views/runnable.py",
        "    return sorted(model.name for model in use_case.allowed_models.all())",
        '    return sorted(use_case.allowed_models.values_list("name", flat=True))',
        "management/backend/tests/test_smoketests.py",
    ),
    Mutation(
        "FXM-M2-21a",
        "A placeholder is allowed only where the renderer fills it in.",
        "management/backend/src/aira_management/apps/privacy/texts.py",
        "        allowed = set(PLACEHOLDERS) if filled_in(where) else allowed",
        '        allowed = set(PLACEHOLDERS) if not where.startswith(("values.", "access.")) else allowed',
        "management/backend/tests/test_privacy_notice_texts.py",
    ),
    Mutation(
        "FXM-M2-21b",
        "What an activity stores is part of the notice's digest.",
        "management/backend/src/aira_management/apps/privacy/edition.py",
        "                [[str(store.plane), store.table, list(store.columns)] for store in activity.stores],",
        "                [],",
        "management/backend/tests/test_privacy_notice.py",
    ),
    Mutation(
        "FXM-M2-22",
        "Whoever may set the installation budget may list it.",
        "management/backend/src/aira_management/apps/budgets/views.py",
        "        if may(request.user, Permission.BUDGET_INSTALLATION_WRITE):",
        "        if False:",
        "management/backend/tests/test_installation_budget.py management/backend/tests/test_the_pairwise_permission_matrix.py",
    ),
    Mutation(
        "FXM-M2-23",
        "A catalogue write that loses a race answers 409 with a sentence, never 500.",
        "management/backend/src/aira_management/apps/catalog/views.py",
        "        raise CatalogueConflict() from exc",
        "        raise",
        "management/backend/tests/test_catalog.py",
    ),
    Mutation(
        "FXM-M2-24",
        "a re-seed never fills a position IT Security retired",
        "management/backend/src/aira_management/apps/seed/contributions/test_catalogue.py",
        '        used = set(TestCase.objects.values_list("position", flat=True))',
        '        used = set(TestCase.objects.filter(retired=False).values_list("position", flat=True))',
        "management/backend/tests/test_seed.py",
    ),
    Mutation(
        "FXM-M2-24b",
        "a re-seed never rewrites a question at a position in use",
        "management/backend/src/aira_management/apps/seed/contributions/test_catalogue.py",
        "            if position in used:\n                continue",
        "            if False:\n                continue",
        "management/backend/tests/test_seed.py management/backend/tests/test_smoketests.py",
    ),
    Mutation(
        "FXM-M2-3",
        "An injection filter's action outside {block, flag} is refused where it is authored.",
        "management/backend/src/aira_management/apps/pipelines/serializers.py",
        '        _check_choice(config, "action", FilterAction)',
        "        pass",
        "management/backend/tests/test_pipelines.py",
    ),
    Mutation(
        "FXM-M2-4",
        "Removing a model re-announces every live use case it was released to.",
        "management/backend/src/aira_management/apps/catalog/views.py",
        "            for usecase in releasing:",
        "            for usecase in []:",
        "management/backend/tests/test_catalog.py",
    ),
    Mutation(
        "FXM-M2-5",
        "A capability or thinking mode that is a list or an object is refused by name, never a TypeError.",
        "management/backend/src/aira_management/apps/catalog/validation.py",
        "    return isinstance(value, str) and value in known",
        "    return value in known",
        "management/backend/tests/test_catalog_declaration.py",
    ),
    Mutation(
        "FXM-M2-5p",
        "A pipeline step whose type is a list or an object is refused by name.",
        "management/backend/src/aira_management/apps/pipelines/serializers.py",
        "            if not isinstance(step_type, str) or step_type not in STEP_TYPES:",
        "            if step_type not in STEP_TYPES:",
        "management/backend/tests/test_pipelines.py",
    ),
    Mutation(
        "FXM-M2-6a",
        "A budget without a period is a monthly one, never a KeyError.",
        "management/backend/src/aira_management/apps/budgets/serializers.py",
        "    period = serializers.ChoiceField(choices=Budget.PERIOD_CHOICES, default=Budget.MONTH)",
        "",
        "management/backend/tests/test_budgets.py management/backend/tests/test_installation_budget.py",
    ),
    Mutation(
        "FXM-M2-6b",
        "An installation-scoped budget is refused by name on a use case's route.",
        "management/backend/src/aira_management/apps/budgets/serializers.py",
        "        if scope not in self.SCOPES:",
        "        if False:",
        "management/backend/tests/test_budgets.py",
    ),
    Mutation(
        "FXM-M2-7a",
        "A re-posted rule is validated against the stored rule and stays a whole throttle.",
        "management/backend/src/aira_management/apps/anomalies/views.py",
        '    serializer = AnomalyRuleSerializer(existing, data=data, context={"use_case": usecase})',
        '    serializer = AnomalyRuleSerializer(data=data, context={"use_case": usecase})',
        "management/backend/tests/test_anomaly_rules.py",
    ),
    Mutation(
        "FXM-M2-8b",
        "A rule name clashing within its own scope is refused by name, never an IntegrityError.",
        "management/backend/src/aira_management/apps/anomalies/serializers.py",
        "        self._unique_name(attrs)",
        "",
        "management/backend/tests/test_anomaly_rules.py",
    ),
    Mutation(
        "FXM-M2-9a",
        "a test run is never deleted, not even by the use case's administrator",
        "management/backend/src/aira_management/apps/smoketests/views/runs.py",
        '    http_method_names = ["get", "post", "head", "options"]',
        '    http_method_names = ["get", "post", "delete", "head", "options"]',
        "management/backend/tests/test_smoketests.py",
    ),
    Mutation(
        "FXM-M2-9b",
        "a rated answer cannot be rewritten under its verdict",
        "management/backend/src/aira_management/apps/smoketests/views/runs.py",
        "            if result.verdict != Verdict.UNRATED:",
        "            if False:",
        "management/backend/tests/test_smoketests.py",
    ),
    Mutation(
        "FXM-M2-9c",
        "only the run's requester writes its answers",
        "management/backend/src/aira_management/apps/smoketests/views/runs.py",
        "            if result.run.requested_by_id != self.request.user.pk:",
        "            if False:",
        "management/backend/tests/test_smoketests.py",
    ),
    Mutation(
        "FXM-M2-9d",
        "a verdict taken back to unrated names no rater",
        "management/backend/src/aira_management/apps/smoketests/views/runs.py",
        "            serializer.save(rated_by=None, rated_at=None)",
        "            serializer.save()",
        "management/backend/tests/test_smoketests.py",
    ),
    Mutation(
        "FXM-M2-9e",
        "changing a rating's note names whoever changed it",
        "management/backend/src/aira_management/apps/smoketests/views/runs.py",
        "        if not any(field in data for field in JUDGEMENT_FIELDS):",
        '        if "verdict" not in data:',
        "management/backend/tests/test_smoketests.py",
    ),
    Mutation(
        "FXT1-1",
        "the Vault setup refuses a secret-id target git would commit, before Vault is asked for anything",
        "tools/vault_setup.py",
        "    if refusal is not None:",
        "    if False:",
        "tools/tests/test_the_vault_secret_id_stays_out_of_git.py",
    ),
    Mutation(
        "FXT1-10",
        "every Python mutant in the harness still parses",
        "tools/mutation_check.py",
        '        "            except Exception as exc:  # a failed write must never take the worker down with it",\n        "            except ValueError as exc:",',
        '        "            except Exception as exc:  # a failed write must never take the worker down",\n        "            except ValueError as exc:",',
        "tools/tests/test_mutation_anchors.py",
    ),
    Mutation(
        "FXT1-11",
        "the demo reset clears only the demo's own Redis keys",
        "tools/demo_reset_usage.py",
        "            for pattern in redis_patterns(slug):",
        '            for pattern in [f"*{slug}*"]:',
        "tools/tests/test_the_demo_reset_forgives_only_the_demo.py",
    ),
    Mutation(
        "FXT1-12",
        "the demo scripts reach the Postgres this stack publishes",
        "tools/demo_reset_usage.py",
        "    return os.environ.get(DSN_VARIABLE) or stack_addresses.postgres_url(",
        '    return os.environ.get(DSN_VARIABLE) or "postgresql+psycopg://aira:aira-local@localhost:5432/aira_gateway" or stack_addresses.postgres_url(',
        "tools/tests/test_the_demo_reset_forgives_only_the_demo.py",
    ),
    Mutation(
        "FXT1-13",
        "the broker advertises the host port it is published on",
        "deploy/compose/docker-compose.yml",
        "      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://kafka:9092,PLAINTEXT_HOST://localhost:${AIRA_PUBLISH_KAFKA_PORT:-29092}",
        "      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://kafka:9092,PLAINTEXT_HOST://localhost:29092",
        "tools/tests/test_kafka_is_reached_where_it_is_published.py",
    ),
    Mutation(
        "FXT1-14",
        "config-check asks the planes without reading a developer's .env",
        "tools/config_check.py",
        '    print(json.dumps({{"ok": True, "problems": unsafe_settings(S(_env_file=None))}}))',
        '    print(json.dumps({{"ok": True, "problems": unsafe_settings(S())}}))',
        "tools/tests/test_a_config_is_checked_before_it_is_deployed.py",
    ),
    Mutation(
        "FXT1-15",
        "the realm repair is told the console's port",
        "deploy/compose/docker-compose.showcase.yml",
        "      AIRA_CONSOLE_PORT: ${AIRA_CONSOLE_PORT:-${AIRA_PUBLISH_FRONTEND_PORT:-${AIRA_FRONTEND_PORT:-4200}}}",
        "",
        "tools/tests/test_the_realm_follows_the_console_port.py",
    ),
    Mutation(
        "FXT1-16",
        "every published port binds through the bind-host variables",
        "deploy/compose/docker-compose.yml",
        '      - "${AIRA_BIND_HOST:-127.0.0.1}:${AIRA_PUBLISH_VAULT_PORT:-8200}:8200"',
        '      - "${AIRA_PUBLISH_VAULT_PORT:-8200}:8200"',
        "tools/tests/test_every_published_port_serves_both_families.py",
    ),
    Mutation(
        "FXT1-17",
        "--only A1,B2 selects a subset in every spelling",
        "tools/mutation_check.py",
        '    named = [ident.strip() for value in arguments.only for ident in value.split(",")]\n    if arguments.only and not any(named):',
        "    named = [value.strip() for value in arguments.only]\n    if arguments.only and not any(named):",
        "tools/tests/test_the_mutation_harness_reads_what_it_runs.py",
    ),
    Mutation(
        "FXT1-17b",
        "a selection that could not run in full is never counted as caught",
        "tools/mutation_check.py",
        "    for outcome in (Outcome.BROKEN, Outcome.FAILED, Outcome.TIMED_OUT):\n        details = [detail for found, detail in results if found is outcome]",
        "    for outcome in (Outcome.FAILED, Outcome.BROKEN, Outcome.TIMED_OUT):\n        details = [detail for found, detail in results if found is outcome]",
        "tools/tests/test_the_mutation_harness_reads_what_it_runs.py",
    ),
    Mutation(
        "FXT1-17c",
        "a console run that reached no test is broken, not caught",
        "tools/mutation_check.py",
        '        if not NG_TESTS_RAN.search(text):\n            return Outcome.BROKEN, f"ng test exited {code} without running a test file"',
        '        if False:\n            return Outcome.BROKEN, f"ng test exited {code} without running a test file"',
        "tools/tests/test_the_mutation_harness_reads_what_it_runs.py",
    ),
    Mutation(
        "FXT1-17d",
        "a run that hangs is stopped at its time limit",
        "tools/mutation_check.py",
        "        output, _ = process.communicate(timeout=timeout)\n    except subprocess.TimeoutExpired:",
        "        output, _ = process.communicate()\n    except subprocess.TimeoutExpired:",
        "tools/tests/test_the_mutation_harness_reads_what_it_runs.py",
    ),
    Mutation(
        "FXT1-18",
        "make lint-frontend builds the console in the configuration CI builds",
        "Makefile",
        "\tcd $(FRONTEND_DIR) && npx ng build\n",
        "\tcd $(FRONTEND_DIR) && npx ng build --configuration development\n",
        "tools/tests/test_ci_runs_what_a_developer_runs.py",
    ),
    Mutation(
        "FXT1-19",
        "every stated number of Kafka topics is the number there are",
        "docs/DEPLOYMENT.md",
        "### Step 3 — Kafka: nine compacted topics",
        "### Step 3 — Kafka: eight compacted topics",
        "tools/tests/test_kafka_topics_are_created.py",
    ),
    Mutation(
        "FXT1-1b",
        "the secret-id file is never readable by anyone else, not even while it is written",
        "tools/vault_setup.py",
        "            os.fchmod(handle.fileno(), 0o600)",
        "            os.fchmod(handle.fileno(), 0o644)",
        "tools/tests/test_the_vault_secret_id_stays_out_of_git.py",
    ),
    Mutation(
        "FXT1-1c",
        "a secret-id the setup mints expires",
        "tools/vault_setup.py",
        '                "secret_id_ttl": secret_id_ttl,',
        "",
        "tools/tests/test_the_vault_secret_id_stays_out_of_git.py",
    ),
    Mutation(
        "FXT1-2",
        "a rendered value Compose would interpolate or unescape reaches the container unchanged",
        "tools/config_render.py",
        "        return f\"'{value}'\"",
        "        return f'\"{value}\"'",
        "tools/tests/test_a_rendered_value_reaches_the_container_unchanged.py tools/tests/test_the_config_examples_are_real.py",
    ),
    Mutation(
        "FXT1-20",
        "a load-test step's log window names its time zone",
        "tools/loadtest/sample.py",
        '    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())',
        '    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())',
        "tools/tests/test_the_load_double_keeps_its_promise.py",
    ),
    Mutation(
        "FXT1-21",
        "otel-status exits non-zero whenever it reports a loss",
        "tools/otel_status.py",
        "    # hides is invisible to every script and CI step that runs this.\n    return 1",
        "    # hides is invisible to every script and CI step that runs this.\n    return 0",
        "tools/tests/test_otel_status_says_a_loss_with_its_exit_code.py",
    ),
    Mutation(
        "FXT1-22",
        "only the canonical spelling of a version is a release tag",
        "tools/release_version.py",
        '_RELEASE_TAG = re.compile(r"v(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)")',
        '_RELEASE_TAG = re.compile(r"v(\\d+)\\.(\\d+)\\.(\\d+)")',
        "tools/tests/test_the_release_version_comes_from_the_tags.py tools/tests/test_a_release_is_published_only_behind_the_gates.py",
    ),
    Mutation(
        "FXT1-24",
        "every demo script derives the use case's own key the way the seed mints it",
        "tools/demo_keys.py",
        '    material = f"{DEMO_KEY_SALT}:{slug}" if person is None else f"{DEMO_KEY_SALT}:{slug}:{person}"',
        '    material = f"{DEMO_KEY_SALT}:{slug}:{person}"',
        "management/backend/tests/test_showcase_seed.py",
    ),
    Mutation(
        "FXT1-25",
        "an unreachable gateway is reported in a line, not a traceback",
        "tools/demo_traffic.py",
        "    except httpx.HTTPError as error:",
        "    except ValueError as error:",
        "tools/tests/test_the_demo_traffic_says_what_went_wrong.py",
    ),
    Mutation(
        "FXT1-26",
        "the Keycloak password reaches SQL as a psql variable",
        "deploy/compose/postgres/init/01-init-databases.sh",
        "    CREATE ROLE keycloak WITH LOGIN PASSWORD :'kcpass';",
        "    CREATE ROLE keycloak WITH LOGIN PASSWORD '${KC_DB_PASSWORD}';",
        "tools/tests/test_the_database_init_quotes_its_password.py",
    ),
    Mutation(
        "FXT1-27",
        "a release script receives the tag name through the environment, never spliced into its text",
        ".github/workflows/ci.yml",
        '        run: uv run python tools/release_notes.py --check "$TAG"',
        '        run: uv run python tools/release_notes.py --check "${{ github.ref_name }}"',
        "tools/tests/test_a_release_is_published_only_behind_the_gates.py",
    ),
    Mutation(
        "FXT1-3",
        "the budget plugin never sends the key from the environment to another provider's address",
        "tools/opencode/plugin/aira-budget.js",
        "  const paired = entry !== null && (entry.own || (!env.key && !env.base));",
        "  const paired = entry !== null;",
        "tools/tests/test_the_opencode_plugin_holds_its_properties.py",
    ),
    Mutation(
        "FXT1-4",
        "vault_setup --from-env copies every credential setting of both planes",
        "tools/vault_setup.py",
        "KNOWN_SECRETS = tuple(sorted(credential_settings.credentials()))",
        'KNOWN_SECRETS = ("AIRA_GOOGLE_API_KEY", "AIRA_POSTGRES_PASSWORD", "AIRA_SECRET_KEY")',
        "tools/tests/test_one_list_of_credentials.py",
    ),
    Mutation(
        "FXT1-4b",
        "a switch that only mentions credentials is not taken for one",
        "tools/credential_settings.py",
        "        if name.endswith(SUFFIXES) and _is_text(annotation)",
        "        if name.endswith(SUFFIXES)",
        "tools/tests/test_one_list_of_credentials.py",
    ),
    Mutation(
        "FXT1-5",
        "make help says up-apps starts the development realm and Vault",
        "Makefile",
        "up-apps: env ## Start the product on the development infrastructure (dev realm and dev Vault included)",
        "up-apps: env ## Start the product: infra + the application processes, no demo provisioning",
        "tools/tests/test_the_core_stack_carries_no_demo.py",
    ),
    Mutation(
        "FXT1-6",
        "the release tests ask a scratch repository, never the checkout they run in",
        "tools/tests/test_a_release_says_what_it_is.py",
        '    monkeypatch.setattr(release_notes, "ROOT", repository)',
        "    pass",
        "tools/tests/test_a_release_says_what_it_is.py",
    ),
    Mutation(
        "FXT1-7",
        "the load-test cleanup removes only the use cases it seeded",
        "tools/loadtest/seed.py",
        '        ("DELETE FROM request_logs WHERE use_case IN :slugs", by_slug),',
        "        (\"DELETE FROM request_logs WHERE use_case LIKE 'lt-%'\", {}),",
        "tools/tests/test_the_load_test_cleans_only_what_it_made.py",
    ),
    Mutation(
        "FXT1-8",
        "a compressed body is inflated only until it passes the inspector's keep limit",
        "tools/otlp_inspector.py",
        "                part = stream.decompress(pending, INFLATE_STEP)",
        "                part = stream.decompress(pending)",
        "tools/tests/test_the_inspector_shows_what_was_forwarded.py",
    ),
    Mutation(
        "FXT1-8b",
        "an announced body longer than the limit is refused unread",
        "tools/otlp_inspector.py",
        "        if length > self.inspector.max_body:",
        "        if False:",
        "tools/tests/test_the_inspector_shows_what_was_forwarded.py",
    ),
    Mutation(
        "FXT1-9",
        "a killed run's mutant is put back before any check that reads the source",
        "tools/mutation_check.py",
        "    _recover()\n    _refuse_duplicate_ids()\n    _refuse_ambiguous_anchors()",
        "    _refuse_duplicate_ids()\n    _refuse_ambiguous_anchors()\n    _recover()",
        "tools/tests/test_the_mutation_harness_reads_what_it_runs.py",
    ),
    Mutation(
        "FXT1-9b",
        "a file edited while its mutant was on disk is not overwritten",
        "tools/mutation_check.py",
        "    if current == mutated:\n        path.write_text(original)\n        return True\n    return current == original",
        "    path.write_text(original)\n    return True",
        "tools/tests/test_the_mutation_harness_reads_what_it_runs.py",
    ),
    Mutation(
        "FXT1-T224",
        "the local catalog seed never takes a numeric id from another model unasked",
        "tools/seed_local_catalog.py",
        "        if holders and not move_id:",
        "        if False:",
        "tools/tests/test_the_local_catalog_seed_keeps_an_id_it_does_not_own.py",
    ),
    Mutation(
        "G4-1",
        "a period's running token total is 64-bit",
        "gateway/src/aira_gateway/db/models/owned.py",
        "    tokens: Mapped[int] = mapped_column(BigInteger, default=0)",
        "    tokens: Mapped[int] = mapped_column(Integer, default=0)",
        "gateway/tests/test_a_number_cannot_switch_the_budget_off.py",
    ),
    Mutation(
        "G4-3",
        "the JWT pattern costs time linear in the text",
        "gateway/src/aira_gateway/persistence/redaction.py",
        '    r"(?:(?<![A-Za-z0-9_-])|(?<=\\\\[nrt]))eyJ[A-Za-z0-9_-]++\\.[A-Za-z0-9_-]++\\.[A-Za-z0-9_-]++",',
        '    r"eyJ[A-Za-z0-9\\-_]+\\.[A-Za-z0-9\\-_]+\\.[A-Za-z0-9\\-_]+",',
        "gateway/tests/test_redaction.py",
    ),
    Mutation(
        "G4-3b",
        "the private-key pattern costs time linear in the text and covers a block cut off before its END line",
        "gateway/src/aira_gateway/persistence/redaction.py",
        '    r"[^-]*+(?:-----END [A-Z ]{0,32}PRIVATE KEY-----)?",',
        '    r"(?s:.*?)-----END [A-Z ]{0,32}PRIVATE KEY-----",',
        "gateway/tests/test_redaction.py",
    ),
    Mutation(
        "G5-1",
        "a retirement applied to a read-model with no row for the use case still leaves the tombstone",
        "gateway/src/aira_gateway/consumer/apply.py",
        "    if await session.get(UseCaseRead, slug) is None:",
        "    if False:",
        "gateway/tests/test_consumer_apply.py gateway/tests/test_a_retired_use_case_serves_nothing.py",
    ),
    Mutation(
        "G5-10",
        "a reader restricted inside one use case still sees requests with no use case",
        "gateway/src/aira_gateway/api/reporting/traces.py",
        "                RequestLog.use_case.is_(None),",
        "",
        "gateway/tests/test_traces.py",
    ),
    Mutation(
        "G5-10b",
        "a reader restricted inside one use case still sees a global rule's findings",
        "gateway/src/aira_gateway/api/reporting/anomalies.py",
        "        AnomalyEvent.use_case.is_(None),",
        "",
        "gateway/tests/test_anomaly_engine.py",
    ),
    Mutation(
        "G5-12",
        "a finding's addresses are shown only to a reader who may investigate",
        "gateway/src/aira_gateway/api/reporting/anomalies.py",
        '        "detail": row.detail if investigating else without_addresses(row.detail),',
        '        "detail": row.detail,',
        "gateway/tests/test_anomaly_engine.py",
    ),
    Mutation(
        "G5-12b",
        "a rule-made stop's reason names addresses only to a reader who may investigate",
        "gateway/src/aira_gateway/anomalies/suspensions.py",
        "    if not addresses and row.author.startswith(RULE_AUTHOR):",
        "    if False:",
        "gateway/tests/test_suspensions.py",
    ),
    Mutation(
        "G5-13",
        "the enforcement cache loads only suspensions still in force",
        "gateway/src/aira_gateway/anomalies/suspensions.py",
        "    stmt = select(AccessSuspension).where(in_force(datetime.now(UTC)))",
        "    stmt = select(AccessSuspension).where(AccessSuspension.lifted_at.is_(None))",
        "gateway/tests/test_suspensions.py",
    ),
    Mutation(
        "G5-13b",
        "every suspension in force is listed whatever its age",
        "gateway/src/aira_gateway/api/incidents/suspensions.py",
        "            (await session.execute(stmt.where(in_force(now)).order_by(*newest_first))).scalars()",
        "            (await session.execute(stmt.where(in_force(now)).order_by(*newest_first).limit(0))).scalars()",
        "gateway/tests/test_suspensions.py",
    ),
    Mutation(
        "G5-14",
        "a rule-made suspension applies as soon as the tick that wrote it returns",
        "gateway/src/aira_gateway/anomalies/service.py",
        "            self.suspensions.invalidate()",
        "            pass",
        "gateway/tests/test_suspensions.py",
    ),
    Mutation(
        "G5-15",
        "a payload that is not an object is redacted before it is stored",
        "gateway/src/aira_gateway/persistence/writer.py",
        "                payload = as_object(payload)",
        "                pass",
        "gateway/tests/test_log_writer.py",
    ),
    Mutation(
        "G5-16",
        "a model call the gateway made itself is not reported as expired",
        "gateway/src/aira_gateway/payloads.py",
        "        if _is_the_gateways_own_call(row):",
        "        if False:",
        "gateway/tests/test_payload_access.py",
    ),
    Mutation(
        "G5-17",
        "a stop on a whole use case names a slug",
        "gateway/src/aira_gateway/api/incidents/suspensions.py",
        "    if target == RuleTarget.USE_CASE.value and not is_valid_use_case(value):",
        "    if False:",
        "gateway/tests/test_suspensions.py",
    ),
    Mutation(
        "G5-17b",
        "a suspension target with a control character is refused, never a 500",
        "gateway/src/aira_gateway/api/incidents/suspensions.py",
        "    if printable(value) != value:",
        "    if False:",
        "gateway/tests/test_suspensions.py",
    ),
    Mutation(
        "G5-18",
        "a content reader's roles are recorded however many they hold",
        "gateway/src/aira_gateway/db/models/owned.py",
        "    roles: Mapped[str | None] = mapped_column(Text, nullable=True)",
        "    roles: Mapped[str | None] = mapped_column(String(255), nullable=True)",
        "gateway/tests/test_the_content_read_log.py",
    ),
    Mutation(
        "G5-19",
        "a reporting window is read in UTC whatever offset it was given in",
        "gateway/src/aira_gateway/api/reporting/common.py",
        "        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)",
        "        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)",
        "gateway/tests/test_usage_series.py",
    ),
    Mutation(
        "G5-1b",
        "a key of a retired use case arriving after its tombstone is written deactivated",
        "gateway/src/aira_gateway/consumer/apply.py",
        '        active = not await _is_retired(session, payload.get("use_case"))',
        "        active = True",
        "gateway/tests/test_consumer_apply.py",
    ),
    Mutation(
        "G5-1c",
        "usecase.deleted carries the use case as it stood, so a rebuilt tombstone keeps its retention promise",
        "management/backend/src/aira_management/apps/usecases/views/lifecycle.py",
        '            emit("usecase.deleted", _snapshot(usecase))',
        '            emit("usecase.deleted", {"slug": usecase.slug})',
        "management/backend/tests/test_soft_delete.py",
    ),
    Mutation(
        "G5-2",
        "a reader whose splits are people-limited cannot reach a colleague's figures through person=",
        "gateway/src/aira_gateway/api/reporting/spend.py",
        "    if limited is not None and person and person not in {limited, principal.subject}:",
        "    if False:",
        "gateway/tests/test_whose_figures_a_member_sees.py",
    ),
    Mutation(
        "G5-20",
        "a rate is compared with its threshold exactly, never rounded first",
        "gateway/src/aira_gateway/anomalies/evaluator.py",
        "        if (matched or 0) * 100 < rule.threshold * total:",
        "        if round((matched or 0) * 100 / total) < rule.threshold:",
        "gateway/tests/test_anomaly_engine.py",
    ),
    Mutation(
        "G5-20b",
        "a ratio is compared with its threshold exactly, never rounded first",
        "gateway/src/aira_gateway/anomalies/evaluator.py",
        "        if amount * 100 < rule.threshold * was:",
        "        if round(amount * 100 / was) < rule.threshold:",
        "gateway/tests/test_anomaly_engine.py",
    ),
    Mutation(
        "G5-21",
        "an empty username is no name, in the report as in the detector",
        "gateway/src/aira_gateway/db/models/owned.py",
        'REQUEST_PERSON = func.coalesce(func.nullif(RequestLog.username, ""), RequestLog.subject)',
        "REQUEST_PERSON = func.coalesce(RequestLog.username, RequestLog.subject)",
        "gateway/tests/test_reporting.py gateway/tests/test_anomaly_engine.py",
    ),
    Mutation(
        "G5-22a",
        "a refusal with no usage is not counted as unmetered",
        "gateway/src/aira_gateway/reporting/service.py",
        "                    & _was_served(),",
        "                    & True,",
        "gateway/tests/test_usage_series.py",
    ),
    Mutation(
        "G5-22b",
        "a row stored without payloads still carries its accounting",
        "gateway/src/aira_gateway/persistence/writer.py",
        "                cost_nanos=entry.cost_nanos,",
        "                cost_nanos=None,",
        "gateway/tests/test_log_writer.py",
    ),
    Mutation(
        "G5-22d",
        "an arriving configuration event is applied, not merely seen",
        "gateway/src/aira_gateway/consumer/apply.py",
        '    "usecase.upserted": _upsert_usecase,',
        '    "usecase.upserted_": _upsert_usecase,',
        "gateway/tests/test_the_channel_is_wired_to_what_it_names.py",
    ),
    Mutation(
        "G5-23",
        "the paging index is declared descending, as the migration built it (integration layer)",
        "gateway/src/aira_gateway/db/models/owned.py",
        "    RequestLog.created_at.desc(),",
        "    RequestLog.created_at,",
        "tests/integration/test_the_gateway_migrations_match_its_models.py",
    ),
    Mutation(
        "G5-24",
        "a count no 32-bit column can hold costs the audit row nothing",
        "gateway/src/aira_gateway/persistence/service.py",
        "    return max(0, min(int(value), COUNT_CEILING))",
        "    return int(value)",
        "gateway/tests/test_persistence_service.py",
    ),
    Mutation(
        "G5-27",
        "the payload walks run off the event loop",
        "gateway/src/aira_gateway/persistence/writer.py",
        "            request_payload, response_payload = await asyncio.to_thread(",
        "            request_payload, response_payload = (lambda f: f())(",
        "gateway/tests/test_log_writer.py",
    ),
    Mutation(
        "G5-28",
        "the erasure record is shown to every reader of the register",
        "gateway/src/aira_gateway/reporting/register.py",
        "                last_erasure=await self._last_erasure(session),",
        "                last_erasure=await self._last_erasure(session) if scope is None else None,",
        "gateway/tests/test_register.py",
    ),
    Mutation(
        "G5-3",
        "an Authorization header is redacted with its credential, not only its scheme word",
        "gateway/src/aira_gateway/persistence/redaction.py",
        '    r"(?i)authorization[\\"\']?[ \\t]*+[:=][ \\t]*+[\\"\']?[^\\r\\n\\"\']++",',
        '    r"(?i)authorization\\s*:\\s*\\S+",',
        "gateway/tests/test_redaction.py",
    ),
    Mutation(
        "G5-4",
        "a message's value is decoded inside the consumer's guard, so no shape of message stops it",
        "gateway/src/aira_gateway/consumer/worker.py",
        "    event_type = decode_event_type(message.headers)",
        "    event_type = decode_event_type(message.headers); event_payload(message.value)",
        "gateway/tests/test_config_distribution_survives_a_bad_event.py",
    ),
    Mutation(
        "G5-4b",
        "a header that is not UTF-8 never stops the consumer",
        "libs/src/aira_common/observability/propagation.py",
        '        key: (value.decode(errors="replace") if isinstance(value, bytes) else value)',
        "        key: (value.decode() if isinstance(value, bytes) else value)",
        "gateway/tests/test_config_distribution_survives_a_bad_event.py",
    ),
    Mutation(
        "G5-5",
        "an anomaly measurement is written within what its column holds",
        "gateway/src/aira_gateway/anomalies/service.py",
        "            observed=min(finding.observed, OBSERVED_CEILING),",
        "            observed=finding.observed,",
        "gateway/tests/test_anomaly_engine.py",
    ),
    Mutation(
        "G5-5b",
        "one rule's database error costs no other rule its findings",
        "gateway/src/aira_gateway/anomalies/service.py",
        "            except SQLAlchemyError as exc:",
        "            except ZeroDivisionError as exc:",
        "gateway/tests/test_anomaly_engine.py",
    ),
    Mutation(
        "G5-6",
        "a model check is no detector rule's traffic",
        "gateway/src/aira_gateway/anomalies/evaluator.py",
        '    stmt = stmt.where(group.is_not(None), group != "", traffic)',
        '    stmt = stmt.where(group.is_not(None), group != "")',
        "gateway/tests/test_anomaly_engine.py",
    ),
    Mutation(
        "G5-7",
        "a database error names the statement and not the values bound into it",
        "gateway/src/aira_gateway/db/base.py",
        '_ENGINE_OPTIONS: dict[str, Any] = {"hide_parameters": True}',
        '_ENGINE_OPTIONS: dict[str, Any] = {"hide_parameters": False}',
        "gateway/tests/test_db.py",
    ),
    Mutation(
        "G5-7b",
        "a database's DETAIL, which quotes refused rows, is cut from what is logged",
        "gateway/src/aira_gateway/db/errors.py",
        "    head, found, rest = text.partition(_DETAIL)",
        '    head, found, rest = text, "", ""',
        "gateway/tests/test_db.py",
    ),
    Mutation(
        "G5-8",
        "a content read records the method and credential that read",
        "gateway/src/aira_gateway/api/reporting/traces.py",
        "        auth_method=principal.method,",
        "        auth_method=None,",
        "gateway/tests/test_the_content_read_log.py",
    ),
    Mutation(
        "G5-9",
        "an answer is returned when its audit row cannot be written, unless the installation writes rows before responses",
        "gateway/src/aira_gateway/persistence/recorder.py",
        "        if writer.synchronous:",
        "        if True:",
        "gateway/tests/test_an_answer_outlives_its_audit_row.py",
    ),
    Mutation(
        "ME1",
        "a JSON body is read only as a Unicode text, never through a byte transform such as bz2",
        "management/backend/src/aira_management/apps/api/parsers.py",
        "    if name not in JSON_ENCODINGS:",
        "    if False:",
        "management/backend/tests/test_a_callers_value_is_never_a_server_error.py",
    ),
    Mutation(
        "ME2",
        "a body larger than the plane holds is refused unread, with or without a proxy",
        "management/backend/src/aira_management/apps/api/parsers.py",
        "        if limit is not None and len(raw) > limit:",
        "        if False:",
        "management/backend/tests/test_a_callers_value_is_never_a_server_error.py",
    ),
    Mutation(
        "ME3",
        "a body's depth is measured on its text, whatever encoding it arrived in",
        "management/backend/src/aira_management/apps/api/parsers.py",
        '        if nests_deeper_than(text.encode("utf-8", "surrogatepass"), MAX_JSON_DEPTH):',
        "        if nests_deeper_than(raw if isinstance(raw, bytes) else text.encode(), MAX_JSON_DEPTH):",
        "management/backend/tests/test_a_callers_value_is_never_a_server_error.py",
    ),
    Mutation(
        "ME4",
        "a failed budget booking loses neither the audit row nor the answer",
        "gateway/src/aira_gateway/api/serving/accounting.py",
        "    except Exception as exc:\n        # **The row is written whatever the booking did.**",
        "    except ZeroDivisionError as exc:\n        # **The row is written whatever the booking did.**",
        "gateway/tests/test_an_answer_outlives_its_audit_row.py",
    ),
    Mutation(
        "ME5",
        "a dry run offers steps past a block only to an administrator, whom the gateway alone serves them",
        "management/frontend/src/app/features/pipelines/pipeline-editor.html",
        '          [mayAuthor]="canManage()"\n',
        "",
        "management/frontend/src/app/features/pipelines/pipeline-editor.spec.ts",
    ),
    Mutation(
        "FXT2-10",
        "a use-case rule measures only its own use case's traffic",
        "gateway/src/aira_gateway/anomalies/evaluator.py",
        "        stmt = stmt.where(RequestLog.use_case == rule.use_case)",
        "        stmt = stmt",
        "gateway/tests/test_anomaly_engine.py tests/integration/test_anomaly_round.py",
    ),
    Mutation(
        "FXT2-23a",
        "a container is named the way Compose names it, stack prefix included",
        "tools/stack_addresses.py",
        "    return f\"{setting('AIRA_STACK')}-{service}\"",
        '    return f"aira-{service}"',
        "tools/tests/test_the_stack_settings_follow_compose.py",
    ),
    Mutation(
        "FXT2-23b",
        "a Compose default ends at its own brace, not at the end of the line",
        "tools/stack_addresses.py",
        "            value = text[start + len(marker) : index - 1]",
        '            value = text[start + len(marker) :].split("\\n")[0]',
        "tools/tests/test_the_stack_settings_follow_compose.py",
    ),
    Mutation(
        "FXT2-23c",
        "a database password is quoted into the URL, whatever characters it holds",
        "tools/stack_addresses.py",
        '    password = quote(setting("POSTGRES_PASSWORD"), safe="")',
        '    password = setting("POSTGRES_PASSWORD")',
        "tools/tests/test_the_stack_settings_follow_compose.py",
    ),
    Mutation(
        "FXT2-30",
        "every installation permission has a probe in the live matrix, checked in make test",
        "libs/src/aira_common/permissions.py",
        '    ROLE_MANAGE = "role.manage"\n',
        '    ROLE_MANAGE = "role.manage"\n    ROLE_UNPROBED = "role.unprobed"\n',
        "tests/integration/test_the_permission_matrix.py",
    ),
    Mutation(
        "FXT2-PRE",
        "a test that asks for the tool model states that it needs one",
        "tests/integration/test_agent_round.py",
        "    fixture, declaration, reason, tool_model\n",
        "    fixture, declaration, reason\n",
        "tests/integration/test_the_suite_states_its_preconditions.py",
    ),
    Mutation(
        "LIVE-1",
        "switching a live view off never drops the first answer to its question",
        "management/frontend/src/app/core/ui/live.ts",
        "    if (this.settledOnce) this.cancel();",
        "    this.cancel();",
        "management/frontend/src/app/core/ui/live.spec.ts"
        " management/frontend/src/app/features/use-cases/traces-tab.spec.ts",
    ),
    Mutation(
        "MONEY-1",
        "a configured limit is shown as it was set, never rounded to two places",
        "management/frontend/src/app/core/ui/money.ts",
        "Math.min(NANOS_PER_UNIT_DIGITS, Math.max(2, significant))",
        "Math.min(NANOS_PER_UNIT_DIGITS, 2)",
        "management/frontend/src/app/core/ui/money.spec.ts"
        " management/frontend/src/app/features/use-cases/budgets-tab.spec.ts",
    ),
    # ---- a stream left before its vendor reported usage is counted (FRD-128 FR-6) ----------
    Mutation(
        "COUNT-1",
        "a usage object with no count in it is no report",
        "gateway/src/aira_gateway/upstreams/gemini_mapping.py",
        "    return isinstance(meta, dict) and any(field in meta for field in USAGE_COUNT_FIELDS)",
        "    return isinstance(meta, dict) and bool(meta)",
        "gateway/tests/test_gemini_upstream_mapping.py",
    ),
    Mutation(
        "COUNT-2",
        "a stream left before its vendor reported usage is counted before the booking",
        "gateway/src/aira_gateway/api/serving/accounting.py",
        "        if state.unreported is not None and not state.produced:\n            await _count_what_was_left(request, state)",
        "        if False:\n            await _count_what_was_left(request, state)",
        "gateway/tests/test_cancelled_requests.py",
    ),
    Mutation(
        "COUNT-3",
        "counting switched off sends nothing to the vendor a second time",
        "gateway/src/aira_gateway/api/serving/accounting.py",
        "    if state.unreported is None or not settings_of(request).count_cancelled_streams:",
        "    if state.unreported is None:",
        "gateway/tests/test_cancelled_requests.py",
    ),
    Mutation(
        "COUNT-4",
        "a counted prompt long enough to be cached is priced as cached, never above the bill",
        "gateway/src/aira_gateway/api/serving/counting.py",
        '    return usage.model_copy(update={"cached_input_tokens": usage.prompt_tokens})',
        "    return usage",
        "gateway/tests/test_a_cancelled_stream_is_counted.py gateway/tests/test_cancelled_requests.py",
    ),
    Mutation(
        "COUNT-5",
        "a counted row leaves the cache split and the thinking unknown, never zero",
        "gateway/src/aira_gateway/persistence/service.py",
        "        counted = usage_basis == UsageBasis.COUNTED",
        "        counted = False",
        "gateway/tests/test_cancelled_requests.py",
    ),
    Mutation(
        "COUNT-6",
        "a usage reported before the end of the answer is marked as a floor",
        "gateway/src/aira_gateway/api/serving/streaming.py",
        "                complete=self.finish_reason is not None,",
        "                complete=True,",
        "gateway/tests/test_cancelled_requests.py",
    ),
    Mutation(
        "COUNT-7",
        "the answer is counted on its own, without the request's system text and tools",
        "gateway/src/aira_gateway/api/serving/counting.py",
        "        messages=[CanonicalMessage(role=Role.MODEL, parts=parts)],",
        "        messages=[*unreported.sent.messages, CanonicalMessage(role=Role.MODEL, parts=parts)],",
        "gateway/tests/test_a_cancelled_stream_is_counted.py gateway/tests/test_cancelled_requests.py",
    ),
    Mutation(
        "COUNT-8",
        "the request is counted where its answer came from",
        "gateway/src/aira_gateway/api/serving/counting.py",
        "            prompt = await provider.count_tokens(unreported.sent, region=unreported.region)",
        '            prompt = await provider.count_tokens(unreported.sent, region="")',
        "gateway/tests/test_a_cancelled_stream_is_counted.py",
    ),
    Mutation(
        "COUNT-9",
        "Vertex counts in the region that answered, not the model's first",
        "gateway/src/aira_gateway/upstreams/vertex/adapters.py",
        "((where, who) for where, who in targets if where == region), targets[0]",
        "((where, who) for where, who in targets if where != region), targets[0]",
        "gateway/tests/test_vertex.py",
    ),
    Mutation(
        "COUNT-10",
        "a count sends only what a generation is billed for as input",
        "gateway/src/aira_gateway/upstreams/gemini_mapping.py",
        '_COUNTED_FIELDS = ("contents", "systemInstruction", "tools")',
        '_COUNTED_FIELDS = ("contents", "systemInstruction", "tools", "generationConfig")',
        "gateway/tests/test_vertex.py",
    ),
    Mutation(
        "COUNT-11",
        "the report counts the requests booked at a floor",
        "gateway/src/aira_gateway/reporting/service.py",
        "        func.sum(case((RequestLog.usage_basis.is_not(None), 1), else_=0)).label(",
        "        func.sum(case((RequestLog.usage_basis.is_(None), 1), else_=0)).label(",
        "gateway/tests/test_reporting.py",
    ),
    Mutation(
        "COUNT-12",
        "the exported file says when its cost column is a floor",
        "gateway/src/aira_gateway/reporting/csv_export.py",
        "    if partial:\n",
        "    if not partial:\n",
        "gateway/tests/test_csv_export.py",
    ),
    Mutation(
        "COUNT-13",
        "the reporting screen says the spend is a lower bound when requests were booked at a floor",
        "management/frontend/src/app/features/reporting/reporting-page.html",
        "      @if (partialUsage() > 0) {",
        "      @if (partialUsage() < 0) {",
        "management/frontend/src/app/features/reporting/reporting-page.spec.ts",
    ),
    Mutation(
        "COUNT-14",
        "a notice this gateway puts before the answer is never counted as the vendor's output",
        "gateway/src/aira_gateway/api/serving/streaming.py",
        "        self._answer.append(chunk.text_delta)",
        "        self._answer.append(led)",
        "gateway/tests/test_a_cancelled_stream_is_counted.py",
    ),
    # ---- Agent Platform's managed open models, and a region permitted by name (FRD-115 FR-2a,
    # FRD-611) ----------------------------------------------------------------------------------
    Mutation(
        "OPEN-1",
        "a region permitted by name is permitted for that model alone",
        "gateway/src/aira_gateway/residency.py",
        "    if region in (for_models or {}).get(model, frozenset()):",
        "    if any(region in regions for regions in (for_models or {}).values()):",
        "gateway/tests/test_a_region_permitted_by_name.py gateway/tests/test_vertex_open_models.py",
    ),
    Mutation(
        "OPEN-2",
        "the transport measures a model against its own permission, not only the list",
        "gateway/src/aira_gateway/upstreams/vertex/transport.py",
        "        check_region(region, self._allowed, model=model, for_models=self._model_regions)",
        '        check_region(region, self._allowed, model="", for_models=self._model_regions)',
        "gateway/tests/test_vertex_open_models.py",
    ),
    Mutation(
        "OPEN-3",
        "the open-model endpoint is residency-checked like every other address",
        "gateway/src/aira_gateway/upstreams/vertex/transport.py",
        '        self._check(region, model)\n        return (\n            f"https://{host_for(region)}/v1/projects/{self._project}"\n            f"/locations/{region}/{OPEN_MODELS_PATH}"',  # noqa: E501
        '        return (\n            f"https://{host_for(region)}/v1/projects/{self._project}"\n            f"/locations/{region}/{OPEN_MODELS_PATH}"',  # noqa: E501
        "gateway/tests/test_vertex_open_models.py",
    ),
    Mutation(
        "OPEN-4",
        "a configured model's startup check honours its permission by name",
        "gateway/src/aira_gateway/upstreams/vertex/__init__.py",
        "        check_region(model.region, allowed, model=model.name, for_models=by_name)",
        "        check_region(model.region, allowed)",
        "gateway/tests/test_vertex_open_models.py",
    ),
    Mutation(
        "OPEN-5",
        "the dispatch condition honours a permission by name, as the transport does",
        "gateway/src/aira_gateway/requirements.py",
        "            check_region(described.region, self._allowed, model=model, for_models=self._for_models)",  # noqa: E501
        "            check_region(described.region, self._allowed)",
        "gateway/tests/test_a_region_permitted_by_name.py",
    ),
    Mutation(
        "OPEN-6",
        "a malformed permission stops the gateway rather than refusing the model",
        "gateway/src/aira_gateway/config.py",
        "        try:\n            parse_model_regions(value)\n        except ValueError as exc:",
        "        try:\n            pass\n        except ValueError as exc:",
        "gateway/tests/test_a_region_permitted_by_name.py",
    ),
    Mutation(
        "OPEN-7",
        "the console is told which regions single models are permitted",
        "gateway/src/aira_gateway/api/providers.py",
        '            "modelRegions": {model: sorted(regions) for model, regions in sorted(by_name.items())},',  # noqa: E501
        '            "modelRegions": {},',
        "gateway/tests/test_provider_offerings.py",
    ),
    Mutation(
        "OPEN-8",
        "the open-model body names the model with its publisher",
        "gateway/src/aira_gateway/upstreams/vertex/adapters.py",
        '    return {**body, "model": f"{publisher}/{model}"}',
        '    return {**body, "model": model}',
        "gateway/tests/test_vertex_open_models.py",
    ),
    Mutation(
        "OPEN-9",
        "thinking is switched with the key the model's publisher reads",
        "gateway/src/aira_gateway/upstreams/vertex/adapters.py",
        '            body["chat_template_kwargs"] = {self._thinking_switch: _thinks(request.thinking)}',  # noqa: E501
        '            body["chat_template_kwargs"] = {"enable_thinking": _thinks(request.thinking)}',
        "gateway/tests/test_vertex_open_models.py",
    ),
    Mutation(
        "OPEN-10",
        "thinking switched off is sent as off, never left to the model's default",
        "gateway/src/aira_gateway/upstreams/vertex/adapters.py",
        "    if setting.mode == ThinkingMode.DISABLED:\n        return False",
        "    if setting.mode == ThinkingMode.DISABLED:\n        return True",
        "gateway/tests/test_vertex_open_models.py",
    ),
    Mutation(
        "OPEN-11",
        "the open models' reasoning reaches only a use case that asked for it",
        "gateway/src/aira_gateway/upstreams/vertex/adapters.py",
        "                data, request.model, include_reasoning=request.include_reasoning",
        "                data, request.model, include_reasoning=True",
        "gateway/tests/test_vertex_open_models.py",
    ),
    Mutation(
        "OPEN-12",
        "the probe reads an empty conversation refused as the model being there",
        "gateway/src/aira_gateway/upstreams/vertex/adapters.py",
        "            if exc.status_code != 400:\n                raise",
        "            raise",
        "gateway/tests/test_vertex_open_models.py",
    ),
    Mutation(
        "OPEN-13",
        "a configured Vertex model from a publisher nothing speaks stops the start",
        "gateway/src/aira_gateway/upstreams/vertex/__init__.py",
        "        if model.publisher not in spoken:",
        "        if False:",
        "gateway/tests/test_vertex_open_models.py",
    ),
    Mutation(
        "OPEN-14",
        "the open-model dialect declares a switch, not a budget or a level",
        "gateway/src/aira_gateway/upstreams/vertex/adapters.py",
        "    thinking_modes = frozenset({ThinkingMode.DISABLED, ThinkingMode.AUTO})",
        "    thinking_modes = frozenset(ThinkingMode)",
        "gateway/tests/test_vertex_open_models.py gateway/tests/test_no_silent_drop.py",
    ),
    Mutation(
        "OPEN-15",
        "a usage block with no count in it is no report, so a left stream is not booked at zero",
        "gateway/src/aira_gateway/upstreams/openai/mapping/response.py",
        '    usage = _usage_of(data["usage"]) if reports_usage(data.get("usage")) else None',
        '    usage = _usage_of(data["usage"]) if data.get("usage") else None',
        f"{OPENAI_DIALECT} gateway/tests/test_vertex_open_models.py",
    ),
    Mutation(
        "OPEN-16",
        "the dialect reads its second spelling of the reasoning field",
        "gateway/src/aira_gateway/upstreams/openai/mapping/response.py",
        'REASONING_FIELDS = ("reasoning", "reasoning_content")',
        'REASONING_FIELDS = ("reasoning",)',
        "gateway/tests/test_reasoning_reaches_every_dialect.py gateway/tests/test_vertex_open_models.py",  # noqa: E501
    ),
    Mutation(
        "OPEN-17",
        "a stream's tool calls are emitted whole on the chunk that ends the message",
        "gateway/src/aira_gateway/upstreams/openai/mapping/response.py",
        "        if chunk.finish_reason is not None and self._calls.pending:",
        "        if False:",
        f"{OPENAI_DIALECT} gateway/tests/test_vertex_open_models.py",
    ),
    Mutation(
        "OPEN-18",
        "a refusal wrapped in a list still says why",
        "gateway/src/aira_gateway/upstreams/base.py",
        "        if isinstance(body, list) and body:\n            body = body[0]",
        "        if False:\n            body = body[0]",
        "gateway/tests/test_vertex_open_models.py",
    ),
    Mutation(
        "OPEN-19",
        "the editor permits a model the regions the gateway permits it by name",
        "management/frontend/src/app/features/models/model-editor.ts",
        "    const byName = this.gateway.modelRegions()[this.name().trim()] ?? [];",
        "    const byName: string[] = [];",
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "OPEN-20",
        "and only that model",
        "management/frontend/src/app/features/models/model-editor.ts",
        "    const byName = this.gateway.modelRegions()[this.name().trim()] ?? [];",
        "    const byName = Object.values(this.gateway.modelRegions()).flat();",
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    Mutation(
        "OPEN-21",
        "the console reads the permissions the gateway publishes",
        "management/frontend/src/app/core/api/clients/catalog.ts",
        "          modelRegions: body.modelRegions ?? {},",
        "          modelRegions: {},",
        "management/frontend/src/app/core/api/use-case.service.spec.ts",
    ),
    Mutation(
        "OPEN-22",
        "the add-model window says a catalogue entry is enough where the gateway says so",
        "management/frontend/src/app/features/models/model-catalog.html",
        "            @if (upstream.cataloguedIsEnough) {\n              and cataloguing the model is enough",
        "            @if (false) {\n              and cataloguing the model is enough",
        "management/frontend/src/app/features/models/model-catalog.spec.ts",
    ),
    # ---- Windows sign-on through Keycloak (FRD-140) ------------------------------------------
    #
    # The configuration the browser suite stands on (`make test-windows-sso`), guarded where every
    # developer runs it. That suite proves the sign-on itself, against a real domain, and is not
    # run from here: it needs the stand-in domain and a browser.
    Mutation(
        "WSSO-1",
        "the realm reads the directory and never writes to it",
        "tools/windows_sso.py",
        '            "editMode": ["READ_ONLY"],',
        '            "editMode": ["WRITABLE"],',
        "tools/tests/test_windows_sso.py",
    ),
    Mutation(
        "WSSO-2",
        "nobody registered in the realm is created in AD",
        "tools/windows_sso.py",
        '            "syncRegistrations": ["false"],',
        '            "syncRegistrations": ["true"],',
        "tools/tests/test_windows_sso.py",
    ),
    Mutation(
        "WSSO-3",
        "Kerberos is allowed on the federation",
        "tools/windows_sso.py",
        '            "allowKerberosAuthentication": ["true"],',
        '            "allowKerberosAuthentication": ["false"],',
        "tools/tests/test_windows_sso.py",
    ),
    Mutation(
        "WSSO-4",
        "a ticket is matched by account name, not by a UPN in the mail domain",
        "tools/windows_sso.py",
        '            "krbPrincipalAttribute": [],',
        '            "krbPrincipalAttribute": ["userPrincipalName"],',
        "tools/tests/test_windows_sso.py",
    ),
    Mutation(
        "WSSO-5",
        "a change in AD reaches the realm within a bound",
        "tools/windows_sso.py",
        '            "cachePolicy": ["MAX_LIFESPAN"],',
        '            "cachePolicy": ["DEFAULT"],',
        "tools/tests/test_windows_sso.py",
    ),
    Mutation(
        "WSSO-6",
        "the bound is given to Keycloak in its own unit",
        "tools/windows_sso.py",
        '            "maxLifespan": [str(cache_lifespan * 1000)],',
        '            "maxLifespan": [str(cache_lifespan)],',
        "tools/tests/test_windows_sso.py",
    ),
    Mutation(
        "WSSO-7",
        "only the people OU may sign on, not every account of the domain",
        "tools/windows_sso.py",
        '            "usersDn": [PEOPLE],',
        '            "usersDn": [BASE_DN],',
        "tools/tests/test_windows_sso.py",
    ),
    Mutation(
        "WSSO-8",
        "AD's disabled flag is read every time, so a disabled person stays out",
        "tools/windows_sso.py",
        '        "config": {"always.read.enabled.value.from.ldap": ["true"]},',
        '        "config": {"always.read.enabled.value.from.ldap": ["false"]},',
        "tools/tests/test_windows_sso.py",
    ),
    Mutation(
        "WSSO-9",
        "group membership is AD's alone",
        "tools/windows_sso.py",
        '            "mode": ["READ_ONLY"],',
        '            "mode": ["IMPORT"],',
        "tools/tests/test_windows_sso.py",
    ),
    Mutation(
        "WSSO-10",
        "role groups land where AIRA_ROLE_GROUPS looks for them",
        "tools/windows_sso.py",
        'GROUP_PATHS = {ROLES: "/aira", DEPARTMENTS: "/abteilungen"}',
        'GROUP_PATHS = {ROLES: "/ad", DEPARTMENTS: "/abteilungen"}',
        "tools/tests/test_windows_sso.py",
    ),
    Mutation(
        "WSSO-11",
        "Keycloak's key is AES only, never RC4",
        "tools/windows_sso.py",
        "AES_ONLY = 0x18",
        "AES_ONLY = 0x1C",
        "tools/tests/test_windows_sso.py",
    ),
    Mutation(
        "WSSO-12",
        "the service principal names the host of the issuer",
        "tools/windows_sso.py",
        "    host = urllib.parse.urlsplit(stack_addresses.issuer()).hostname",
        '    host = "localhost"',
        "tools/tests/test_windows_sso.py",
    ),
    Mutation(
        "WSSO-13",
        "a workstation asks for the host exactly as typed",
        "tools/windows_sso.py",
        '    qualify_shortname = ""\n',
        "",
        "tools/tests/test_windows_sso.py",
    ),
    Mutation(
        "WSSO-14",
        "a realm that is somebody's directory is left alone",
        "tools/windows_sso.py",
        "    if environment not in DEMO_ENVIRONMENTS:",
        "    if False:",
        "tools/tests/test_windows_sso.py",
    ),
    Mutation(
        "WSSO-15",
        "the keytab is handed to Keycloak readable by Keycloak alone",
        "tools/windows_sso.py",
        '        \'umask 077 && cat > "$1.new" && mv "$1.new" "$1"\',',
        '        \'cat > "$1.new" && mv "$1.new" "$1"\',',
        "tools/tests/test_windows_sso.py",
    ),
    # == the settings-combinations findings: a use case the gateway does not hold ==============
    Mutation(
        "GS-1",
        "a use case the gateway holds no record of is refused, not served with permissive defaults",
        "gateway/src/aira_gateway/api/serving/controls.py",
        "    if record is None:\n        # 403 like retirement",
        "    if record is None:\n        return\n    if record is None:\n        # 403 like retirement",
        "gateway/tests/test_an_unknown_use_case_serves_nothing.py "
        "gateway/tests/test_store_payloads.py gateway/tests/test_tool_calling.py",
    ),
    Mutation(
        "GS-2",
        "the content of a use case the gateway has not heard of is not stored",
        "gateway/src/aira_gateway/persistence/writer.py",
        "        return False if record is None else bool(record.store_payloads)",
        "        return True if record is None else bool(record.store_payloads)",
        "gateway/tests/test_store_payloads.py",
    ),
    Mutation(
        "GS-3",
        "a dry run meets the rule a request meets: an unknown use case dry-runs nothing",
        "gateway/src/aira_gateway/api/pipeline.py",
        "        await refuse_unless_live(request, payload.use_case)\n",
        "        pass\n",
        "gateway/tests/test_an_unknown_use_case_serves_nothing.py",
    ),
    Mutation(
        "GS-4",
        "a use case's description fits the gateway column whatever length Management accepted",
        "gateway/src/aira_gateway/db/models/read_models.py",
        '    description: Mapped[str] = mapped_column(Text, default="")',
        '    description: Mapped[str] = mapped_column(String(2000), default="")',
        "tools/tests/test_what_management_accepts_fits_the_gateway.py",
    ),
    # == the settings-combinations findings: each candidate of a chain is fitted on its own ======
    Mutation(
        "GS-5",
        "each candidate of a chain is sent the caller's request with its own defaults",
        "gateway/src/aira_gateway/pipeline/dispatch.py",
        "        if fit is not None:\n            fitted = await fit(addressed)",
        "        if False:\n            fitted = await fit(addressed)",
        "gateway/tests/test_every_candidate_is_sent_its_own_defaults.py",
    ),
    Mutation(
        "GS-6",
        "a thinking budget that does not fit a fallback's own cap passes the fallback over",
        "gateway/src/aira_gateway/api/serving/prepare.py",
        "            check_budget_fits(candidate.thinking, candidate.max_output_tokens)\n",
        "            pass\n",
        "gateway/tests/test_every_candidate_is_sent_its_own_defaults.py",
    ),
    Mutation(
        "GS-7",
        "a cap the caller set is a condition on every candidate, not only on the routed model",
        "gateway/src/aira_gateway/api/serving/prepare.py",
        "    if canonical is not None and canonical.max_output_tokens is not None:\n"
        "        checks.append(OutputCapAccepted(",
        "    if False:\n        checks.append(OutputCapAccepted(",
        "gateway/tests/test_every_candidate_is_sent_its_own_defaults.py",
    ),
    Mutation(
        "GS-8",
        "a Gemini chain is conditioned on what the caller asked, not on the primary's defaults",
        "gateway/src/aira_gateway/api/gemini/routes.py",
        "            permits=await requirements_for(request, asked),",
        "            permits=await requirements_for(request, canonical),",
        "gateway/tests/test_every_candidate_is_sent_its_own_defaults.py",
    ),
    Mutation(
        "GS-9",
        "a KIRA chain is conditioned on what the caller asked, not on the primary's defaults",
        "gateway/src/aira_gateway/api/kira/routes.py",
        "                permits=await requirements_for(request, asked),",
        "                permits=await requirements_for(request, canonical),",
        "gateway/tests/test_every_candidate_is_sent_its_own_defaults.py",
    ),
    Mutation(
        "GS-10",
        "every chain a surface opens is handed the fit, the KIRA stream's included",
        "gateway/src/aira_gateway/api/kira/routes.py",
        "            fit=fitting(request),\n        )\n    except KIRA_REFUSALS as exc:",
        "        )\n    except KIRA_REFUSALS as exc:",
        "gateway/tests/test_surface_layering.py "
        "gateway/tests/test_every_candidate_is_sent_its_own_defaults.py",
    ),
    # == the settings-combinations findings: a step calls only what the request may reach =======
    Mutation(
        "GS-11",
        "a pipeline step asks the request's conditions of a model before calling it",
        "gateway/src/aira_gateway/pipeline/engine/steps.py",
        "            refusal = await permits(model) if permits is not None else None\n",
        "            refusal = None\n",
        "gateway/tests/test_a_step_calls_only_what_the_request_may_reach.py",
    ),
    Mutation(
        "GS-12",
        "a step that may not call its model says why, rather than reading as a typo",
        "gateway/src/aira_gateway/pipeline/engine/steps.py",
        '                withheld.append(f"{model}: {refusal}")\n',
        "                pass\n",
        "gateway/tests/test_a_step_calls_only_what_the_request_may_reach.py",
    ),
    Mutation(
        "GS-13",
        "the request path hands the installation's conditions to the pipeline",
        "gateway/src/aira_gateway/api/serving/pipeline_run.py",
        "            # And only one this request may reach: release, approval, residency, price.\n"
        "            permits=permits(await installation_requirements(request)),\n",
        "",
        "gateway/tests/test_a_step_calls_only_what_the_request_may_reach.py",
    ),
    Mutation(
        "GS-14",
        "the embedding path hands the installation's conditions to its redactor too",
        "gateway/src/aira_gateway/api/serving/pipeline_run.py",
        "            declaration_of=await declared_model(request),\n"
        "            permits=permits(await installation_requirements(request)),\n",
        "            declaration_of=await declared_model(request),\n",
        "gateway/tests/test_a_step_calls_only_what_the_request_may_reach.py",
    ),
    # == the settings-combinations findings: a lifted budget still counts ======================
    Mutation(
        "GS-15",
        "a lifted budget is found, so that it can be counted",
        "gateway/src/aira_gateway/budgets/store.py",
        '        select(BudgetRead).where(BudgetRead.use_case == (use_case or ""))',
        "        select(BudgetRead).where(\n"
        '            BudgetRead.use_case == (use_case or ""), BudgetRead.enabled.is_(True)\n'
        "        )",
        "gateway/tests/test_a_lifted_budget_still_counts.py gateway/tests/test_budget_service.py",
    ),
    Mutation(
        "GS-17",
        "a settled request is booked against its lifted budgets too",
        "gateway/src/aira_gateway/budgets/service.py",
        "            [*reservation.budgets, *reservation.lifted],",
        "            reservation.budgets,",
        "gateway/tests/test_a_lifted_budget_still_counts.py",
    ),
    Mutation(
        "GS-18",
        "a lifted budget's shared counter takes what was spent, so putting it back sees it",
        "gateway/src/aira_gateway/budgets/service.py",
        "        await self._add_to_counters(\n            reservation.lifted,",
        "        await self._add_to_counters(\n            [],",
        "gateway/tests/test_a_lifted_budget_still_counts.py",
    ),
    Mutation(
        "GS-19",
        "the early check does not refuse on a lifted budget",
        "gateway/src/aira_gateway/budgets/service.py",
        "            budgets = [b for b in await applicable(session, use_case, subject) if b.enabled]",
        "            budgets = await applicable(session, use_case, subject)",
        "gateway/tests/test_a_lifted_budget_still_counts.py",
    ),
    Mutation(
        "GS-20",
        "a lifted cost budget binds no model to a price",
        "gateway/src/aira_gateway/budgets/service.py",
        "budget.enabled and budget.limit_cost_nanos is not None for budget in budgets",
        "budget.limit_cost_nanos is not None for budget in budgets",
        "gateway/tests/test_a_lifted_budget_still_counts.py gateway/tests/test_an_unpriced_model_under_a_cost_limit.py",
    ),
    # == the settings-combinations findings: suspensions and their switches ===================
    Mutation(
        "GS-21",
        "a throttle holds with the configured rate limits switched off",
        "gateway/src/aira_gateway/ratelimit/service.py",
        "        configured = (\n"
        "            self._applicable(await self._config(use_case), use_case, subject)\n"
        "            if use_case and self._enforce",
        "        if not self._enforce:\n"
        "            return\n"
        "        configured = (\n"
        "            self._applicable(await self._config(use_case), use_case, subject)\n"
        "            if use_case and self._enforce",
        "gateway/tests/test_suspensions.py gateway/tests/test_ratelimit.py",
    ),
    Mutation(
        "GS-22",
        "a throttle's refusal is recorded as a suspension, not as a rate limit",
        "gateway/src/aira_gateway/api/serving/controls.py",
        "        raise throttle.refusal(exc.retry_after) from exc",
        "        raise",
        "gateway/tests/test_suspensions.py gateway/tests/test_throttle_reaches_the_limiter.py",
    ),
    Mutation(
        "GS-23",
        "of several blocks, the one that ends last says when to come back",
        "gateway/src/aira_gateway/anomalies/suspensions.py",
        "            row = max(blocks, key=lambda block: int(_retry_after(block)))",
        "            row = blocks[0]",
        "gateway/tests/test_suspensions.py",
    ),
    Mutation(
        "GS-24",
        "while suspensions are off, a rule records its finding and writes no decision",
        "gateway/src/aira_gateway/anomalies/service.py",
        "        if self.suspensions is None or not self.suspensions.enforcing or not rule.action_minutes:",
        "        if self.suspensions is None or not rule.action_minutes:",
        "gateway/tests/test_suspensions.py",
    ),
    # == the settings-combinations findings: what an anomaly rule measures =====================
    Mutation(
        "GS-25",
        "a pipeline step's own call is not counted among a rule's requests",
        "gateway/src/aira_gateway/anomalies/evaluator.py",
        "    if not spend:\n        stmt = stmt.where(",
        "    if False:\n        stmt = stmt.where(",
        "gateway/tests/test_anomaly_engine.py",
    ),
    Mutation(
        "GS-26",
        "a pipeline step's own call is counted as spend",
        "gateway/src/aira_gateway/anomalies/evaluator.py",
        "spend=kind is RuleKind.SPEND_SPIKE",
        "spend=False",
        "gateway/tests/test_anomaly_engine.py",
    ),
    Mutation(
        "GS-27",
        "a row with no caller is no rule's target",
        "gateway/src/aira_gateway/anomalies/evaluator.py",
        '    stmt = stmt.where(group.is_not(None), group != "", traffic)',
        "    stmt = stmt.where(group.is_not(None), traffic)",
        "gateway/tests/test_anomaly_engine.py",
    ),
    Mutation(
        "GS-28",
        "a rule naming a use case other than its route's is refused, not created global",
        "management/backend/src/aira_management/apps/anomalies/serializers.py",
        "        self._names_its_own_use_case()\n",
        "",
        "management/backend/tests/test_anomaly_rules.py",
    ),
    Mutation(
        "GS-29",
        "a block carries no rate",
        "gateway/src/aira_gateway/api/incidents/suspensions.py",
        "    if action == RuleAction.BLOCK.value and throttle_rpm is not None:",
        "    if False:",
        "gateway/tests/test_suspensions.py",
    ),
    Mutation(
        "GS-30",
        "a cursor naming a row id with a control character is refused, not handed to Postgres",
        "gateway/src/aira_gateway/api/reporting/common.py",
        "    if not row_id or printable(row_id) != row_id:",
        "    if not row_id:",
        "gateway/tests/test_suspensions.py",
    ),
    Mutation(
        "GS-31",
        "the suspension list refuses a use case outside the slug charset by name",
        "gateway/src/aira_gateway/api/incidents/suspensions.py",
        "    if use_case and not is_valid_use_case(use_case):\n"
        "        # The charset every caller-supplied slug meets",
        "    if False:\n        # The charset every caller-supplied slug meets",
        "gateway/tests/test_suspensions.py",
    ),
    Mutation(
        "GS-32",
        "the findings list refuses a use case outside the slug charset by name",
        "gateway/src/aira_gateway/api/reporting/anomalies.py",
        "    if use_case and not is_valid_use_case(use_case):",
        "    if False:",
        "gateway/tests/test_suspensions.py",
    ),
    # == the settings-combinations findings: the low ones ======================================
    Mutation(
        "GS-33",
        "a model check is no request in a report, and its spend still counts",
        "gateway/src/aira_gateway/reporting/service.py",
        "            func.sum(case((RequestLog.outcome == Outcome.DIAGNOSTIC.value, 0), else_=1)), 0",
        "            func.count(), 0",
        "gateway/tests/test_reporting.py",
    ),
    Mutation(
        "GS-34",
        "the model list leaves out a model the catalogue knows and has not approved",
        "gateway/src/aira_gateway/api/serving/context.py",
        "        if not await _withheld(catalog, registry, model.name)\n",
        "",
        "gateway/tests/test_the_model_list_says_what_is_served.py",
    ),
    Mutation(
        "GS-35",
        "a redactor's preamble is dropped even when its reply never echoed the closing marker",
        "gateway/src/aira_gateway/pipeline/classifiers/prompts.py",
        "        if start >= 0:\n",
        "        if start == 0:\n",
        "gateway/tests/test_pipeline_classifiers.py",
    ),
    Mutation(
        "GS-36",
        "an expiry leeway wider than the clock skew is honoured",
        "libs/src/aira_common/oidc.py",
        "                leeway=max(self._clock_skew, self._expiry_leeway),",
        "                leeway=self._clock_skew,",
        "libs/tests/test_oidc_clock_tolerance.py",
    ),
    Mutation(
        "GS-37",
        "a wider expiry leeway does not widen the clock skew for iat and nbf",
        "libs/src/aira_common/oidc.py",
        "            if ahead > self._clock_skew:",
        "            if False:",
        "libs/tests/test_oidc_clock_tolerance.py",
    ),
    Mutation(
        "GS-38",
        "the gateway refuses a token tolerance out of bounds at startup, OIDC on or off",
        "gateway/src/aira_gateway/config.py",
        '        return check_tolerance(value, f"AIRA_{str(info.field_name).upper()}")',
        "        return value",
        "gateway/tests/test_config.py",
    ),
    Mutation(
        "GS-39",
        "the console's backend refuses a token tolerance out of bounds at startup, OIDC on or off",
        "management/backend/src/aira_management/config/app_settings.py",
        '        return check_tolerance(value, f"AIRA_{str(info.field_name).upper()}")',
        "        return value",
        "management/backend/tests/test_a_token_tolerance_is_bounded_at_startup.py",
    ),
    Mutation(
        "GS-40",
        "a response schema is measured as written, compact and in UTF-8",
        "gateway/src/aira_gateway/core/schema.py",
        'json.dumps(raw, separators=(",", ":"), ensure_ascii=False)',
        "json.dumps(raw)",
        "gateway/tests/test_response_schema.py",
    ),
    Mutation(
        "GS-41",
        "a broker named by a host nothing could dial refuses to start",
        "libs/src/aira_common/kafka.py",
        '    if not entry.startswith("[") and not _HOST_NAME.match(host):',
        "    if False:",
        "gateway/tests/test_config.py tools/tests/test_both_planes_read_one_broker_list.py",
    ),
    Mutation(
        "GS-42",
        "a bracketed broker must be an IPv6 address",
        "libs/src/aira_common/kafka.py",
        "            ipaddress.IPv6Address(host)\n",
        "            pass\n",
        "gateway/tests/test_config.py tools/tests/test_both_planes_read_one_broker_list.py",
    ),
    Mutation(
        "GS-43",
        "with no proxy in front, the forwarded-for header is the caller's and is ignored",
        "gateway/src/aira_gateway/persistence/recorder.py",
        "            if 0 < hops <= len(chain):",
        "            if max(1, hops) <= len(chain):\n                hops = max(1, hops)",
        "gateway/tests/test_persistence_recorder.py",
    ),
    Mutation(
        "DA-1",
        "an exhausted chain's error carries whom it tried and skipped",
        "gateway/src/aira_gateway/pipeline/dispatch.py",
        "    setattr(error, _PASSED_OVER, PassedOver(tuple(skipped), tuple(failed)))",
        "    pass",
        "gateway/tests/test_an_exhausted_chain_says_what_it_tried.py",
    ),
    Mutation(
        "DA-2",
        "the row of a refused request records what the exhausted chain passed over",
        "gateway/src/aira_gateway/api/serving/accounting.py",
        "        trail.passed_over(list(chain.skipped), list(chain.failed))",
        "        pass",
        "gateway/tests/test_an_exhausted_chain_says_what_it_tried.py",
    ),
    Mutation(
        "DA-3",
        "a caller's sampled flag does not decide whether its request leaves a span",
        "libs/src/aira_common/observability/providers.py",
        "        root=by_ratio, remote_parent_sampled=by_ratio, remote_parent_not_sampled=by_ratio",
        "        root=by_ratio",
        "libs/tests/test_a_caller_cannot_switch_its_span_off.py "
        "gateway/tests/test_a_refused_access_is_on_its_span.py",
    ),
    Mutation(
        "DA-4",
        "a Kafka outage degrades the gateway and does not take it out of rotation",
        "gateway/src/aira_gateway/routes/health.py",
        "        kafka_ok = kafka.ok\n",
        "        kafka_ok = kafka.ok\n        ready = ready and kafka.ok\n",
        "gateway/tests/test_health.py",
    ),
    Mutation(
        "DA-5",
        "the gateway counts the Kafka cluster reachable while any broker is",
        "gateway/src/aira_gateway/routes/health.py",
        '        kafka = any_reachable("kafka", members)',
        "        kafka = members[0]",
        "gateway/tests/test_health.py",
    ),
    Mutation(
        "DA-6",
        "a Kafka outage degrades Management and does not take it out of rotation",
        "management/backend/src/aira_management/apps/health/views.py",
        '            "status": "ready" if database.ok else "not_ready",',
        '            "status": "ready" if database.ok and (kafka is None or kafka.ok) else "not_ready",',
        "management/backend/tests/test_health.py",
    ),
    Mutation(
        "DA-7",
        "a cluster with some members answering is reachable",
        "libs/src/aira_common/health.py",
        "    if answered == 0:",
        "    if answered < len(members):",
        "management/backend/tests/test_health.py gateway/tests/test_health.py",
    ),
    Mutation(
        "DA-8",
        "a Foundry deployment that names no region refuses to start",
        "gateway/src/aira_gateway/upstreams/foundry/__init__.py",
        "        if not fields[0] or not region:",
        "        if not fields[0]:",
        "gateway/tests/test_foundry.py",
    ),
    Mutation(
        "DA-9",
        "a known caller the gateway cannot attribute to a use case is marked on its span",
        "gateway/src/aira_gateway/auth/dependencies.py",
        "    mark_refused(\n        UNATTRIBUTED,",
        "    return\n    mark_refused(\n        UNATTRIBUTED,",
        "gateway/tests/test_a_refused_access_is_on_its_span.py",
    ),
    Mutation(
        "DA-10",
        "the KIRA surface marks a request it cannot attribute on its span",
        "gateway/src/aira_gateway/api/kira/attribution.py",
        "    mark_refused(\n        UNATTRIBUTED,",
        "    return\n    mark_refused(\n        UNATTRIBUTED,",
        "gateway/tests/test_a_refused_access_is_on_its_span.py",
    ),
    Mutation(
        "DA-11",
        "an address asked to wait after failed sign-ins is marked on its span",
        "gateway/src/aira_gateway/auth/attempts.py",
        "    mark_refused(UNAUTHENTICATED, 429)\n",
        "",
        "gateway/tests/test_a_refused_access_is_on_its_span.py",
    ),
    Mutation(
        "DA-12",
        "the default retention is a real period, as the sweep and the privacy notice both read it",
        "libs/src/aira_common/config.py",
        "    default_retention_days: int = Field(default=7, ge=1, le=3650)",
        "    default_retention_days: int = 7",
        "libs/tests/test_config.py",
    ),
    Mutation(
        "DA-13",
        "the gateway refuses an in-memory database outside local",
        "gateway/src/aira_gateway/security.py",
        "    if settings.test_database:",
        "    if False:",
        "gateway/tests/test_deployment_safety.py",
    ),
    Mutation(
        "DA-14",
        "the gateway refuses a negative proxy count, as Management does",
        "gateway/src/aira_gateway/config.py",
        "    trusted_proxy_hops: int = Field(default=1, ge=0)",
        "    trusted_proxy_hops: int = 1",
        "gateway/tests/test_config.py",
    ),
    Mutation(
        "SD-3",
        "a request row withheld in strict data mode names nobody",
        "management/frontend/src/app/features/use-cases/traces-tab.html",
        "                  @if (row.person_withheld) {\n                    <!-- Strict data mode:",
        "                  @if (false) {\n                    <!-- Strict data mode:",
        "management/frontend/src/app/features/use-cases/traces-tab.spec.ts",
    ),
    Mutation(
        "SD-4",
        "a withheld request row carries no via-key badge, whatever arrived with it",
        "management/frontend/src/app/features/use-cases/traces-tab.html",
        "                  @if (row.credential && !row.person_withheld) {",
        "                  @if (row.credential) {",
        "management/frontend/src/app/features/use-cases/traces-tab.spec.ts",
    ),
    Mutation(
        "SD-5",
        "the key filter is not offered to a reader strict data mode would refuse it",
        "management/frontend/src/app/features/use-cases/traces-tab.ts",
        "    return me !== null && !peopleWithheld(me);\n",
        "    return me !== null;\n",
        "management/frontend/src/app/features/use-cases/traces-tab.spec.ts",
    ),
    Mutation(
        "SD-6",
        "strict data mode offers only not-stored and oversight content modes",
        "management/frontend/src/app/features/use-cases/data-protection-panel.ts",
        "    this.strictData()\n      ? CONTENT_MODES.filter(",
        "    false\n      ? CONTENT_MODES.filter(",
        "management/frontend/src/app/features/use-cases/data-protection-panel.spec.ts",
    ),
    Mutation(
        "SD-7",
        "the role editor does not offer what strict data mode keeps for the oversight roles",
        "management/frontend/src/app/features/platform/role-editor.ts",
        "    if (permission.strict_data_reserved !== true) return null;",
        "    return null;",
        "management/frontend/src/app/features/platform/roles-page.spec.ts",
    ),
    Mutation(
        "SD-8",
        "a withheld finding or stop is worded as a person or a key, never shown blank",
        "management/frontend/src/app/core/ui/withheld.ts",
        "  if (row.target_withheld) return row.target === 'credential' ? 'a key' : 'a person';\n",
        "",
        "management/frontend/src/app/features/use-cases/warnings-tab.spec.ts management/frontend/src/app/features/security/security-page.spec.ts",
    ),
    Mutation(
        "SD-9",
        "a breakdown whose rows carry no latency has no latency column",
        "management/frontend/src/app/features/reporting/breakdown-table.ts",
        "    this.rows().some((row) => row.avg_latency_ms !== undefined),",
        "    true,",
        "management/frontend/src/app/features/reporting/breakdown-table.spec.ts",
    ),
]


def _digest(text: str) -> str:
    """A fingerprint of a file's text, so the journal can recognise the mutant without holding it."""
    return hashlib.sha256(text.encode()).hexdigest()


def _write_journal(entry: dict[str, str]) -> None:
    """Record what is about to be changed, **atomically**.

    Written beside the journal and renamed over it: a run killed while writing would otherwise
    leave half a document, and the next run could neither read the original back nor tell whether
    the file on disk is the mutant.
    """
    temporary = JOURNAL.with_name(f"{JOURNAL.name}.tmp")
    temporary.write_text(json.dumps(entry))
    os.replace(temporary, JOURNAL)


def _restore(path: pathlib.Path, original: str, mutated: str) -> bool:
    """Put `original` back **only if `path` still holds the mutant**; True if it is back.

    A file holding neither was edited while the mutant was on disk — a fix made after a crash, an
    editor saving into a running harness — and writing the original over it would throw that edit
    away without a word.
    """
    current = path.read_text()
    if current == mutated:
        path.write_text(original)
        return True
    return current == original


def _recover() -> None:
    """Put back what a previous run was holding when it died — if the file still holds it.

    Runs before anything else reads the source: a mutant left on disk can make another anchor match
    twice, and the anchor check would then refuse to start and leave the mutant where it is.
    """
    if not JOURNAL.exists():
        return
    try:
        entry = json.loads(JOURNAL.read_text())
        path = ROOT / entry["path"]
        original = entry["original"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"{JOURNAL} cannot be read ({error}). Check the file it names, then delete it.")
        raise SystemExit(2) from None
    current = path.read_text() if path.exists() else None
    if current == original:
        JOURNAL.unlink()
        return
    if current is not None and _digest(current) == entry.get("mutated_sha256"):
        path.write_text(original)
        JOURNAL.unlink()
        print(f"Recovered {entry['path']} from an interrupted run.", flush=True)
        return
    print(
        f"{entry['path']} holds neither its original text nor the mutant "
        f"{entry.get('ident', '?')} that an interrupted run left in it, so it has been edited "
        f"since and is left as it is. The original is in {JOURNAL}: keep what is right, then "
        "delete the journal."
    )
    raise SystemExit(2)


class Outcome(enum.Enum):
    """What one run of one test selection says."""

    #: Every test passed.
    PASSED = "passed"
    #: A test failed — for a mutant, the property was defended.
    FAILED = "failed"
    #: Stopped at the time limit. For a mutant, noticed, but only by the clock.
    TIMED_OUT = "timed out"
    #: The run never reached the tests: a build error, a usage error, nothing collected. **Not** a
    #: catch — a mutant that does not build proves nothing about the tests.
    BROKEN = "broken"


def _commands(selection: str) -> list[tuple[list[str], pathlib.Path]]:
    """The runs one selection takes, with their working directories.

    A console spec (`*.spec.ts`) runs under `ng test`, which is the only runner that builds the
    Angular application the way its tests expect; everything else is a pytest path. A selection may
    hold both, and then both must pass for it to be green.
    """
    paths = selection.split()
    python = [path for path in paths if not path.endswith(SPEC_SUFFIX)]
    specs = [path for path in paths if path.endswith(SPEC_SUFFIX)]
    commands: list[tuple[list[str], pathlib.Path]] = []
    if python:
        commands.append(([sys.executable, "-m", "pytest", *python, "-x", "-q", "--no-cov"], ROOT))
    if specs:
        included: list[str] = []
        for spec in specs:
            included += ["--include", str((ROOT / spec).relative_to(FRONTEND))]
        commands.append(
            (["npx", "ng", "test", "--watch=false", "--no-coverage", *included], FRONTEND)
        )
    return commands


def _execute(command: list[str], cwd: pathlib.Path, timeout: float) -> tuple[int | None, str]:
    """`(exit code, output)`, or `(None, output)` when the run outlived `timeout` seconds.

    In a session of its own, so the time limit stops the whole tree — `ng test`'s builder and
    browser, a test's own subprocesses — and not only the process at its top.
    """
    try:
        process = subprocess.Popen(  # noqa: S603 — fixed argument lists, no shell
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    except OSError as error:
        return 127, f"{command[0]} could not be started: {error}"
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _stop(process)
        output, _ = process.communicate()
        return None, output or ""
    except BaseException:
        # Interrupted or terminated: the run's own processes must not outlive the harness.
        _stop(process)
        raise
    return process.returncode, output or ""


def _stop(process: subprocess.Popen[str]) -> None:
    """Kill `process` and everything it started, then reap it."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()


def _classify(command: list[str], code: int | None, output: str) -> tuple[Outcome, str]:
    """What a finished run means. The detail says why, for anything but a plain pass or fail."""
    if code is None:
        return Outcome.TIMED_OUT, f"stopped after the time limit: {' '.join(command[:4])} …"
    if command[:3] == ["npx", "ng", "test"]:
        text = _ANSI.sub("", output)
        if NG_BUILD_FAILED in text:
            errors = [line.strip() for line in text.splitlines() if "ERROR" in line][:3]
            return Outcome.BROKEN, "the console did not build: " + " | ".join(errors)
        if not NG_TESTS_RAN.search(text):
            return Outcome.BROKEN, f"ng test exited {code} without running a test file"
        return (Outcome.PASSED if code == 0 else Outcome.FAILED), ""
    if code == 0:
        return Outcome.PASSED, ""
    if code in PYTEST_FAILED:
        return Outcome.FAILED, ""
    tail = " | ".join(output.strip().splitlines()[-3:])
    return Outcome.BROKEN, f"pytest exited {code}: {tail}"


def _run(selection: str, timeout: float) -> tuple[Outcome, str]:
    """Run every part of `selection` and say what they say together.

    A part that could not run outranks one that failed, because nothing can be read from a
    selection that was only partly run; a failure outranks a timeout, which outranks a pass.
    """
    results = [
        _classify(command, *_execute(command, cwd, timeout))
        for command, cwd in _commands(selection)
    ]
    for outcome in (Outcome.BROKEN, Outcome.FAILED, Outcome.TIMED_OUT):
        details = [detail for found, detail in results if found is outcome]
        if details:
            return outcome, "; ".join(detail for detail in details if detail)
    return Outcome.PASSED, ""


def _unparsable(path: pathlib.Path, text: str) -> str | None:
    """Why the mutant cannot even be loaded, or `None`.

    A Python file that does not compile fails every test that imports it, whatever those tests
    check — a `caught` that would be reported for a property nobody defends. Console sources are
    checked by the build itself, which `_classify` reads.
    """
    if path.suffix == ".py":
        try:
            compile(text, str(path), "exec", dont_inherit=True)
        except SyntaxError as error:
            return f"{error.msg} (line {error.lineno})"
    elif path.suffix == ".json":
        try:
            json.loads(text)
        except ValueError as error:
            return str(error)
    return None


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


def _terminated(signum: int, _frame: object) -> None:
    """A `SIGTERM` — a cancelled CI job, a closed terminal — unwinds like an interrupt, so the
    mutant on disk is restored by the run that put it there rather than by the next one."""
    raise SystemExit(128 + signum)


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    """The command line. `--only A1,B2`, `--only=A1,B2` and a repeated `--only` all select."""
    parser = argparse.ArgumentParser(
        prog="mutation_check.py",
        description="Break each guarded property on purpose and check that the suite notices.",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="ID[,ID...]",
        help="run only these mutations; comma-separated, and the option may be repeated",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=TIMEOUT_SECONDS,
        help=f"seconds one run of one test selection may take (default: {TIMEOUT_SECONDS:g})",
    )
    arguments, extra = parser.parse_known_args(argv)
    for argument in extra:
        if argument.startswith("-"):
            parser.error(f"unknown option {argument!r}")
        # **A bare id is not a selection**, and silence about that is the expensive half: running
        # the whole set when somebody asked for nine properties looks like a subset that passes,
        # hours later.
        parser.error(f"{argument!r} is not an option. Select a subset with --only={argument}.")
    named = [ident.strip() for value in arguments.only for ident in value.split(",")]
    if arguments.only and not any(named):
        parser.error("--only names no mutation")
    arguments.only = {ident for ident in named if ident} or None
    return arguments


def main(argv: list[str] | None = None) -> int:
    arguments = _arguments(argv)
    previous = signal.signal(signal.SIGTERM, _terminated)
    try:
        return _check(arguments)
    finally:
        signal.signal(signal.SIGTERM, previous)


def _check(arguments: argparse.Namespace) -> int:
    # **Recovery before every check that reads the source**, for the reason `_recover` gives.
    _recover()
    _refuse_duplicate_ids()
    _refuse_ambiguous_anchors()
    # `--only A1,B2` runs a subset: the whole set takes hours, and somebody iterating on one
    # property needs one. The baseline check narrows with it, or the subset run pays for suites it
    # never touches.
    wanted: set[str] | None = arguments.only
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
        outcome, detail = _run(selection, arguments.timeout)
        if outcome is not Outcome.PASSED:
            said = f": {detail}" if detail else ""
            print(f"BASELINE {outcome.value.upper()} for '{selection}'{said}.")
            print("Fix the suite first — with a baseline that is not green every mutation looks")
            print("'caught' and this tool tells you nothing.")
            return 2

    survivors: list[Mutation] = []
    broken: list[tuple[Mutation, str]] = []
    slow: list[Mutation] = []

    for mutation in chosen:
        path = ROOT / mutation.path
        original = path.read_text()
        if mutation.old not in original:
            print(f"{mutation.ident:<4} STALE     anchor gone from {mutation.path}", flush=True)
            survivors.append(mutation)
            continue
        mutated = original.replace(mutation.old, mutation.new, 1)
        reason = _unparsable(path, mutated)
        if reason is not None:
            outcome, detail = Outcome.BROKEN, f"the mutant does not parse: {reason}"
        else:
            _write_journal(
                {
                    "ident": mutation.ident,
                    "path": mutation.path,
                    "original": original,
                    "mutated_sha256": _digest(mutated),
                }
            )
            try:
                path.write_text(mutated)
                outcome, detail = _run(mutation.tests, arguments.timeout)
            finally:
                restored = _restore(path, original, mutated)
                if restored:
                    JOURNAL.unlink(missing_ok=True)
            if not restored:
                print(
                    f"{mutation.path} changed while {mutation.ident} was applied, so it is left as "
                    f"it is and nothing more is run. Its original is in {JOURNAL}."
                )
                return 2

        if outcome is Outcome.BROKEN:
            status = "BROKEN"
            broken.append((mutation, detail))
        elif outcome is Outcome.PASSED:
            status = "SURVIVED"
            survivors.append(mutation)
        elif outcome is Outcome.TIMED_OUT:
            status = "TIMEOUT"
            slow.append(mutation)
        else:
            status = "caught"
        print(f"{mutation.ident:<4} {status:<9} {mutation.property_defended}", flush=True)

    print()
    if slow:
        print(f"{len(slow)} caught only by the time limit — noticed, but by a test that hangs:")
        for mutation in slow:
            print(f"  {mutation.ident}  TIMEOUT   {mutation.property_defended}")
        print("A test that fails fast says *what* broke; a test that hangs says only that")
        print("something did.")
        print()
    if broken:
        print(f"{len(broken)} could not be checked, because the mutant never reached a test:")
        for mutation, detail in broken:
            print(f"  {mutation.ident}  BROKEN    {mutation.property_defended}\n      {detail}")
        print("A mutant that does not parse or build fails every test that loads it, whatever")
        print("the test checks. Rewrite the entry so that the mutant still runs and still breaks")
        print("the property it names.")
        print()
    if survivors:
        # A stale anchor and a surviving mutation are different problems with the same consequence,
        # and saying "no test would notice losing this" about a stale one sends the reader looking
        # for a missing test that is probably right there.
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
    if survivors or broken:
        return 1
    print(f"All {len(chosen)} properties are defended by at least one test.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
