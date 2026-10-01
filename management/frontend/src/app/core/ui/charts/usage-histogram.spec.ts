import { TestBed } from '@angular/core/testing';
import { SeriesPoint, UsageSeries } from '../../api/types/reporting';
import { UsageHistogram } from './usage-histogram';
import { Metric, buildChart } from './usage-series';

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

function setup(series: Partial<UsageSeries> = {}, metric: Metric = 'cost') {
  const full: UsageSeries = {
    granularity: 'day',
    split: 'model',
    buckets: ['2026-09-01', '2026-09-02'],
    keys: ['chat-1'],
    folded: false,
    points: [point()],
    ...series,
  };
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({ imports: [UsageHistogram] });
  const fixture = TestBed.createComponent(UsageHistogram);
  fixture.componentRef.setInput('model', buildChart(full, metric));
  fixture.detectChanges();
  const element = fixture.nativeElement as HTMLElement;
  return {
    fixture,
    element,
    text: () => element.textContent ?? '',
    columns: () => Array.from(element.querySelectorAll<HTMLElement>('.histogram__col')),
    stacks: () => Array.from(element.querySelectorAll<HTMLElement>('.histogram__stack')),
    labels: () =>
      Array.from(element.querySelectorAll<HTMLElement>('.histogram__label')).map((label) =>
        (label.textContent ?? '').trim(),
      ),
    hover: (index: number) => {
      element
        .querySelectorAll<HTMLElement>('.histogram__col')
        [index].dispatchEvent(new MouseEvent('mouseenter'));
      fixture.detectChanges();
    },
  };
}

describe('UsageHistogram', () => {
  it('draws a column for every bucket, including the empty ones', () => {
    const { columns, stacks } = setup();

    expect(columns()).toHaveLength(2);
    expect(stacks()[0].style.height).toBe('100%');
    expect(stacks()[1].style.height).toBe('0%');
  });

  it('stacks a bucket into one segment per band', () => {
    const { element } = setup({
      keys: ['chat-1', 'embed-1'],
      points: [
        point({ key: 'chat-1', cost_nanos: 3_000_000_000 }),
        point({ key: 'embed-1', cost_nanos: 1_000_000_000 }),
      ],
    });
    const segments = Array.from(
      element.querySelectorAll<HTMLElement>('[data-testid^="seg-2026-09-01"]'),
    );

    expect(segments.map((segment) => segment.style.height)).toEqual(['75%', '25%']);
  });

  it('shows every band of the hovered bucket at once, not only the one under the pointer', () => {
    // A stacked column is read by comparing its parts, so the readout is per column. It is also the
    // bigger hit target, which matters most for the quiet days.
    const { hover, element } = setup({
      keys: ['chat-1', 'embed-1'],
      points: [
        point({ key: 'chat-1', cost_nanos: 3_000_000_000 }),
        point({ key: 'embed-1', cost_nanos: 1_000_000_000 }),
      ],
    });
    hover(0);
    const tip = element.querySelector<HTMLElement>('[data-testid="histogram-tip"]');

    expect(tip?.textContent).toContain('2026-09-01');
    expect(tip?.textContent).toContain('chat-1');
    expect(tip?.textContent).toContain('embed-1');
    expect(tip?.textContent).toContain('4.00');
  });

  it('says what the readout is for until something is hovered', () => {
    // Rather than a blank strip that shifts the page when it fills. The hint also says the plot is
    // interactive, which a static picture of bars does not.
    const { element, text } = setup();

    expect(element.querySelector('[data-testid="histogram-tip"]')).toBeNull();
    expect(text()).toContain('Point at a column');
  });

  it('scales the y-axis to the tallest column', () => {
    const { text } = setup({
      points: [
        point({ bucket: '2026-09-01', cost_nanos: 4_000_000_000 }),
        point({ bucket: '2026-09-02', cost_nanos: 1_000_000_000 }),
      ],
    });

    expect(text()).toContain('4.00');
    expect(text()).toContain('2.00');
    expect(text()).toContain('1.00');
  });

  it('gives a money axis one precision, so no two ticks read alike', () => {
    // Seen on the running showcase: a peak of 0.00019 printed `0.0002 / 0.0001 / 0.0001 / 0.000041`,
    // because each tick was refined on its own until it stopped reading as zero. That is the right
    // rule for one figure and the wrong one for a scale.
    const { text } = setup({ points: [point({ cost_nanos: 190_000 })] });
    const ticks = Array.from((text().match(/0\.\d+/g) ?? []) as string[]);

    expect(new Set(ticks).size).toBe(ticks.length);
  });

  it('drops a tick label that repeats one already shown', () => {
    // Four ticks over two requests are 2, 2, 1, 1 whatever the precision, because the values really
    // are equal once rounded. The line stays — it is reference — and the repeat goes.
    const { element } = setup({ points: [point({ requests: 2 })] }, 'requests');
    const written = Array.from(element.querySelectorAll('.histogram__tick'))
      .map((tick) => (tick.textContent ?? '').trim())
      .filter(Boolean);

    expect(new Set(written).size).toBe(written.length);
    expect(element.querySelectorAll('.histogram__grid')).toHaveLength(4);
  });

  it('rounds a count tick to a whole unit', () => {
    // "7.75 requests" is not a tick a reader trusts; a fraction of a cent is.
    const { text } = setup({ points: [point({ requests: 7 })] }, 'requests');

    expect(text()).toContain('7');
    expect(text()).not.toContain('5.25');
  });

  it('labels every column where they fit', () => {
    const { labels } = setup();

    expect(labels()).toEqual(['01', '02']);
  });

  it('thins the labels out rather than letting them overlap', () => {
    // A month of hours is 744 columns. Overlapping labels are worse than every nth one: a reader can
    // count the gaps, and cannot un-overlap text.
    const buckets = Array.from(
      { length: 96 },
      (_, hour) => `2026-09-0${1 + Math.floor(hour / 24)}T${String(hour % 24).padStart(2, '0')}`,
    );
    const { labels } = setup({
      granularity: 'hour',
      buckets,
      points: [point({ bucket: buckets[40] })],
    });
    const written = labels().filter((label) => label !== '');

    expect(labels()).toHaveLength(96);
    expect(written.length).toBeLessThan(40);
    expect(written.length).toBeGreaterThan(0);
  });

  it('says a period was quiet rather than drawing a flat plot', () => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({ imports: [UsageHistogram] });
    const fixture = TestBed.createComponent(UsageHistogram);
    fixture.componentRef.setInput(
      'model',
      buildChart(
        {
          granularity: 'day',
          split: 'model',
          buckets: ['2026-09-01'],
          keys: [],
          folded: false,
          points: [],
        },
        'cost',
      ),
    );
    fixture.componentRef.setInput('emptyText', 'Nothing was used in this period.');
    fixture.detectChanges();
    const element = fixture.nativeElement as HTMLElement;

    expect(element.querySelector('[data-testid="histogram"]')).toBeNull();
    expect(element.textContent).toContain('Nothing was used in this period.');
  });

  it('hides the plot from assistive technology, whose reader gets the table instead', () => {
    const { element } = setup();

    expect(element.querySelector('[data-testid="histogram"]')?.getAttribute('aria-hidden')).toBe(
      'true',
    );
  });
});
