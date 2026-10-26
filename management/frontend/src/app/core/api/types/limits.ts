/**
 * Who a budget or a rate limit applies to.
 *
 * `use_case` is a **shared pot** the first caller can spend entirely; `each_member` bounds every
 * person the same way without naming any of them, including people who join later.
 */
export type LimitScope = 'use_case' | 'each_member';

/**
 * A budget's scope, wider than a rate limit's (`FRD-610`, `FRD-407`).
 *
 * `member` is **one person's own** budget: it takes the place of the `each_member` budget of its
 * period for the person it names, higher or lower, and the `use_case` budget still bounds them.
 * Offered for budgets only. `installation` is spend that belongs to **no** use case — the console's
 * model checks, a break-glass key, demo traffic. Both are kept out of `LimitScope`, because a
 * type offering a word would let the rate-limit form ask for it.
 */
export type BudgetScope = LimitScope | 'member' | 'installation';

export interface Budget {
  id?: number;
  scope: BudgetScope;
  /**
   * The person a `member` budget is for, by username; empty for every other scope. `null` where
   * strict data mode withholds the name from this reader (`subject_withheld`).
   */
  subject?: string | null;
  /** True when the server withholds who a `member` budget is for (`FRD-627`). */
  subject_withheld?: boolean;
  /**
   * Whether the person a `member` budget names can still call the use case. `false` is a budget
   * that binds nobody now; `null` is "not known to this reader" or "no person named".
   */
  reaches?: boolean | null;
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
  scope: LimitScope;
  subject?: string;
  /** Sustained requests per minute. */
  limit_rpm: number;
  /** How many may arrive at once; 0 means "use the per-minute figure". */
  burst?: number;
  enabled?: boolean;
}

/**
 * Current-period consumption of one budget.
 *
 * Every figure is nullable because a per-person budget is one row and N counters: there is no
 * single number for a reader the row does not bind, and zero is not that answer (`FRD-603`:
 * unknown is never rendered as zero).
 */
export interface BudgetUsage {
  id: number;
  /** `''` for a shared row, a username for a per-person one, `null` when it binds nobody the
   *  reader could be told about. */
  measured_for?: string | null;
  used_tokens: number | null;
  used_requests: number | null;
  /** Consumed spend in nano-units — integer, safe to divide for a progress bar. */
  used_cost_nanos: number | null;
  /** The same amount rounded for display. */
  used_cost: string | null;
  /** Requests served by a model with no price on file; their cost is unknown, not zero. */
  unpriced_requests: number | null;
  /** Whether this budget binds the reader under the hierarchy (`FRD-407`). Absent from older gateways. */
  binds?: boolean;
  /**
   * Where a per-person budget gives way to another for the reader — their own budget in place of
   * the one for everybody, or the reverse while their own is switched off — the one that binds.
   */
  replaced_by?: number | null;
}
