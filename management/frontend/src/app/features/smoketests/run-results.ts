import { errorMessage } from '../../core/api/error-message';
import { TestResult, TestRun, TestVerdict } from '../../core/api/models';
import { DEFAULT_CLOCK, dayIn } from '../../core/ui/clock';

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
 * Whether the run never put this question to the model.
 *
 * A run creates every result row before its first question, and only asking fills one in — with an
 * answer or an error, and always a latency. A row with none of the three is a question the run did
 * not reach: it stopped early, or it is still going. There is nothing in it to judge.
 */
export function neverAsked(result: TestResult): boolean {
  return !result.response && !result.error && result.latency_ms === null;
}

/**
 * What is stored for a request that failed, in the words of whichever envelope answered.
 *
 * Read through `errorMessage`, so the compatibility surface's flat `{code, message}` and a
 * validation answer's `details` are kept like everywhere else in the console. A network drop
 * carries no envelope at all, and the stored note must still say something.
 */
export function describeFailure(error: unknown): string {
  const status = (error as { status?: number })?.status;
  const fallback = `request failed${status ? ` (${status})` : ''}`;
  return errorMessage(error, fallback).slice(0, FAILURE_NOTE_MAX);
}

/**
 * The export's file name: the use case, the run and the day it started, with anything a file name
 * cannot carry replaced. The run id is in it because a use case is run again and again, and two
 * exports of the same use case must not save as one file and a "(1)".
 */
export function csvFileName(run: TestRun, zone: string = DEFAULT_CLOCK): string {
  const useCase = run.use_case.replace(/[^\w.-]/g, '_');
  // The day as the runs table shows it: on the installation's clock (`FRD-629`).
  const day = dayIn(new Date(run.started_at), zone);
  return `aira-smoketest_${useCase}_run-${run.id}_${day}.csv`;
}
