import { BudgetScope, LimitScope } from '../../core/api/models';

/** Each budget scope's place in the hierarchy, top first (see `hierarchyRank`). */
const RANK: Readonly<Record<string, number>> = { use_case: 0, member: 1, each_member: 2 };

/**
 * Who a budget or a rate limit binds, in words — one definition for both tabs, which wrote it
 * twice. "Whole use case" for a per-person row would read as one pot forty people share.
 */
export function scopeLabel(scope: BudgetScope | string): string {
  if (scope === 'member') return 'One person';
  return scope === 'each_member' ? 'Each member, individually' : 'Whole use case';
}

/** Whether any row binds each member separately — when the per-person note is worth showing. */
export function bindsEachMember(
  rows: readonly { scope: LimitScope | BudgetScope | string }[],
): boolean {
  return rows.some((row) => row.scope === 'each_member' || row.scope === 'member');
}

/**
 * Where a scope stands in the hierarchy a budget is applied in (`FRD-407`): the use case's budget
 * over everybody, a person's own in place of the one for everybody, the one for everybody beneath.
 * The tab lists budgets in that order, so the page reads the way the gateway decides.
 */
export function hierarchyRank(scope: BudgetScope | string): number {
  return RANK[scope] ?? 3;
}
