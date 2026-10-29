import { isoDay, lastDayOf, periodText, utcDay, windowError, windowFor } from './periods';

/**
 * The windows are the gateway's: UTC days. These cases are written as UTC instants, so they hold in
 * whatever zone the suite runs — and the late-evening and after-midnight ones are exactly the hours
 * where a local calendar and the budget counters disagree.
 */
describe('windowFor', () => {
  const at = (instant: string) => new Date(instant);

  it('covers the whole current month, exclusive at the end', () => {
    expect(windowFor('this-month', at('2026-08-06T12:00:00Z'))).toEqual({
      from: '2026-08-01',
      to: '2026-09-01',
    });
  });

  it('covers the previous month and stops where this one begins', () => {
    expect(windowFor('last-month', at('2026-08-06T12:00:00Z'))).toEqual({
      from: '2026-07-01',
      to: '2026-08-01',
    });
  });

  it('includes today in the trailing windows', () => {
    // The exclusive end is tomorrow: a report of "the last 7 days" that stopped at midnight
    // would silently omit everything that happened today, which is what people look at first.
    const now = at('2026-08-06T12:00:00Z');
    expect(windowFor('last-7-days', now)).toEqual({ from: '2026-07-31', to: '2026-08-07' });
    expect(windowFor('last-30-days', now)).toEqual({ from: '2026-07-08', to: '2026-08-07' });
  });

  it('crosses a year boundary without landing in the wrong year', () => {
    expect(windowFor('last-month', at('2027-01-15T12:00:00Z'))).toEqual({
      from: '2026-12-01',
      to: '2027-01-01',
    });
  });

  it('asks for the day the budget counters are on, wherever the reader is', () => {
    // 00:30 in Berlin on 13 October is 22:30 UTC on the 12th. The counter that refuses is still on
    // the 12th; asking for the 13th showed an untouched allowance beside a gateway refusing on an
    // exhausted one.
    expect(windowFor('today', at('2026-10-12T22:30:00Z'))).toEqual({
      from: '2026-10-12',
      to: '2026-10-13',
    });
    // 21:00 in New York on 6 August is already the 7th in UTC — and today's traffic is in the
    // window, not cut off at a local midnight that the gateway never heard of.
    expect(windowFor('last-7-days', at('2026-08-07T01:00:00Z'))).toEqual({
      from: '2026-08-01',
      to: '2026-08-08',
    });
  });

  it('starts a month when the UTC month starts', () => {
    // 00:30 on 1 October in Berlin is still September for every monthly budget.
    expect(windowFor('this-month', at('2026-09-30T22:30:00Z'))).toEqual({
      from: '2026-09-01',
      to: '2026-10-01',
    });
  });
});

/**
 * The same hours read from a browser in a real zone. Pinned here rather than left to the machine:
 * on a machine set to UTC a local calendar and the gateway's agree, and a test that only runs there
 * cannot see the defect it is named after.
 */
describe('windowFor, from a browser that is not on UTC', () => {
  // The suite runs in Node, which re-reads `TZ` when it changes; the spec build has no Node types.
  const env = (globalThis as unknown as { process: { env: Record<string, string | undefined> } })
    .process.env;
  const original = env['TZ'];
  afterEach(() => {
    if (original === undefined) delete env['TZ'];
    else env['TZ'] = original;
  });

  it('east of Greenwich, just after local midnight, still asks for the UTC day', () => {
    env['TZ'] = 'Europe/Berlin';
    const halfPastMidnight = new Date(2026, 9, 13, 0, 30); // 13 October, 00:30 in Berlin

    expect(windowFor('today', halfPastMidnight)).toEqual({ from: '2026-10-12', to: '2026-10-13' });
  });

  it('west of Greenwich, in the evening, includes the UTC day that has begun', () => {
    env['TZ'] = 'America/New_York';
    const evening = new Date(2026, 7, 6, 21, 0); // 6 August, 21:00 in New York

    expect(windowFor('last-7-days', evening)).toEqual({ from: '2026-08-01', to: '2026-08-08' });
  });
});

describe('utcDay', () => {
  it('names the UTC day a moment falls on', () => {
    expect(utcDay(new Date('2026-10-12T22:30:00Z'))).toBe('2026-10-12');
    expect(utcDay(new Date('2026-10-13T00:00:00Z'))).toBe('2026-10-13');
  });
});

describe('isoDay', () => {
  it('writes the calendar date a date was built from, without converting it first', () => {
    // For arithmetic on a date the reader typed: `new Date(y, m, d)` is that date in any zone, and
    // `toISOString()` would move it a day east of Greenwich.
    expect(isoDay(new Date(2026, 7, 6, 23, 30))).toBe('2026-08-06');
    expect(isoDay(new Date(2026, 0, 1, 0, 15))).toBe('2026-01-01');
  });
});

describe('a window as two period screens hold it', () => {
  it('refuses a window that is missing a day or ends before it starts, before anything is asked', () => {
    expect(windowError({ from: '', to: '2026-08-07' })).toBe('Choose a start and an end date.');
    expect(windowError({ from: '2026-08-07', to: '2026-08-07' })).toBe(
      'The end date must be after the start date.',
    );
    expect(windowError({ from: '2026-08-01', to: '2026-08-07' })).toBeNull();
  });

  it('reads the exclusive end as the last day included, across a month and a year', () => {
    expect(lastDayOf('2026-09-01')).toBe('2026-08-31');
    expect(lastDayOf('2027-01-01')).toBe('2026-12-31');
    expect(lastDayOf('')).toBe('');
  });

  it('says which clock the days are on', () => {
    expect(periodText({ from: '2026-08-01', to: '2026-09-01' })).toBe(
      '2026-08-01 to 2026-08-31 (UTC)',
    );
    expect(periodText({ from: '2026-08-01', to: '2026-09-01' }, 'Europe/Berlin')).toBe(
      '2026-08-01 to 2026-08-31 (Europe/Berlin)',
    );
    expect(periodText(null)).toBe('');
  });
});

/**
 * On an installation whose clock is not UTC (`FRD-629`): the days are that clock's, because its
 * midnight is where every counter resets and every bare date the gateway is sent begins.
 */
describe('windowFor, on the installation’s clock', () => {
  const at = (instant: string) => new Date(instant);

  it('asks for the installation’s day just after its midnight', () => {
    // 00:30 on 13 October in Berlin; UTC still says the 12th.
    expect(windowFor('today', at('2026-10-12T22:30:00Z'), 'Europe/Berlin')).toEqual({
      from: '2026-10-13',
      to: '2026-10-14',
    });
  });

  it('starts a month when the installation’s month starts', () => {
    expect(windowFor('this-month', at('2026-09-30T22:30:00Z'), 'Europe/Berlin')).toEqual({
      from: '2026-10-01',
      to: '2026-11-01',
    });
    expect(windowFor('last-month', at('2026-09-30T22:30:00Z'), 'Europe/Berlin')).toEqual({
      from: '2026-09-01',
      to: '2026-10-01',
    });
  });

  it('counts back whole days from the installation’s today, across its clock change', () => {
    // 00:30 on 26 October in Berlin, on winter time since the night before.
    expect(windowFor('last-7-days', at('2026-10-25T23:30:00Z'), 'Europe/Berlin')).toEqual({
      from: '2026-10-20',
      to: '2026-10-27',
    });
  });

  it('is behind UTC west of it', () => {
    // 20:00 on 31 December in New York is New Year's Day in UTC.
    expect(windowFor('this-month', at('2027-01-01T01:00:00Z'), 'America/New_York')).toEqual({
      from: '2026-12-01',
      to: '2027-01-01',
    });
  });
});
