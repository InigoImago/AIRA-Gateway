/**
 * Who a budget or a rate limit applies to.
 *
 * `use_case` is a **shared pot** the first caller can spend entirely; `each_member` bounds every
 * person the same way without naming any of them, including people who join later. A scope naming
 * one person was removed by the owner's decision; `subject` stays on the wire for older rows and is
 * ignored.
 */
export type LimitScope = 'use_case' | 'each_member';

/**
 * A budget's scope, one word wider than a rate limit's (`FRD-610`).
 *
 * `installation` is spend that belongs to **no** use case — the console's model checks, a
 * break-glass key, demo traffic. Kept out of `LimitScope` because nothing enforces a
 * per-installation request rate, and a type offering the word would let a form ask for it.
 */
export type BudgetScope = LimitScope | 'installation';

export interface Budget {
  id?: number;
  scope: BudgetScope;
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
}
