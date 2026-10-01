import { TestBed } from '@angular/core/testing';
import { SeriesPoint, UsageSeries } from '../../api/types/reporting';
import { UsageDays } from './usage-days';
import { buildDays } from './usage-series';

function point(over: Partial<SeriesPoint> = {}): SeriesPoint {
  return {
    bucket: '2026-09-01',
    key: 'chat-1',
    requests: 2,
    prompt_tokens: 100,
    completion_tokens: 50,
    total_tokens: 150,
    cost_nanos: 1_000_000_000,
    cost: '1.00',
    cached_input_tokens: 0,
    unpriced_requests: 0,
    failed_requests: 0,
    avg_latency_ms: 40,
    max_latency_ms: 90,
    ...over,
  };
}

function series(over: Partial<UsageSeries> = {}): UsageSeries {
  return {
    granularity: 'day',
    split: 'model',
    buckets: ['2026-09-01', '2026-09-02', '2026-09-03'],
    keys: ['chat-1'],
    folded: false,
    points: [point()],
    ...over,
  };
}

function setup(over: Partial<UsageSeries> = {}, unit = ' (EUR)') {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({ imports: [UsageDays] });
  const fixture = TestBed.createComponent(UsageDays);
  fixture.componentRef.setInput('days', buildDays(series(over)));
  fixture.componentRef.setInput('unit', unit);
  fixture.detectChanges();
  const element = fixture.nativeElement as HTMLElement;
  return {
    fixture,
    element,
    text: () => element.textContent ?? '',
    groups: () => Array.from(element.querySelectorAll<HTMLElement>('tbody')),
    headings: () =>
      Array.from(element.querySelectorAll<HTMLElement>('thead th')).map((cell) =>
        (cell.textContent ?? '').trim(),
      ),
    cells: (row: HTMLElement) =>
      Array.from(row.querySelectorAll('th,td')).map((cell) => (cell.textContent ?? '').trim()),
    more: () => element.querySelector<HTMLElement>('[data-testid="days-more"]'),
  };
}

describe('UsageDays', () => {
  it('reads a day as a sentence: what was used, and what it cost', () => {
    // The whole reason this view exists. The chart says *how much* against the tallest day; a
    // reader asking "what did I do on the twelfth, and what did that cost me" needs the three
    // measures on one line.
    const { groups, cells } = setup({
      keys: ['chat-1', 'embed-1'],
      points: [
        point({ key: 'chat-1', requests: 14, cost_nanos: 3_000_000_000 }),
        point({ key: 'embed-1', requests: 4, cost_nanos: 250_000_000 }),
      ],
    });
    const [total, chat, embed] = Array.from(groups()[0].querySelectorAll('tr'));

    expect(cells(total)).toEqual(['2026-09-01', 'all models', '18', '3.25', '200 / 100']);
    expect(cells(chat)).toEqual(['', 'chat-1', '14', '3.00', '100 / 50']);
    expect(cells(embed)).toEqual(['', 'embed-1', '4', '0.25', '100 / 50']);
  });

  it('puts the money before the token split', () => {
    // On a phone this table scrolls inside its wrapper, so the fifth column is the one a reader has
    // to drag for. "What did it cost" is the question; the token split is the one that can wait.
    expect(setup().headings()).toEqual([
      'Day',
      'Model',
      'Requests',
      'Spend (EUR)',
      'Prompt / completion tokens',
    ]);
  });

  it('lists the newest day first', () => {
    // A question about usage is nearly always about a day somebody remembers, and those are recent.
    const { groups } = setup({
      points: [point({ bucket: '2026-09-01' }), point({ bucket: '2026-09-03' })],
    });

    expect(groups().map((group) => group.dataset['testid'])).toEqual([
      'day-2026-09-03',
      'day-2026-09-01',
    ]);
  });

  it('leaves out a day with nothing in it', () => {
    // The opposite rule from the chart's, and both are right: on an axis an empty day is a reading,
    // because the week has a shape. In a list it is a row that says nothing, thirty times over.
    expect(setup().groups()).toHaveLength(1);
  });

  it('says a period had no traffic rather than printing an empty table', () => {
    const { element, text } = setup({ points: [] });

    expect(element.querySelector('[data-testid="usage-days"]')).toBeNull();
    expect(text()).toContain('No day in this period had any traffic');
  });

  it('never shows a non-zero spend as zero', () => {
    // A low-volume use case — or any demo — spends fractions of a cent, and `0.00` on this row
    // would say the day was free.
    const { text } = setup({ points: [point({ cost_nanos: 3_674_900 })] });

    expect(text()).toContain('0.0037');
  });

  it('shows a spend nobody could compute as unknown, never as zero', () => {
    // Reported from the running showcase: the local embedding model costs `0.00` on this list, and
    // it is not free — the runtime reports no tokens, so nothing could be priced. `0.00` is a
    // measurement; this one was never made (`LESSONS.md` §2).
    const { groups } = setup({
      keys: ['chat-1', 'embed-1'],
      points: [
        point({ key: 'chat-1', requests: 4, cost_nanos: 1_000_000_000 }),
        point({ key: 'embed-1', requests: 9, cost_nanos: 0, unpriced_requests: 9 }),
      ],
    });
    const rows = Array.from(groups()[0].querySelectorAll('tr'));
    const embed = rows.find((row) => row.textContent?.includes('embed-1'))!;

    expect(embed.textContent).toContain('—');
    expect(embed.textContent).not.toContain('0.00');
    // The day still has a figure, because one of its two bands could be priced — and it is a lower
    // bound, which is a different statement from "unknown".
    expect(rows[0].textContent).toContain('1.00');
    expect(rows[0].textContent).toContain('9 unpriced');
  });

  it('shows a day where nothing could be priced as unknown, with no arithmetic beside it', () => {
    const { groups } = setup({
      points: [point({ requests: 9, cost_nanos: 0, unpriced_requests: 9 })],
    });
    const total = groups()[0].querySelector('tr')!;

    expect(total.textContent).toContain('—');
    // Not "— + 9 unpriced", which reads as a sum nobody can do.
    expect(total.textContent).not.toContain('unpriced');
  });

  it('says where a day’s spend is a lower bound', () => {
    const { groups } = setup({
      points: [point({ cost_nanos: 0, unpriced_requests: 3 })],
    });

    expect(groups()[0].textContent).toContain('3 unpriced');
  });

  it('marks the requests on a row that failed', () => {
    const { groups } = setup({ points: [point({ requests: 10, failed_requests: 2 })] });

    expect(groups()[0].textContent).toContain('(2 failed)');
  });

  it('shows the most recent week and offers the rest', () => {
    // Enough that "what did I do this week" needs no click; few enough that thirty days do not bury
    // everything under this card.
    const buckets = Array.from(
      { length: 12 },
      (_, day) => `2026-09-${String(day + 1).padStart(2, '0')}`,
    );
    const harness = setup({
      buckets,
      points: buckets.map((bucket) => point({ bucket })),
    });

    expect(harness.groups()).toHaveLength(7);
    expect(harness.more()?.textContent).toContain('Show all 12 days');

    harness.more()!.click();
    harness.fixture.detectChanges();

    expect(harness.groups()).toHaveLength(12);
  });

  it('offers nothing to expand when every day is already listed', () => {
    expect(setup().more()).toBeNull();
  });
});
