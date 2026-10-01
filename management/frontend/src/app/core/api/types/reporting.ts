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
  /** Of the prompt tokens, how many the provider served from its cache (FRD-133) — what makes a
   *  cache that silently stopped working visible. */
  cached_input_tokens: number;
  /** Requests whose cost is **unknown, not zero** — nothing could be computed for them. */
  unpriced_requests: number;
  /**
   * Of those, the ones where **nothing was reported to price**: the upstream answered with no token
   * count (`FRD-626` FR-18).
   *
   * A different fact from "this model has no price on file", and a different remedy — adding a price
   * fixes the second and does nothing for the first. Absent on a report from before this existed.
   */
  unmetered_requests?: number;
  failed_requests: number;
  avg_latency_ms: number | null;
  max_latency_ms: number | null;
}

/** One person's consumption, and which credential produced which part of it (`FRD-606`). */
export interface PersonRow extends ReportRow {
  /** Keyed by auth method — `oidc`, `api_key`. Absent methods simply did not call. */
  by_method?: Record<string, ReportRow>;
}

/** How wide one column of the usage chart is (`FRD-626`). */
export type Granularity = 'day' | 'hour';

/** What the coloured bands of the usage chart are. */
export type SeriesSplit = 'model' | 'use_case' | 'outcome';

/** One band of one bucket: a model (or use case, or outcome) on a given day or hour. */
export interface SeriesPoint extends ReportRow {
  /** `2026-10-01` for a day, `2026-10-01T14` for an hour. An ISO-8601 prefix, never a local one. */
  bucket: string;
}

/**
 * The period bucket by bucket (`FRD-626`) — what the histogram and the composition blocks draw.
 *
 * `buckets` carries the **empty** ones too: a chart drawn only from buckets that have rows puts
 * Monday beside Friday and calls it a week.
 */
export interface UsageSeries {
  granularity: Granularity;
  split: SeriesSplit;
  /** Every bucket of the window, in order, including the quiet ones. */
  buckets: string[];
  /**
   * The bands, biggest first — which is also the order their colours are assigned in, so a period
   * with fewer bands does not repaint the ones that survive.
   */
  keys: string[];
  /** Whether a tail of small bands was folded into `(other)`. */
  folded: boolean;
  points: SeriesPoint[];
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
  /**
   * One **person**, both ways they can authenticate (`FRD-606`).
   *
   * Not a rename of `by_member`, which stays keyed on `subject` like every counter and budget. This
   * is keyed on the name the credential carried, so a person calling with a key and signing in to
   * the console is one figure; a row whose credential named nobody stands alone under its subject.
   */
  by_person?: PersonRow[];
  /** The single use case this report was narrowed to, or `null` for all of them (FRD-603). */
  use_case?: string | null;
  /**
   * Whether the caller was allowed to see what they asked for. `false` means empty because the use
   * case is not theirs, not because nothing happened in it.
   */
  in_scope?: boolean;
  /**
   * The time series, **present only when it was asked for** (`FRD-626`). Absent is not empty: a
   * report loaded for its figures alone does not pay for a query nothing on the page reads.
   */
  series?: UsageSeries;
}

/**
 * What a use case actually consumed, independent of any limit (FRD-603).
 *
 * `month`/`today` being `null` means **unknown** — the gateway was not reachable, or the caller may
 * not see this use case — never a row of zeroes, which means nothing was consumed.
 */
export interface UseCaseConsumption {
  month: ReportRow | null;
  today: ReportRow | null;
  /**
   * **Nothing** arrived. Not "something went wrong": the two windows are two requests, and one flag
   * written by both let whichever answered last decide what the reader saw.
   */
  unavailable: boolean;
  /** One window arrived and the other did not. What is known is shown; the rest says so. */
  partial: boolean;
  /** Why it did not arrive, in the backend's own words where there are any
   *  (`core/api/error-message.ts`). */
  reason: string;
  /** The gateway answered, and this use case is not one this caller may see figures for. */
  outOfScope: boolean;
}

/**
 * One model a use case may call, and where the catalogue says it lives (`FRD-608`).
 *
 * `catalogued: false` and `approved: false` have one consequence — a configured model no request
 * will reach — and need different actions, so the register keeps them apart.
 */
export interface RegisterModel {
  name: string;
  provider: string;
  publisher: string;
  /** Empty where the platform addresses a model by name alone, which is most of them. */
  regions: string[];
  approved: boolean;
  catalogued: boolean;
}

/** Where traffic actually went, from the audit trail rather than from the configuration. */
export interface ProcessedIn {
  /** `(not applicable)` where the dialect addresses a model by name and has no region. */
  region: string;
  provider: string;
  requests: number;
}

/** One use case, as the register of processing activities reads it (`FRD-608`). */
export interface RegisterEntry {
  slug: string;
  name: string;
  status: 'live' | 'retired';
  purpose: string;
  processing: string;
  models: RegisterModel[];
  prompts_stored: boolean;
  /** `null` where prompts are not stored: there is no erasure deadline for data never written. */
  retention_days: number | null;
  own_requests_only: boolean;
  tools: boolean;
  prompt_caching: boolean;
  cache_ttl: string;
  reasoning: boolean;
  members: number;
  groups: number;
  requests: number;
  processed_in: ProcessedIn[];
  /**
   * Regions the traffic reached that no released model's catalogue entry names — **the finding**.
   *
   * Empty is the ordinary answer, and not the same as "nothing ran" (`requests` says that). A row
   * with no region is never here: most dialects address a model by name.
   */
  unexpected_regions: string[];
}

/** The last pass of the retention sweep (`FRD-608` §2.4) — erasure as evidence, not a setting. */
export interface Erasure {
  ran_at: string;
  payloads_cleared: number;
  rows_deleted: number;
}

/** The register of processing activities (`FRD-608`). */
export interface Register {
  from: string;
  to: string;
  /** `all` when the caller oversees the installation, otherwise their own use cases. */
  scope: 'all' | 'use_cases';
  use_cases: RegisterEntry[];
  /**
   * Where the installation processed requests in this period, **including traffic that names no
   * use case** (break-glass keys, the console's model checks, demo traffic). Empty for a caller who
   * does not oversee the installation.
   */
  processed_in: ProcessedIn[];
  /**
   * Every model **the gateway** holds, for a reader who oversees the installation (`FRD-608` §4) —
   * the other half of *is what we think is configured what is actually running*. Empty otherwise.
   */
  catalogue: string[];
  /** `null` when the sweep has no recorded pass — which is a fact, not a zero. */
  last_erasure: Erasure | null;
}
