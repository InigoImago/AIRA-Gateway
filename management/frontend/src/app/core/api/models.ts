export interface Me {
  subject: string;
  username: string;
  email: string;
  roles: string[];
  use_cases: string[];
}

export interface UseCase {
  slug: string;
  name: string;
  description: string;
  processing_notes: string;
  /** Whether prompts and responses are stored at all (FRD-404). */
  store_payloads?: boolean;
  /** How long stored prompts and responses are kept, in days (FRD-404). */
  retention_days?: number;
  created_at?: string;
  updated_at?: string;
}

export interface Membership {
  username: string;
  role: string;
  created_at?: string;
}

export interface ApiKey {
  prefix: string;
  label: string;
  owner: string;
  is_active: boolean;
  created_at?: string;
  revoked_at?: string | null;
}

/** Issue response — the only time the plaintext key is ever returned. */
export interface IssuedApiKey {
  api_key: string;
  prefix: string;
  label: string;
  use_case: string;
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
export type Capability = 'generate' | 'embed' | 'structured_output' | 'thinking' | 'attachments';

export const CAPABILITIES: readonly Capability[] = [
  'generate',
  'embed',
  'structured_output',
  'thinking',
  'attachments',
];

/** A model in the catalog: what it costs, what it can do, how it is reached (FRD-403, FRD-114). */
export interface CatalogModel {
  name: string;
  display_name?: string;
  provider?: string;
  input_price_per_million?: string | null;
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
}
