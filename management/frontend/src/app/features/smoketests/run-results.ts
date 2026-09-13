import { TestRun, TestVerdict } from '../../core/api/models';

/** How much of a failed request's message is stored on the result. */
const FAILURE_NOTE_MAX = 240;

/** The badge class for a verdict, so pass, fail and "cannot tell" read apart at a glance. */
export function verdictBadge(verdict: TestVerdict): string {
  if (verdict === 'pass') return 'badge badge--success';
  if (verdict === 'fail') return 'badge badge--danger';
  if (verdict === 'unclear') return 'badge badge--warning';
  return 'badge';
}

/**
 * What is stored for a request that failed: the gateway's own message, or the status.
 *
 * A network drop carries no error envelope, and the stored note must still say something.
 */
export function describeFailure(error: unknown): string {
  const body = (error as { error?: { error?: { message?: string } } })?.error?.error?.message;
  const status = (error as { status?: number })?.status;
  return (body ?? `request failed${status ? ` (${status})` : ''}`).slice(0, FAILURE_NOTE_MAX);
}

/** The export's file name, with anything a file name cannot carry replaced. */
export function csvFileName(run: TestRun): string {
  return `aira-smoketest-${run.use_case.replace(/[^\w.-]/g, '_')}.csv`;
}
