/**
 * Putting the question catalogue to a use case's pipeline (`FRD-504`, `ADR-0020`).
 */

/**
 * Whether this caller may book a run against one use case, and why not.
 *
 * All of it is the server's: the console holding a slug would break when the seed renamed it, and
 * deciding `may_run` from a membership list is the defect this endpoint exists to prevent.
 */
export interface TestAttribution {
  use_case: string;
  name: string;
  /**
   * The models a run may be **entered at**: exactly what is released to this use case, chosen per
   * run so two runs can compare two models. Empty exactly when `may_run` is false.
   */
  models: string[];
  may_run: boolean;
  /** Why not, in a sentence naming the fix. Empty when `may_run` is true. */
  why_not: string;
}

/** One question in the catalogue. `topic` is a label on a row; nothing branches on it. */
export interface TestCase {
  id: number;
  topic: string;
  prompt: string;
  expectation: string;
  position: number;
  /**
   * Out of the catalogue, with its answers kept — only ever *sent*, to retire a question. Deleting
   * it would take the verdicts formed against its wording with it (`TestResult.case` is `PROTECT`).
   */
  retired?: boolean;
}

/**
 * How a run stands. `unrated` is reported apart: a run nobody has read is **not** a run with no
 * failures, and "0 failed" would state something false in the most reassuring direction.
 */
export interface TestCounts {
  total: number;
  unrated: number;
  pass: number;
  fail: number;
  unclear: number;
}

export interface TestRun {
  id: number;
  model: string;
  use_case: string;
  started_at: string;
  finished_at: string | null;
  requested_by_name: string;
  counts: TestCounts;
}

export type TestVerdict = 'unrated' | 'pass' | 'fail' | 'unclear';

export interface TestResult {
  id: number;
  run: number;
  topic: string;
  prompt: string;
  expectation: string;
  /** The model's answer. Hidden in the list on purpose — see `smoke-tests.ts`. */
  response: string;
  /** Set when the *request* failed, which is a different fact from a bad answer. */
  error: string;
  latency_ms: number | null;
  verdict: TestVerdict;
  note: string;
  rated_by_name: string;
  rated_at: string | null;
}

/**
 * **The latest run of the catalogue against one use case** — not a total across every run.
 *
 * Models are compared against the *same* questions, so the figure that answers "how does this do"
 * is the most recent result; earlier runs are history under Runs. The start model is named on the
 * row because it can change between runs.
 */
export interface TestModelStats {
  use_case: string;
  /** What that run entered the pipeline at. Not necessarily what answered — a router may reroute. */
  model: string;
  run: number;
  /** How many questions the catalogue asks today, which an older run may not have been asked. */
  catalogue: number;
  started_at: string;
  requested_by: string;
  total: number;
  unrated: number;
  pass: number;
  fail: number;
  unclear: number;
  errored: number;
}
