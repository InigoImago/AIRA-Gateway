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

/** One finding: a rule crossed its threshold for one target (`FRD-501`). */
export interface AnomalyEvent {
  id: string;
  created_at: string;
  rule: string;
  kind: string;
  use_case: string | null;
  target: string;
  target_value: string;
  /** What was measured, the threshold it crossed, and how many rows it was drawn from — a finding
   *  nobody can check is a finding nobody acts on. */
  observed: number;
  threshold: number;
  sample: number;
  window_minutes: number;
  /** What was **done** — kept separate from what the rule asked for (`ADR-0014` §3). */
  action_taken: string;
  detail: string;
}

/** One page of findings — cursor-paged, because findings are an append-only log. */
export interface AnomalyPage {
  events: AnomalyEvent[];
  next_cursor: string | null;
  scope: string;
  in_scope?: boolean;
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
 * **No payloads** — not the prompt, not the response, not a snippet. Content is `TracePayload`,
 * fetched separately and recorded as read.
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
  /** Only sent to an incident role, hence optional rather than nullable: "the column is not for
   *  you" and "the row has no address" differ. */
  source_ip?: string | null;
}

/**
 * A stored prompt and answer, or the precise reason there is none (`FRD-505`).
 *
 * `available: false` is an answer, not an error, and `reason` says which of three: the use case
 * does not store payloads, retention removed them, or the request never reached a model.
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
  /** `null` on the last page. A cursor, not an offset: rows arrive while somebody reads, and an
   *  offset under an appending table shows some rows twice and skips others. */
  next_cursor: string | null;
  scope: 'all' | 'use_cases';
  /**
   * False when the caller's visibility does not cover what was asked for — a different empty from
   * "nothing has happened yet". Optional so an older gateway, which sends neither, reads as in
   * scope.
   */
  in_scope?: boolean;
}

/** One reading of a request's stored prompt and response (`FRD-622` FR-4). Metadata only. */
export interface ContentRead {
  id: string;
  created_at: string;
  request_log_id: string;
  use_case: string;
  subject: string;
  username: string | null;
  /** The authority the read rested on: `incident`, `use_case_admin` or `use_case_member`. */
  ground: string;
  /** The reader's organisation-wide roles at that moment; `null` for a read from before they were
   *  kept, which is unknown rather than "none". */
  roles: string[] | null;
}

export interface ContentReadPage {
  reads: ContentRead[];
  /** `null` on the last page. */
  next_cursor: string | null;
}
