/**
 * Saying what a rule does, and what a finding found, in words rather than the raw kind and two bare
 * numbers.
 *
 * Safe to write because the vocabulary is closed (`aira_common.anomalies`): seven kinds, each with
 * one meaning. Three things stay honest here:
 *
 * - **A ratio is not a threshold.** `spend_spike` at 300 means "three times the window before",
 *   not "300 euros" — `FRD-500` chose a ratio because a fixed number is a budget, and there is one.
 *   It is *stored* in percent of the previous window, which is the unit the server's refusals
 *   speak, and *read* as a multiple, which is the unit a person thinks in.
 * - **A threshold is reached, not exceeded.** The evaluator fires at equality (`share >= threshold`,
 *   `request_bytes >= parameter`), so no sentence here says "above".
 * - **`alert` is not enforcement.** `ADR-0014` keeps detecting and doing apart, and so does every
 *   sentence below: a rule that alerts *records* and takes nothing away.
 */
import { AnomalyEvent, AnomalyRule } from '../../core/api/models';

/** What each kind's stored threshold is counted in — the unit a bare number beside it is in. */
const UNITS: Record<string, string> = {
  refusal_rate: '% of requests',
  error_rate: '% of requests',
  spend_spike: '% of the previous window',
  request_spike: '% of the previous window',
  payload_size: '% of requests',
  blocked_prompt_rate: '% of requests',
  new_source_ip: 'new address(es)',
};

/** Kinds whose threshold is a share of requests, in percent — `aira_common.anomalies.RATE_KINDS`. */
const RATE_KINDS = new Set(['refusal_rate', 'error_rate', 'payload_size', 'blocked_prompt_rate']);

/**
 * Kinds whose threshold is a multiple of the previous window, stored in percent — `RATIO_KINDS`
 * there. 300 is three times as much, and 100 is no growth at all.
 */
const RATIO_KINDS = new Set(['spend_spike', 'request_spike']);

/** Decimals a multiple is shown to: 333 is "3.33×", and 300 is "3×" rather than "3.00×". */
const MULTIPLE_DECIMALS = 2;

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
    case 'blocked_prompt_rate':
      return 'the share of requests the pipeline blocked';
    case 'payload_size':
      return `the share of requests whose body was at least ${rule.parameter ?? '—'} bytes`;
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
    case 'throttle': {
      const effect =
        rule.action === 'block'
          ? 'stopped'
          : rule.throttle_rpm
            ? `slowed to ${rule.throttle_rpm} requests a minute`
            : 'slowed';
      // The gateway carries out an automatic action only for as long as the rule says, and records
      // a rule that names no duration as `detected_not_enforced` (`FRD-125`): such a rule takes
      // nothing away, and saying "until somebody lifts it" would claim an outage it never causes.
      return rule.action_minutes
        ? `traffic is ${effect} for ${rule.action_minutes} minutes`
        : `traffic would be ${effect}, but the rule names no duration, so nothing is enforced — ` +
            `each finding is recorded as not enforced`;
    }
    default:
      // `alert`, the safe default (`FRD-500` §3).
      return 'it is recorded — nothing is taken away';
  }
}

/** A share in percent of the previous window as the multiple it means: 300 is "3×". */
function multiple(percent: number): string {
  return `${Number((percent / 100).toFixed(MULTIPLE_DECIMALS))}×`;
}

export function unitOf(kind: string): string {
  return UNITS[kind] ?? '';
}

/** Whether a smallest sample means anything for this kind — `aira_common.anomalies.needs_sample`.
 *  An event is not a proportion: twenty new-address sightings before saying so is twenty leaks. */
export function needsSample(kind: string): boolean {
  return RATE_KINDS.has(kind) || RATIO_KINDS.has(kind);
}

/** Whether this kind's threshold is a share of requests, which cannot exceed 100 percent. */
export function isShare(kind: string): boolean {
  return RATE_KINDS.has(kind);
}

/** Whether this kind's threshold is a multiple of the previous window, stored in percent. */
export function isMultiple(kind: string): boolean {
  return RATIO_KINDS.has(kind);
}

/**
 * A threshold as a reader takes it in, unit included — the one place a stored threshold becomes
 * words, so a table cell, a detail grid and the sentence under them cannot disagree.
 *
 * A spike reads as a multiple ("3× the previous window"), never as the stored "300", which beside
 * a spend rule invites exactly the reading the ratio was chosen to avoid.
 */
export function thresholdText(kind: string, value: number): string {
  if (RATIO_KINDS.has(kind)) return `${multiple(value)} the previous window`;
  const unit = unitOf(kind);
  return unit ? `${value} ${unit}` : `${value}`;
}

/** A threshold or a measurement short enough for a table cell: "3×", "50 %", "2". */
export function figureText(kind: string, value: number): string {
  if (RATIO_KINDS.has(kind)) return multiple(value);
  if (RATE_KINDS.has(kind)) return `${value} %`;
  return `${value}`;
}

/**
 * What the threshold **field** takes, in the unit it is stored and refused in.
 *
 * A spike's field takes percent of the previous window, because that is what the server checks and
 * what its refusal says ("above 100 percent — 200 means twice as much"); a hint in multiples
 * invites "3" for three times, which the server refuses in a unit the hint never mentioned. The
 * typed number is read back as a multiple, so the two units are seen side by side.
 */
export function thresholdHint(kind: string, value: number | null): string {
  if (RATIO_KINDS.has(kind)) {
    return value && value > 100
      ? `% of the previous window — ${value} is ${multiple(value)} as much`
      : '% of the previous window — 200 is twice as much';
  }
  if (RATE_KINDS.has(kind)) return '% of requests — at most 100';
  return unitOf(kind) || 'as counted';
}

/** One sentence: what is watched, over how long, about whom, and what happens when it trips. */
export function describeRule(rule: AnomalyRule): string {
  const sample =
    rule.min_sample > 0
      ? ` It is not judged on fewer than ${rule.min_sample} requests — a rate over too few rows is noise.`
      : '';
  return (
    `Watches ${subject(rule)}, ${about(rule)}, over ${rule.window_minutes} minutes. ` +
    `At ${thresholdText(rule.kind, rule.threshold)} or more, ${consequence(rule)}.${sample}`
  );
}

/** The same, for a finding that has already happened: what was measured, against what. */
export function describeEvent(event: AnomalyEvent): string {
  const observed = thresholdText(event.kind, event.observed);
  const threshold = RATIO_KINDS.has(event.kind)
    ? multiple(event.threshold)
    : thresholdText(event.kind, event.threshold);
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
