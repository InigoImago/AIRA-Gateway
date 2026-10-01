import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { SeriesPoint, UsageSeries } from '../../api/types/reporting';
import { UsageChart } from './usage-chart';

function point(over: Partial<SeriesPoint> = {}): SeriesPoint {
  return {
    bucket: '2026-09-01',
    key: 'chat-1',
    requests: 2,
    prompt_tokens: 10,
    completion_tokens: 20,
    total_tokens: 30,
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
    buckets: ['2026-09-01', '2026-09-02'],
    keys: ['chat-1'],
    folded: false,
    points: [point()],
    ...over,
  };
}

function setup(inputs: Record<string, unknown> = {}) {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({
    imports: [UsageChart],
    providers: [provideHttpClient(), provideHttpClientTesting()],
  });
  const fixture = TestBed.createComponent(UsageChart);
  for (const [name, value] of Object.entries(inputs)) {
    fixture.componentRef.setInput(name, value);
  }
  fixture.detectChanges();
  const element = fixture.nativeElement as HTMLElement;
  const find = <T extends HTMLElement>(testid: string) =>
    element.querySelector<T>(`[data-testid="${testid}"]`);
  return {
    fixture,
    element,
    find,
    text: () => element.textContent ?? '',
    choose: (testid: string, value: string) => {
      const select = find<HTMLSelectElement>(testid)!;
      select.value = value;
      select.dispatchEvent(new Event('change'));
      fixture.detectChanges();
    },
    click: (testid: string) => {
      find<HTMLElement>(testid)!.click();
      fixture.detectChanges();
    },
    rows: () =>
      Array.from(element.querySelectorAll<HTMLElement>('[data-testid="chart-table"] tbody tr')).map(
        (row) =>
          Array.from(row.querySelectorAll('th,td')).map((cell) => (cell.textContent ?? '').trim()),
      ),
  };
}

describe('UsageChart', () => {
  it('draws the histogram and the blocks from one series', () => {
    // The two halves of the question: the histogram says *when*, the blocks say *what the whole is
    // made of*. One card, because reading them against each other is the point.
    const { element } = setup({ series: series() });

    expect(element.querySelector('[data-testid="histogram"]')).not.toBeNull();
    expect(element.querySelector('[data-testid="blocks"]')).not.toBeNull();
  });

  it('shows a legend with every band and what it did over the period', () => {
    // Always, for two bands or seven: identity is never colour alone.
    const { element, text } = setup({
      series: series({
        keys: ['chat-1', 'embed-1'],
        points: [
          point({ key: 'chat-1', cost_nanos: 3_000_000_000 }),
          point({ key: 'embed-1', cost_nanos: 1_000_000_000 }),
        ],
      }),
    });

    expect(element.querySelectorAll('[data-testid="chart-legend"] li')).toHaveLength(2);
    expect(text()).toContain('chat-1');
    expect(text()).toContain('3.00');
  });

  it('switches the measure without asking the parent to reload', () => {
    // The gateway sends spend, requests and tokens for every bucket, so this costs no request — and
    // the one control that *would* need one reports upwards instead.
    const { choose, find, fixture } = setup({
      series: series({ points: [point({ requests: 42 })] }),
    });
    let reloads = 0;
    fixture.componentInstance.granularityChange.subscribe(() => (reloads += 1));
    fixture.componentInstance.splitChange.subscribe(() => (reloads += 1));

    choose('chart-metric-select', 'requests');

    expect(find('chart-total')?.textContent?.trim()).toBe('42');
    expect(reloads).toBe(0);
  });

  it('reports a different granularity upwards rather than fetching it itself', () => {
    // The page owns the load (`CLAUDE.md` §3). A chart that fetched its own axis would be a second
    // place that decides which window and which scope.
    const { choose, fixture } = setup({ series: series() });
    const asked: string[] = [];
    fixture.componentInstance.granularityChange.subscribe((value) => asked.push(value));

    choose('chart-grain-select', 'hour');

    expect(asked).toEqual(['hour']);
  });

  it('reports a different split upwards', () => {
    const { choose, fixture } = setup({ series: series() });
    const asked: string[] = [];
    fixture.componentInstance.splitChange.subscribe((value) => asked.push(value));

    choose('chart-split-select', 'outcome');

    expect(asked).toEqual(['outcome']);
  });

  it('offers no period control where the page already has one', () => {
    // The reporting screen's picker sits above everything and drives the tables too. A second one
    // inside the card would be two answers to one question.
    expect(setup({ series: series() }).find('chart-period-select')).toBeNull();
  });

  it('offers the periods it was given, and reports a choice upwards', () => {
    // A use case's own page has no other period control: the consumption card above answers *this
    // month* and *today* by design, and the panel below answers whatever period the reader's budget
    // resets in. On the first of a month that leaves one day, which is how this was reported.
    const { choose, element, fixture } = setup({
      series: series(),
      periods: ['last-7-days', 'last-30-days', 'this-month'],
      period: 'last-30-days',
    });
    const asked: string[] = [];
    fixture.componentInstance.periodChange.subscribe((value) => asked.push(value));

    const options = Array.from(
      element.querySelectorAll<HTMLOptionElement>('[data-testid="chart-period-select"] option'),
    ).map((option) => option.textContent?.trim());
    expect(options).toEqual(['Last 7 days', 'Last 30 days', 'This month']);

    choose('chart-period-select', 'this-month');

    expect(asked).toEqual(['this-month']);
  });

  it('offers only the splits it was given', () => {
    // A use case's own page would otherwise offer to colour its traffic by use case, which draws one
    // band and calls it a comparison.
    const { element } = setup({ series: series(), splits: ['model', 'outcome'] });
    const options = Array.from(
      element.querySelectorAll<HTMLOptionElement>('[data-testid="chart-split-select"] option'),
    ).map((option) => option.value);

    expect(options).toEqual(['model', 'outcome']);
  });

  it('says the chart could not be loaded, in the backend’s own words, rather than drawing zeroes', () => {
    // A flat plot would state that nothing was used — a measurement nobody made (`FRD-603` FR-4).
    const { find, text } = setup({ series: null, reason: 'the gateway refused the request.' });

    expect(find('usage-chart-down')).not.toBeNull();
    expect(text()).toContain('the gateway refused the request.');
    expect(text()).toContain('unknown, not zero');
    expect(find('histogram')).toBeNull();
  });

  it('says it is loading rather than showing the failure while it waits', () => {
    const { find, text } = setup({ series: null, loading: true });

    expect(find('usage-chart-down')).toBeNull();
    expect(text()).toContain('Loading the chart…');
  });

  it('distinguishes a quiet period from one that did not arrive', () => {
    const { find, text } = setup({ series: series({ keys: [], points: [] }) });

    expect(find('usage-chart-down')).toBeNull();
    expect(text()).toContain('Nothing was used in this period');
  });

  it('keeps the day list in the document, inside a fold that is shut', () => {
    // A native `<details>`: shut so thirty days of rows do not push the rest of the page away, and
    // **still in the accessibility tree**, which an `sr-only` pivot behind a button was not. The
    // plot is `aria-hidden` and three of the seven hues sit below 3:1 against this surface, so these
    // rows are the only path to the figures for some readers.
    const { element } = setup({ series: series() });
    const fold = element.querySelector<HTMLDetailsElement>('details.days-fold')!;

    expect(fold).not.toBeNull();
    expect(fold.open).toBe(false);
    expect(element.querySelector('[data-testid="usage-days"]')).not.toBeNull();
  });

  it('says how much is in the fold before it is opened', () => {
    // A summary that says only "day by day" gives nobody a reason to open it; the count is the one
    // thing a reader can act on without opening anything.
    const { element } = setup({
      series: series({
        buckets: ['2026-09-01', '2026-09-02'],
        points: [point({ bucket: '2026-09-01' }), point({ bucket: '2026-09-02' })],
      }),
    });

    expect(element.querySelector('details.days-fold summary')?.textContent).toContain(
      '2 days with traffic',
    );
  });

  it('keeps the money in the day list whichever measure the picture draws', () => {
    // Switching the chart to Requests must not take the money off the list: the money is half the
    // sentence somebody opened this card to read.
    const { choose, element } = setup({
      series: series({ points: [point({ requests: 42, cost_nanos: 3_000_000_000 })] }),
    });

    choose('chart-metric-select', 'requests');

    expect(element.querySelector('[data-testid="usage-days"]')?.textContent).toContain('3.00');
  });

  it('says why a tail was folded, and where the rest of it can be read', () => {
    const { find, text } = setup({ series: series({ folded: true, keys: ['chat-1', '(other)'] }) });

    expect(find('chart-folded')).not.toBeNull();
    expect(text()).toContain('(other)');
  });

  it('warns that spend is a lower bound when any traffic was unpriced', () => {
    const { find } = setup({
      series: series({ points: [point({ cost_nanos: 0, unpriced_requests: 3 })] }),
    });

    expect(find('chart-unpriced')?.textContent).toContain('3 request(s)');
  });

  it('drops the unpriced caveat when the measure is not money', () => {
    // Unpriced traffic is fully counted in requests and tokens; the caveat is about the spend figure
    // alone, and a warning that is always there is one nobody reads.
    const { choose, find } = setup({
      series: series({ points: [point({ cost_nanos: 0, unpriced_requests: 3 })] }),
    });

    choose('chart-metric-select', 'requests');

    expect(find('chart-unpriced')).toBeNull();
  });

  it('says what it is about in its own heading', () => {
    // The default is the whole-installation reading. A use case's own page passes its own, because
    // the chart there is narrowed to the reader and sits above a card headed *What you used*.
    const { text } = setup({
      series: series(),
      heading: 'What you used, over time',
      subject: 'Your own requests in this use case, day by day',
    });

    expect(text()).toContain('What you used, over time');
    expect(text()).toContain('Your own requests in this use case');
    expect(text()).not.toContain('Usage over time');
  });

  it('says which axis the server settled on, which “automatic” only decides there', () => {
    const { find } = setup({ series: series({ granularity: 'hour' }), granularity: 'auto' });

    expect(find('usage-grain')?.textContent).toContain('hour');
  });
});
