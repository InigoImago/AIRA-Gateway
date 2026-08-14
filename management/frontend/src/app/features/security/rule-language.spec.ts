import { AnomalyEvent, AnomalyRule } from '../../core/api/models';
import { describeAction, describeEvent, describeRule, unitOf } from './rule-language';

function rule(over: Partial<AnomalyRule> = {}): AnomalyRule {
  return {
    id: 1,
    use_case: null,
    is_global: true,
    name: 'too many refusals',
    kind: 'refusal_rate',
    window_minutes: 15,
    threshold: 50,
    parameter: null,
    min_sample: 20,
    action: 'alert',
    target: 'subject',
    action_minutes: null,
    throttle_rpm: null,
    enabled: true,
    ...over,
  };
}

function event(over: Partial<AnomalyEvent> = {}): AnomalyEvent {
  return {
    id: 'e1',
    created_at: '2026-08-08T10:00:00Z',
    rule: 'too many refusals',
    kind: 'refusal_rate',
    use_case: 'kundenservice',
    target: 'subject',
    target_value: 'ada',
    observed: 90,
    threshold: 50,
    sample: 20,
    window_minutes: 15,
    action_taken: 'alert',
    detail: '90% refusals',
    ...over,
  };
}

describe('describeRule', () => {
  it('says what is watched, about whom, over how long, and what follows', () => {
    const sentence = describeRule(rule());

    expect(sentence).toContain('refused');
    expect(sentence).toContain('per caller');
    expect(sentence).toContain('15 minutes');
    expect(sentence).toContain('50 % of requests');
  });

  it('reads a spike as a multiple, never as an amount', () => {
    // `FRD-500` chose a ratio deliberately: a fixed number is a budget and there already is one.
    // Printing "300" beside a spend rule invites exactly the reading it was chosen to avoid.
    const sentence = describeRule(rule({ kind: 'spend_spike', threshold: 300 }));

    expect(sentence).toContain('3× the previous window');
    expect(sentence).not.toContain('300 ');
  });

  it('says that an alerting rule takes nothing away', () => {
    // `ADR-0014` keeps detecting and doing apart, and a reader deciding whether to touch a rule
    // needs to know which of the two it is doing.
    expect(describeRule(rule({ action: 'alert' }))).toContain('nothing is taken away');
  });

  it('names the duration of a block, and the rate of a throttle', () => {
    // "An enum member is not a specification" (`FRD-503` §7): a throttle without its rate is a
    // decision nobody can review.
    expect(describeRule(rule({ action: 'block', action_minutes: 30 }))).toContain(
      'stopped for 30 minutes',
    );
    expect(describeRule(rule({ action: 'block', action_minutes: null }))).toContain(
      'until somebody lifts it',
    );
    expect(describeRule(rule({ action: 'throttle', throttle_rpm: 12 }))).toContain(
      '12 requests a minute',
    );
  });

  it('mentions the smallest sample only when there is one', () => {
    expect(describeRule(rule({ min_sample: 20 }))).toContain('fewer than 20 requests');
    expect(describeRule(rule({ min_sample: 0 }))).not.toContain('fewer than');
  });

  it('names the byte figure a payload rule needs', () => {
    // The kind that was declared with one number and needed two — the defect stage A shipped.
    expect(describeRule(rule({ kind: 'payload_size', parameter: 1_000_000 }))).toContain(
      '1000000 bytes',
    );
  });
});

describe('describeEvent', () => {
  it('states the measurement, its threshold and how many rows it came from', () => {
    const sentence = describeEvent(event());

    expect(sentence).toContain('15 minutes');
    expect(sentence).toContain('kundenservice');
    expect(sentence).toContain('ada');
    expect(sentence).toContain('90 % of requests');
    expect(sentence).toContain('20 request(s)');
  });

  it('says "across every use case" when a global rule fired without one', () => {
    expect(describeEvent(event({ use_case: null }))).toContain('across every use case');
  });

  it('reads a spike finding as a multiple too', () => {
    expect(describeEvent(event({ kind: 'spend_spike', observed: 450, threshold: 200 }))).toContain(
      '4.5× the previous window',
    );
  });
});

describe('describeAction', () => {
  it('keeps "recorded" and "enforced" apart, in words', () => {
    expect(describeAction(event({ action_taken: 'alert' }))).toContain('nothing was taken away');
    expect(describeAction(event({ action_taken: 'blocked' }))).toContain('Traffic was stopped');
    expect(describeAction(event({ action_taken: 'throttled' }))).toContain('Traffic was slowed');
  });

  it('does not let "asked to block, did not" read as "blocked"', () => {
    // The engine records `detected_not_enforced` in those words for a reason: a rule that asked
    // for a block and got none is a finding *and* a gap, and a console that showed only "blocked"
    // would report traffic as stopped that was still flowing.
    const sentence = describeAction(event({ action_taken: 'detected_not_enforced' }));

    expect(sentence).toContain('it was not');
    expect(sentence).toContain('the traffic continued');
  });
});

describe('unitOf', () => {
  it('knows a share from a multiple, and says nothing about a kind it has never met', () => {
    expect(unitOf('refusal_rate')).toBe('% of requests');
    expect(unitOf('request_spike')).toBe('× the previous window');
    // Forward compatible: a kind added to the gateway before this console knows it must show a
    // bare number, never a wrong unit.
    expect(unitOf('something_new')).toBe('');
  });
});

describe('rule-language — every kind has words', () => {
  // **A fourth hand-written copy of a closed vocabulary**, and it was wrong in the same two ways
  // as the other three: it listed `token_spike`, which does not exist, and omitted
  // `blocked_prompt_rate`, which does. So this test asserted that every kind has words by checking
  // a list that was missing the kind without any — a guard agreeing with the thing it guards.
  //
  // Kept explicit, because TypeScript cannot import the Python enum; held to it by
  // `tools/tests/test_the_console_speaks_the_closed_vocabulary.py`, which compares every copy
  // against `aira_common.anomalies` in both directions.
  const KINDS = [
    'refusal_rate',
    'error_rate',
    'spend_spike',
    'request_spike',
    'blocked_prompt_rate',
    'payload_size',
    'new_source_ip',
  ];

  it('describes all seven, and none of them by their slug', () => {
    // The vocabulary is closed (`aira_common.anomalies`), which is exactly what makes this safe
    // to write — and what makes a missing case a real gap rather than a default nobody hits.
    for (const kind of KINDS) {
      const sentence = describeRule(rule({ kind, threshold: 200, parameter: 1000 }));
      expect(sentence, kind).not.toContain(kind);
      expect(sentence.length, kind).toBeGreaterThan(40);
    }
  });

  it('falls back to the raw kind rather than inventing a meaning', () => {
    // A kind the gateway grows before this console knows it must read as unfamiliar, not as
    // something else — the same rule as "undeclared means the baseline and nothing more".
    expect(describeRule(rule({ kind: 'future_kind' }))).toContain('future_kind');
  });

  it("names each target in the reader's terms", () => {
    expect(describeRule(rule({ target: 'subject' }))).toContain('per caller');
    expect(describeRule(rule({ target: 'credential' }))).toContain('per API key');
    expect(describeRule(rule({ target: 'use_case' }))).toContain('per use case');
    expect(describeRule(rule({ target: 'something' }))).toContain('per something');
  });

  it('does not claim a throttle rate or a duration it was not given', () => {
    expect(describeRule(rule({ action: 'throttle', throttle_rpm: null }))).toContain(
      'traffic is slowed',
    );
    expect(describeRule(rule({ action: 'throttle', throttle_rpm: null }))).not.toContain(
      'a minute',
    );
  });

  it('says nothing about a payload size that was never set', () => {
    expect(describeRule(rule({ kind: 'payload_size', parameter: null }))).toContain('— bytes');
  });

  it('passes an unfamiliar action through rather than calling it recorded', () => {
    expect(describeAction(event({ action_taken: 'something_new' }))).toBe('something_new');
  });
});
