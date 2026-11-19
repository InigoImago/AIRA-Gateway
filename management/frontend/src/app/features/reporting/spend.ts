import { Report } from '../../core/api/models';

/** The outcome of a request that ran: the only kind with a cost there could be to know. */
const SERVED = 'served';

/**
 * Whether a period's spend is unknown outright (`FRD-626` FR-18): every request that ran was
 * unpriced and nothing else carried a cost, so `0.00` would be a measurement nobody made.
 *
 * Against the requests that **ran**, not all of them: a refusal ran nothing and has no cost to be
 * unknown, so with one refusal in the period "every request was unpriced" could never hold. One
 * rule for every screen that prints a period's spend — the reporting totals and a use case's own
 * consumption — so the two never print `—` and `0.00` for the same traffic.
 */
export function spendUnknown(report: Pick<Report, 'totals' | 'by_outcome'> | null): boolean {
  if (!report) return false;
  const { totals } = report;
  if (totals.unpriced_requests === 0 || totals.cost_nanos !== 0) return false;
  const ran = (report.by_outcome ?? [])
    .filter((row) => row.key === SERVED)
    .reduce((sum, row) => sum + row.requests, 0);
  return totals.unpriced_requests >= ran;
}
