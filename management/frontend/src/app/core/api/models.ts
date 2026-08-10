/**
 * One page of a list the server paged (`FRD-208`).
 *
 * `count` is the total across every page, not the length of `results` — a list that does not say
 * how much it is not showing reads as complete.
 */
export interface Page<T> {
  count: number;
  page: number;
  page_size: number;
  pages: number;
  results: T[];
}

export interface Me {
  subject: string;
  username: string;
  email: string;
  roles: string[];
  use_cases: string[];
  /** The installation's key policy, so a form states what the server enforces (`ADR-0015`). */
  api_key_default_days?: number;
  api_key_max_days?: number;
}

/**
 * What the signed-in caller may do inside one use case, as the server answers it.
 *
 * Not derivable from `/me`: these are object-level (django-guardian) permissions, so the console
 * has to be told. It used to guess, and it guessed generously — a use-case *user* was shown "Add
 * member" and got a 403 from the screen that had just invited the click.
 */
export interface UseCasePermissions {
  /** May rename or delete the use case itself. */
  can_admin: boolean;
  /** May change what happens inside it: members, keys, pipeline, budgets, limits. */
  can_manage: boolean;
  /** Actually belongs to it — which is what issuing an API key requires, and seeing it is not. */
  is_member: boolean;
}

export interface UseCase {
  permissions?: UseCasePermissions;
  slug: string;
  name: string;
  description: string;
  processing_notes: string;
  /** Whether prompts and responses are stored at all (FRD-404). */
  store_payloads?: boolean;
  /** Let this use case declare functions for the model to call (FRD-131). */
  tools_enabled?: boolean;
  /** Mark this use case's stable prefix as cacheable at the provider (FRD-133). */
  prompt_caching_enabled?: boolean;
  /** How long the provider keeps it: `5m` or `1h`. */
  prompt_cache_ttl?: string;
  /** Show each use-case *user* only their own requests. An administrator still sees all of them. */
  restrict_members_to_own_requests?: boolean;
  /** How long stored prompts and responses are kept, in days (FRD-404). */
  retention_days?: number;
  created_at?: string;
  updated_at?: string;
}

/** One thing a grant can name — a Keycloak group, or a person (`FRD-209`). */
export interface DirectoryEntry {
  kind: 'group' | 'user';
  /** What the grant stores: a group path, or a username. */
  id: string;
  label: string;
  detail: string;
}

export interface DirectoryResults {
  results: DirectoryEntry[];
  /**
   * Where the answer came from. `local` means Keycloak could not be asked and this is what
   * Management already knows — a real subset, never a guess, and the console says so because
   * "no results" from a degraded directory and "no such group" are different answers.
   */
  source: 'keycloak' | 'local' | 'none';
  hint?: string;
}

/** Access granted to a Keycloak group rather than to a person (`FRD-209`). */
export interface GroupGrant {
  group_path: string;
  role: 'admin' | 'user';
  granted_by: string;
  /**
   * How many people **Management has seen sign in** this grant currently reaches — not the
   * group's true size, which only the identity provider knows. It exists so a grant naming a
   * group that reaches nobody is visible rather than silently inert.
   */
  reaches: number;
  created_at?: string;
}

export interface Membership {
  username: string;
  role: string;
  created_at?: string;
}

export interface ApiKey {
  prefix: string;
  label: string;
  /** Who **answers for** the credential. The name every audit row carries — a row describes what
   *  called, not who authorised the credential months earlier (`FRD-604`). */
  owner: string;
  /** The human who created it, when that is not the owner. Empty for an ordinary key, where they
   *  are the same person; set for a team's shared credential, and then it is the only place the
   *  second fact exists. */
  issued_by?: string;
  is_active: boolean;
  created_at?: string;
  revoked_at?: string | null;
  /** When it stops working on its own. `null` means never — the default, and what the
   *  break-glass credential needs. Expiry and revocation are separate facts on purpose: "it
   *  lapsed as planned" and "we took it away" are not the same answer to an audit. */
  expires_at?: string | null;
}

/** Issue response — the only time the plaintext key is ever returned. */
export interface IssuedApiKey {
  api_key: string;
  prefix: string;
  label: string;
  use_case: string;
  expires_at?: string | null;
  owner?: string;
  issued_by?: string;
}

export type StepType = 'injection_filter' | 'allow_check' | 'model_route';

export interface RouteCategory {
  name: string;
  description?: string;
  model: string;
}

export interface StepConfig {
  // injection_filter
  mode?: 'heuristic' | 'llm';
  action?: 'block' | 'flag';
  scope?: 'user' | 'system_user';
  patterns?: string[];
  use_builtins?: boolean;
  instruction?: string;
  /**
   * What a *blocking* LLM filter does when its classifier reaches no verdict (FRD-125).
   * Defaults to refusing: a filter that serves unchecked requests stops protecting anything
   * while still showing as active in the builder.
   */
  on_undetermined?: 'block' | 'allow';
  // allow_check
  models?: string[];
  // model_route
  model?: string; // classifier model
  categories?: RouteCategory[];
  default_model?: string;
}

export interface PipelineStep {
  type: StepType;
  config: StepConfig;
}

export interface PipelineConfig {
  steps: PipelineStep[];
  fallback_models: string[];
  updated_at?: string;
}

export interface DryRunTraceEntry {
  type: string;
  action: string;
  detail: Record<string, unknown>;
}

export interface DryRunResult {
  blocked: boolean;
  block_reason: string | null;
  effective_model: string;
  fallback_models: string[];
  trace: DryRunTraceEntry[];
}

export interface Budget {
  id?: number;
  scope: 'use_case' | 'member';
  subject?: string;
  period: 'day' | 'month';
  /** Spend limit for the period, as an exact decimal string (never a JS number). */
  limit_cost?: string | null;
  limit_tokens?: number | null;
  limit_requests?: number | null;
  enabled?: boolean;
}

/** A request-rate limit (FRD-405). A budget says how much; this says how fast. */
export interface RateLimit {
  id?: number;
  scope: 'use_case' | 'member';
  subject?: string;
  /** Sustained requests per minute. */
  limit_rpm: number;
  /** How many may arrive at once; 0 means "use the per-minute figure". */
  burst?: number;
  enabled?: boolean;
}

export interface BudgetUsage {
  id: number;
  used_tokens: number;
  used_requests: number;
  /** Consumed spend in nano-units — integer, safe to divide for a progress bar. */
  used_cost_nanos: number;
  /** The same amount rounded for display. */
  used_cost: string;
  /** Requests served by a model with no price on file; their cost is unknown, not zero. */
  unpriced_requests: number;
}

/** What a model may be asked to do (FRD-114). Flags say *whether*, never *how*. */
// Mirrors `aira_common.models.Capability`. A second definition, and it drifted the first time the
// Python one grew: `tools` was added there (`FRD-131`) and the console could not name it. The
// mismatch surfaced as a compile error rather than as silence, which is the only reason it is a
// footnote — a value the console cannot express is a value it cannot show a reader.
/**
 * The closed capability vocabulary (`ADR-0012`), restated here because a checkbox list has to come
 * from somewhere. `libs/tests/test_capability_vocabulary.py` compares it against the Python enum
 * **in both directions** — it was missing `tools` and `prompt_caching` for two days and three
 * days respectively, and neither absence failed anywhere: a capability with no way in looks
 * exactly like a design decision.
 */
export type Capability =
  | 'generate'
  | 'embed'
  | 'structured_output'
  | 'thinking'
  | 'attachments'
  | 'tools'
  | 'prompt_caching';

export const CAPABILITIES: readonly Capability[] = [
  'generate',
  'embed',
  'structured_output',
  'thinking',
  'attachments',
  'tools',
  'prompt_caching',
];

/** A model in the catalog: what it costs, what it can do, how it is reached (FRD-403, FRD-114). */
export interface CatalogModel {
  name: string;
  display_name?: string;
  /** Released for use by a Global Administrator (`FRD-307`). Only an approved model may be
   *  called by a use case; an *undeclared* model is not gated by this. */
  approved?: boolean;
  provider?: string;
  input_price_per_million?: string | null;
  /** What a cache read and a cache write cost on this model (FRD-133). Absent means "charge the
   *  ordinary input rate" — never "free", so an undeclared rate can only over-state a bill. */
  cached_input_price_per_million?: string | null;
  cache_write_price_per_million?: string | null;
  output_price_per_million?: string | null;
  is_priced?: boolean;
  updated_at?: string;

  capabilities?: Capability[];
  /** Which vendor's API shape it speaks — selects the upstream dialect (ADR-0011). */
  publisher?: string;
  /** Which transport reaches it (`vertex`, `foundry`, …). */
  platform?: string;
  /** Platform addressing. Never edited from a use case; see ADR-0011 rule 2. */
  addressing?: Record<string, unknown> | null;
  /** What the price attaches to when the caller-facing name is not the vendor's. */
  underlying_model?: string;
  max_output_tokens?: number | null;
  /** Applied when the caller sets no cap — Anthropic requires one on every request. */
  default_max_output_tokens?: number | null;
  thinking?: Record<string, unknown> | null;
  embedding?: Record<string, unknown> | null;
  attachments?: Record<string, unknown> | null;
  hosting?: '' | 'managed' | 'self_deployed';
  /** Warns, never blocks — blocking is what revocation is for. */
  deprecated?: boolean;
  numeric_id?: number | null;
  /** Whether anyone has said what this model can do. Undeclared means the baseline only. */
  is_declared?: boolean;
}

/**
 * Whether a declared model can actually be reached (`FRD-506`).
 *
 * Three separate facts, never collapsed. `declared` says somebody wrote the model down and proves
 * nothing about reachability; `served` says an adapter exists for it, which is what a missing
 * credential fails; `reachable` is `null` when nothing was contacted — "we did not look" and "it is
 * fine" are different answers.
 */
export interface ModelCheck {
  model: string;
  declared: boolean;
  served: boolean;
  reachable: boolean | null;
  detail: string;
}

/** A battery of questions to put to a model (`FRD-504`). */
/**
 * One question in the catalogue.
 *
 * `topic` is the keyword saying what it tests — a label on a row, not a categorisation. Nothing
 * branches on it and nothing is grouped by it.
 */
/**
 * Where a smoke-test run is booked, and whether this caller may book one.
 *
 * All three facts come from the server. The console holding the slug would go silently wrong the
 * day the seed renamed it, and the console deciding `may_call` from a membership list is the defect
 * this endpoint exists because of.
 */
export interface TestAttribution {
  use_case: string;
  name: string;
  exists: boolean;
  may_call: boolean;
}

export interface TestCase {
  id: number;
  topic: string;
  prompt: string;
  expectation: string;
  position: number;
}

/**
 * How a run stands.
 *
 * `unrated` is reported apart from everything else, deliberately: a run nobody has read is **not**
 * a run with no failures, and reporting it as "0 failed" states something false in the most
 * reassuring direction.
 */
export interface TestCounts {
  total: number;
  unrated: number;
  pass: number;
  fail: number;
  unclear: number;
}

export interface TestRun {
  id: number;
  model: string;
  use_case: string;
  started_at: string;
  finished_at: string | null;
  requested_by_name: string;
  counts: TestCounts;
}

export type TestVerdict = 'unrated' | 'pass' | 'fail' | 'unclear';

export interface TestResult {
  id: number;
  run: number;
  topic: string;
  prompt: string;
  expectation: string;
  /** The model's answer. Hidden in the list on purpose — see `smoke-tests.ts`. */
  response: string;
  /** Set when the *request* failed, which is a different fact from a bad answer. */
  error: string;
  latency_ms: number | null;
  verdict: TestVerdict;
  note: string;
  rated_by_name: string;
  rated_at: string | null;
}

/**
 * **The latest run of one battery against one model** — not a total across every run.
 *
 * A standardised catalogue exists so models can be compared against the *same* questions. The
 * figure that answers "how does this model do" is therefore its most recent result, not an average
 * that an old, since-corrected run drags down forever. Earlier runs are **history**, and they stay
 * readable under Runs.
 *
 * One row per model, because there is one catalogue.
 */
export interface TestModelStats {
  model: string;
  run: number;
  /** How many questions the catalogue asks today, which an older run may not have been asked. */
  catalogue: number;
  started_at: string;
  requested_by: string;
  total: number;
  unrated: number;
  pass: number;
  fail: number;
  unclear: number;
  errored: number;
}

/** One row of a report: a group, and what happened in it (FRD-601). */
export interface ReportRow {
  /** The use case, model or member this row is about. */
  key: string;
  requests: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  /** Spend in nano-units — integer, safe to divide for a bar. */
  cost_nanos: number;
  /** The same amount as an exact decimal string, which is what a human reads. */
  cost: string;
  /** Of the prompt tokens, how many the provider served from its cache (FRD-133). The share is
   *  what makes a cache measurable: a cache that has silently stopped working looks exactly like
   *  an expensive month otherwise. */
  cached_input_tokens: number;
  /** Requests on a model with no price. Their cost is unknown, not zero. */
  unpriced_requests: number;
  failed_requests: number;
  avg_latency_ms: number | null;
  max_latency_ms: number | null;
}

export interface Report {
  from: string;
  to: string;
  /** `all` when the caller holds a governance role, otherwise their own use cases. */
  scope: 'all' | 'use_cases';
  totals: ReportRow;
  by_use_case: ReportRow[];
  by_model: ReportRow[];
  by_member: ReportRow[];
  /** Why requests ended the way they did — `served`, `rate_limited`, … (FRD-122). */
  by_outcome: ReportRow[];
  /** The single use case this report was narrowed to, or `null` for all of them (FRD-603). */
  use_case?: string | null;
  /**
   * Whether the caller was allowed to see what they asked for.
   *
   * `false` means the report is empty because the use case is not theirs, not because nothing
   * happened in it. Two different facts that look identical in the rows, and a screen shown only
   * the rows reports the second as the first.
   */
  in_scope?: boolean;
}

/**
 * What a use case actually consumed, independent of any limit (FRD-603).
 *
 * The distinction this type exists to keep: `month`/`today` being `null` means **unknown** —
 * the gateway was not reachable, or the caller may not see this use case — and is never the same
 * as a row of zeroes, which means nothing was consumed. Rendering unknown as zero is how a page
 * comes to state, confidently, that a use case cost nothing.
 */
export interface UseCaseConsumption {
  month: ReportRow | null;
  today: ReportRow | null;
  /**
   * **Nothing** arrived. Deliberately not "something went wrong": the two windows are two
   * requests, and a single flag written by both meant whichever answered last decided what the
   * reader saw — a month that had already been fetched was hidden because the day's request
   * failed a moment later.
   */
  unavailable: boolean;
  /** One window arrived and the other did not. What is known is shown; the rest says so. */
  partial: boolean;
  /**
   * Why it did not arrive, **in the backend's own words** where there are any
   * (`core/api/error-message.ts`). "The gateway did not answer" is a guess, and it is the wrong
   * guess for every failure that is not a timeout — the server usually said something more useful.
   */
  reason: string;
  /** The gateway answered, and this use case is not one this caller may see figures for. */
  outOfScope: boolean;
}

/** One page of findings — cursor-paged, because findings are an append-only log. */
export interface AnomalyPage {
  events: AnomalyEvent[];
  next_cursor: string | null;
  scope: string;
  in_scope?: boolean;
}

/** One finding: a rule crossed its threshold for one target (`FRD-501`). */
export interface AnomalyEvent {
  id: string;
  created_at: string;
  rule: string;
  kind: string;
  use_case: string | null;
  target: string;
  target_value: string;
  /** What was measured, the threshold it crossed, and how many rows it was drawn from. A finding
   * nobody can check is a finding nobody acts on. */
  observed: number;
  threshold: number;
  sample: number;
  window_minutes: number;
  /** What was **done** — kept separate from what the rule asked for (`ADR-0014` §3). */
  action_taken: string;
  detail: string;
}

/** A written decision that some traffic is stopped (`FRD-503`). */
export interface Suspension {
  id: string;
  created_at: string | null;
  use_case: string | null;
  target: string;
  target_value: string;
  action: string;
  throttle_rpm: number | null;
  expires_at: string | null;
  /** `rule:<name>` or `user:<subject>` — never blank. */
  author: string;
  reason: string;
  lifted_at: string | null;
  lifted_by: string | null;
}

/**
 * One request, as metadata (`FRD-502`).
 *
 * **No payloads.** Not the prompt, not the response, not a snippet — that is what `FRD-406` still
 * blocks, and it is a different thing from showing what happened.
 */
export interface Trace {
  id: string;
  created_at: string;
  operation: string;
  api: string;
  model: string;
  requested_model: string | null;
  model_selection: string | null;
  status: number;
  outcome: string | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  latency_ms: number | null;
  cost_nanos: number | null;
  provider: string | null;
  region: string | null;
  trace_id: string | null;
  subject: string;
  credential: string | null;
  use_case: string | null;
  /** What the model asked to have run (`FRD-131` FR-7) — **names and counts, never arguments**. */
  tool_calls: { declared: number; called: string[] } | null;
  /** A pipeline step objected to this request — blocked it, or flagged it and let it through. */
  flagged?: boolean;
  /** Only sent to an incident role. Absent for everybody else, which is why it is optional here
   *  rather than nullable: "the column is not for you" and "the row has no address" differ. */
  source_ip?: string | null;
}

/**
 * A stored prompt and answer, or the precise reason there is none (`FRD-505`).
 *
 * `available: false` is not an error — it is an answer, and `reason` says which of three: the use
 * case does not store payloads, retention has removed them, or this request never reached a model.
 * A screen that rendered one sentence for all three would teach its reader to distrust it.
 */
export interface TracePayload {
  id: string;
  available: boolean;
  request?: unknown;
  response?: unknown;
  reason?: string;
  message?: string;
  /** The authority the read rested on, recorded with it: `incident`, `use_case_admin`, … */
  ground?: string;
}

export interface TracePage {
  traces: Trace[];
  /** `null` when this is the last page. A cursor, not an offset: rows arrive while somebody
   * reads, and an offset under an appending table shows some rows twice and skips others. */
  next_cursor: string | null;
  scope: 'all' | 'use_cases';
  /**
   * False when the caller's visibility does not cover what was asked for — which is a different
   * empty from "nothing has happened yet", and the screen must not print the second when it means
   * the first. Optional so an older gateway (which sends neither) is read as "in scope", the
   * behaviour that was there before.
   */
  in_scope?: boolean;
}

/** An anomaly rule as the gateway and Management both understand it (`FRD-500`). */
export interface AnomalyRule {
  id: number;
  use_case: string | null;
  is_global: boolean;
  name: string;
  kind: string;
  window_minutes: number;
  threshold: number;
  parameter: number | null;
  min_sample: number;
  action: string;
  target: string;
  action_minutes: number | null;
  throttle_rpm: number | null;
  enabled: boolean;
}
