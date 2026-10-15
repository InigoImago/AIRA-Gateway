import { AnomalyEvent, AnomalyRule } from '../../core/api/models';
import {
  describeAction,
  describeEvent,
  describeRule,
  figureText,
  needsSample,
  thresholdHint,
  thresholdText,
  unitOf,
} from './rule-language';

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

  it('names the duration of a block, and the rate and the duration of a throttle', () => {
    // "An enum member is not a specification" (`FRD-503` §7): a throttle without its rate is a
    // decision nobody can review, and one without its duration says nothing about when it ends.
    expect(describeRule(rule({ action: 'block', action_minutes: 30 }))).toContain(
      'stopped for 30 minutes',
    );
    expect(
      describeRule(rule({ action: 'throttle', throttle_rpm: 12, action_minutes: 45 })),
    ).toContain('slowed to 12 requests a minute for 45 minutes');
  });

  it('does not call an automatic action with no duration enforced', () => {
    /** The gateway records such a rule's findings as `detected_not_enforced` and takes nothing
     *  away; "until somebody lifts it" described an outage the platform refuses to cause. */
    for (const action of ['block', 'throttle']) {
      const sentence = describeRule(rule({ action, throttle_rpm: 12, action_minutes: null }));
      expect(sentence, action).toContain('nothing is enforced');
      expect(sentence, action).not.toContain('until somebody lifts it');
      expect(sentence, action).not.toContain('traffic is');
    }
  });

  it('says a threshold is reached, not exceeded, because the evaluator fires at equality', () => {
    // `share >= threshold` and `len(fresh) >= threshold`: "above 1 address" would mean two.
    const sentence = describeRule(rule({ kind: 'new_source_ip', threshold: 1, min_sample: 0 }));
    expect(sentence).toContain('At 1 new address(es) or more');
    expect(sentence).not.toMatch(/above/i);
    // And the byte figure is `request_bytes >= parameter`: a body of exactly that size counts.
    const payload = describeRule(rule({ kind: 'payload_size', parameter: 1000 }));
    expect(payload).toContain('at least 1000 bytes');
    expect(payload).not.toContain('larger than');
  });

  it('mentions the smallest sample only when there is one', () => {
    expect(describeRule(rule({ min_sample: 20 }))).toContain('fewer than 20 requests');
    expect(describeRule(rule({ min_sample: 0 }))).not.toContain('fewer than');
  });

  it('names the byte figure a payload rule needs', () => {
    // `payload_size` needs a byte figure as well as a count (`FRD-501` §7).
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
    // A rule that asked for a block and got none is a finding *and* a gap; showing "blocked" would
    // report traffic as stopped that was still flowing.
    const sentence = describeAction(event({ action_taken: 'detected_not_enforced' }));

    expect(sentence).toContain('it was not');
    expect(sentence).toContain('the traffic continued');
  });
});

describe('unitOf', () => {
  it('names the unit a stored threshold is in, and nothing for a kind it has never met', () => {
    expect(unitOf('refusal_rate')).toBe('% of requests');
    // Stored in percent of the previous window: a bare `300` beside "× the previous window" read
    // as three hundred times.
    expect(unitOf('request_spike')).toBe('% of the previous window');
    // Forward compatible: a kind added to the gateway before this console knows it must show a
    // bare number, never a wrong unit.
    expect(unitOf('something_new')).toBe('');
  });
});

describe('thresholdText and figureText', () => {
  it('read a stored spike threshold as the multiple it means', () => {
    // `aira_common.anomalies.threshold_unit`: "percent of the previous window" — 300 is 3×.
    expect(thresholdText('spend_spike', 300)).toBe('3× the previous window');
    expect(thresholdText('request_spike', 250)).toBe('2.5× the previous window');
    expect(figureText('spend_spike', 450)).toBe('4.5×');
    expect(thresholdText('spend_spike', 300)).not.toContain('300');
  });

  it('keep a share in percent, a count as a count, and an unknown kind bare', () => {
    expect(thresholdText('refusal_rate', 50)).toBe('50 % of requests');
    expect(figureText('refusal_rate', 90)).toBe('90 %');
    expect(thresholdText('new_source_ip', 2)).toBe('2 new address(es)');
    expect(figureText('new_source_ip', 2)).toBe('2');
    expect(thresholdText('future_kind', 7)).toBe('7');
  });
});

describe('thresholdHint', () => {
  it('hints a spike field in percent, the unit the server refuses it in, and reads it back', () => {
    // The refusal says "above 100 percent — 200 means twice as much"; a hint saying "×" invited
    // a `3` for three times and a 400 in a unit nobody had mentioned.
    expect(thresholdHint('spend_spike', null)).toBe(
      '% of the previous window — 200 is twice as much',
    );
    expect(thresholdHint('spend_spike', 300)).toBe('% of the previous window — 300 is 3× as much');
    expect(thresholdHint('refusal_rate', 50)).toContain('at most 100');
    expect(thresholdHint('new_source_ip', 1)).toBe('new address(es)');
  });
});

describe('needsSample', () => {
  it('asks a smallest sample only of a proportion', () => {
    // `aira_common.anomalies.needs_sample`: the server stores 0 for an event kind whatever is sent.
    expect(needsSample('refusal_rate')).toBe(true);
    expect(needsSample('spend_spike')).toBe(true);
    expect(needsSample('new_source_ip')).toBe(false);
  });
});

describe('rule-language — every kind has words', () => {
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
    // The vocabulary is closed (`aira_common.anomalies`), so a missing case is a real gap.
    for (const kind of KINDS) {
      const sentence = describeRule(rule({ kind, threshold: 200, parameter: 1000 }));
      expect(sentence, kind).not.toContain(kind);
      expect(sentence.length, kind).toBeGreaterThan(40);
    }
  });

  it('falls back to the raw kind rather than inventing a meaning', () => {
    // A kind the gateway grows before this console knows it must read as unfamiliar, not as
    // something else.
    expect(describeRule(rule({ kind: 'future_kind' }))).toContain('future_kind');
  });

  it("names each target in the reader's terms", () => {
    expect(describeRule(rule({ target: 'subject' }))).toContain('per caller');
    expect(describeRule(rule({ target: 'credential' }))).toContain('per API key');
    expect(describeRule(rule({ target: 'use_case' }))).toContain('per use case');
    expect(describeRule(rule({ target: 'something' }))).toContain('per something');
  });

  it('does not claim a throttle rate it was not given', () => {
    const sentence = describeRule(
      rule({ action: 'throttle', throttle_rpm: null, action_minutes: 30 }),
    );
    expect(sentence).toContain('traffic is slowed for 30 minutes');
    expect(sentence).not.toContain('a minute');
  });

  it('says nothing about a payload size that was never set', () => {
    expect(describeRule(rule({ kind: 'payload_size', parameter: null }))).toContain('— bytes');
  });

  it('passes an unfamiliar action through rather than calling it recorded', () => {
    expect(describeAction(event({ action_taken: 'something_new' }))).toBe('something_new');
  });
});

describe('describeEvent — strict data mode (`FRD-627` FR-8)', () => {
  it('says what kind of target it was when the value is withheld', () => {
    expect(describeEvent(event({ target_value: null, target_withheld: true }))).toContain(
      'a person reached',
    );
    expect(
      describeEvent(event({ target: 'credential', target_value: null, target_withheld: true })),
    ).toContain('a key reached');
  });
});
