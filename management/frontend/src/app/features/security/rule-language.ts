import { AnomalyEvent, AnomalyRule } from '../../core/api/models';

/**
 * Saying what a rule does, and what a finding found, in words.
 *
 * The console used to print the raw vocabulary — `refusal_rate`, `spend_spike`, `new_source_ip` —
 * next to two bare numbers. That is enough for whoever wrote the rule and nothing for whoever has
 * to decide, at eleven at night, whether the alert in front of them matters. A closed vocabulary
 * (`aira_common.anomalies`) is exactly what makes this safe to write: there are seven kinds, they
 * cannot grow by configuration, and each has one meaning.
 *
 * Two things stay honest here:
 *
 * - **A ratio is not a threshold.** `spend_spike` at 300 means "three times the window before",
 *   not "300 euros" — `FRD-500` chose a ratio deliberately, because a fixed number is a budget and
 *   there already is one. Printing it without the word "times" invites exactly that confusion.
 * - **`alert` is not enforcement.** `ADR-0014` keeps detecting and doing apart, and so does every
 *   sentence below: a rule that alerts *records* and takes nothing away.
 */

const UNITS: Record<string, string> = {
  refusal_rate: '% of requests',
  error_rate: '% of requests',
  spend_spike: '× the previous window',
  request_spike: '× the previous window',
  token_spike: '× the previous window',
  payload_size: '% of requests',
  new_source_ip: 'address(es)',
};

/** What the rule is watching, as a noun phrase that finishes "watches …". */
function subject(rule: AnomalyRule): string {
  switch (rule.kind) {
    case 'refusal_rate':
      return `the share of requests that were refused — by a limit, a budget, the pipeline or a suspension`;
    case 'error_rate':
      return 'the share of requests that failed upstream';
    case 'spend_spike':
      return 'how much more was spent than in the window before';
    case 'request_spike':
      return 'how many more requests arrived than in the window before';
    case 'token_spike':
      return 'how many more tokens were used than in the window before';
    case 'payload_size':
      return `the share of requests whose body was larger than ${rule.parameter ?? '—'} bytes`;
    case 'new_source_ip':
      return 'requests arriving from an address that has not been seen before';
    default:
      return rule.kind;
  }
}

/** Who or what a finding is raised about. */
function about(rule: AnomalyRule): string {
  switch (rule.target) {
    case 'subject':
      return 'per caller';
    case 'credential':
      return 'per API key';
    case 'use_case':
      return 'per use case';
    default:
      return `per ${rule.target}`;
  }
}

function consequence(rule: AnomalyRule): string {
  switch (rule.action) {
    case 'block':
      return rule.action_minutes
        ? `traffic is stopped for ${rule.action_minutes} minutes`
        : 'traffic is stopped until somebody lifts it';
    case 'throttle':
      return rule.throttle_rpm
        ? `traffic is slowed to ${rule.throttle_rpm} requests a minute`
        : 'traffic is slowed';
    default:
      // The default, and a safety property: a system whose first setting is `block` blocks
      // wrongly once and is switched off forever (`FRD-500` §3).
      return 'it is recorded — nothing is taken away';
  }
}

export function unitOf(kind: string): string {
  return UNITS[kind] ?? '';
}

/** One sentence: what is watched, over how long, about whom, and what happens when it trips. */
export function describeRule(rule: AnomalyRule): string {
  const unit = unitOf(rule.kind);
  const measure = unit.startsWith('×')
    ? `${rule.threshold / 100}× the previous window`
    : `${rule.threshold}${unit ? ' ' + unit : ''}`;
  const sample =
    rule.min_sample > 0
      ? ` It is not judged on fewer than ${rule.min_sample} requests — a rate over too few rows is noise.`
      : '';
  return (
    `Watches ${subject(rule)}, ${about(rule)}, over ${rule.window_minutes} minutes. ` +
    `Above ${measure}, ${consequence(rule)}.${sample}`
  );
}

/** The same, for a finding that has already happened: what was measured, against what. */
export function describeEvent(event: AnomalyEvent): string {
  const unit = unitOf(event.kind);
  const observed = unit.startsWith('×')
    ? `${(event.observed / 100).toFixed(1)}× the previous window`
    : `${event.observed}${unit ? ' ' + unit : ''}`;
  const threshold = unit.startsWith('×')
    ? `${(event.threshold / 100).toFixed(1)}×`
    : `${event.threshold}${unit ? ' ' + unit : ''}`;
  const scope = event.use_case ? `in ${event.use_case}` : 'across every use case';
  return (
    `Over ${event.window_minutes} minutes ${scope}, ${event.target_value} reached ${observed}, ` +
    `against a threshold of ${threshold}, measured over ${event.sample} request(s).`
  );
}

/** What the system did about a finding — the second half of `ADR-0014`'s separation. */
export function describeAction(event: AnomalyEvent): string {
  switch (event.action_taken) {
    case 'blocked':
      return 'Traffic was stopped. It appears under Suspensions until it expires or is lifted.';
    case 'throttled':
      return 'Traffic was slowed. It appears under Suspensions until it expires or is lifted.';
    case 'alert':
      return 'Recorded only. The rule asks for an alert, so nothing was taken away.';
    case 'detected_not_enforced':
      return 'The rule asks for traffic to be stopped, and it was not — enforcement was unavailable at the time. The finding stands; the traffic continued.';
    default:
      return event.action_taken;
  }
}
