import { LimitScope } from '../../core/api/models';

/**
 * Who a budget or a rate limit binds, in words — one definition for both tabs, which wrote it
 * twice. "Whole use case" for a per-person row would read as one pot forty people share.
 */
export function scopeLabel(scope: LimitScope | string): string {
  return scope === 'each_member' ? 'Each member, individually' : 'Whole use case';
}

/** Whether any row binds each member separately — when the per-person note is worth showing. */
export function bindsEachMember(rows: readonly { scope: LimitScope | string }[]): boolean {
  return rows.some((row) => row.scope === 'each_member');
}
