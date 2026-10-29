import { SeriesPoint, UsageSeries } from '../../api/types/reporting';
import {
  INKS,
  OTHER,
  OTHER_COLOR,
  SERIES_COLORS,
  buildChart,
  colorFor,
  contrast,
  inkOn,
} from './usage-series';

/**
 * The arithmetic behind the usage chart (`FRD-626`).
 *
 * Every property here is one a drawing would hide: a share computed against the wrong denominator
 * looks like a plausible chart, and so does one that quietly dropped the empty days.
 */

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
    buckets: ['2026-09-01', '2026-09-02', '2026-09-03'],
    keys: ['chat-1'],
    folded: false,
    points: [point()],
    ...over,
  };
}

describe('buildChart', () => {
  it('draws a column for every bucket, including the ones with nothing in them', () => {
    // A chart assembled only from buckets that have rows puts Monday beside Friday and calls it a
    // week. The quiet day is a reading, and this is the test that keeps it one.
    const chart = buildChart(series(), 'cost');

    expect(chart.columns.map((column) => column.bucket)).toEqual([
      '2026-09-01',
      '2026-09-02',
      '2026-09-03',
    ]);
    expect(chart.columns[1].total).toBe(0);
    expect(chart.columns[1].height).toBe(0);
    expect(chart.columns[1].bands).toEqual([]);
  });

  it('scales every column against the tallest one', () => {
    const chart = buildChart(
      series({
        points: [
          point({ bucket: '2026-09-01', cost_nanos: 4_000_000_000 }),
          point({ bucket: '2026-09-02', cost_nanos: 1_000_000_000 }),
        ],
      }),
      'cost',
    );

    expect(chart.columns[0].height).toBe(100);
    expect(chart.columns[1].height).toBe(25);
    expect(chart.peak).toBe(4_000_000_000);
  });

  it('sizes a segment against its own column, not against the peak', () => {
    // Two denominators, one chart, and swapping them produces stacks that do not fill their bars —
    // which looks like missing data rather than like a bug.
    const chart = buildChart(
      series({
        keys: ['chat-1', 'embed-1'],
        points: [
          point({ bucket: '2026-09-01', key: 'chat-1', cost_nanos: 3_000_000_000 }),
          point({ bucket: '2026-09-01', key: 'embed-1', cost_nanos: 1_000_000_000 }),
          point({ bucket: '2026-09-02', key: 'chat-1', cost_nanos: 1_000_000_000 }),
        ],
      }),
      'cost',
    );

    expect(chart.columns[0].bands.map((band) => band.share)).toEqual([75, 25]);
    // The one-band column is full of its single band although it is a quarter as tall.
    expect(chart.columns[1].bands[0].share).toBe(100);
    expect(chart.columns[1].height).toBe(25);
  });

  it('switches which measure it draws without asking the server again', () => {
    const rows = series({
      keys: ['chat-1', 'embed-1'],
      points: [
        point({ key: 'chat-1', cost_nanos: 9_000_000_000, requests: 1, total_tokens: 10 }),
        point({ key: 'embed-1', cost_nanos: 1_000_000_000, requests: 40, total_tokens: 400 }),
      ],
    });

    expect(buildChart(rows, 'cost').bands.map((band) => band.key)).toEqual(['chat-1', 'embed-1']);
    // The cheap model made most of the requests, so the ranking inverts — which is the comparison
    // the switch exists to make.
    expect(buildChart(rows, 'requests').bands.map((band) => band.key)).toEqual([
      'embed-1',
      'chat-1',
    ]);
    expect(buildChart(rows, 'tokens').total).toBe(410);
  });

  it('keeps a band on its own colour when the ranking changes', () => {
    // Colour follows the entity, never its position. Otherwise switching the measure repaints the
    // chart and a reader comparing two views is comparing two different legends.
    const rows = series({
      keys: ['chat-1', 'embed-1'],
      points: [
        point({ key: 'chat-1', cost_nanos: 9_000_000_000, requests: 1 }),
        point({ key: 'embed-1', cost_nanos: 1_000_000_000, requests: 40 }),
      ],
    });

    const byCost = new Map(buildChart(rows, 'cost').bands.map((b) => [b.key, b.color]));
    const byRequests = new Map(buildChart(rows, 'requests').bands.map((b) => [b.key, b.color]));

    expect(byRequests.get('chat-1')).toBe(byCost.get('chat-1'));
    expect(byRequests.get('embed-1')).toBe(byCost.get('embed-1'));
  });

  it('writes spend the way the server writes it, down to the fraction of a cent', () => {
    const chart = buildChart(series({ points: [point({ cost_nanos: 3_674_900 })] }), 'cost');

    expect(chart.display).toBe('0.0037');
    expect(chart.columns[0].display).toBe('0.0037');
  });

  it('reports a genuinely quiet period as empty rather than as a flat plot', () => {
    const chart = buildChart(series({ points: [] }), 'cost');

    expect(chart.empty).toBe(true);
    expect(chart.total).toBe(0);
    // Still three columns: the window is a fact even when nothing happened in it.
    expect(chart.columns).toHaveLength(3);
  });

  it('does not call a period empty because its spend is unknown', () => {
    // Forty requests on a model with no price on file are forty requests. "Nothing was used" is
    // decided by what was asked, never by the measure on screen — and the money that could not be
    // computed is unknown, period and column alike, not `0.00`.
    const chart = buildChart(
      series({ points: [point({ requests: 40, cost_nanos: 0, unpriced_requests: 40 })] }),
      'cost',
    );

    expect(chart.empty).toBe(false);
    expect(chart.costUnknown).toBe(true);
    expect(chart.display).toBe('—');
    expect(chart.columns[0].costUnknown).toBe(true);
    expect(chart.columns[0].display).toBe('—');
    // The empty columns beside it are still quiet days, not unknown ones.
    expect(chart.columns[1].costUnknown).toBe(false);
  });

  it('keeps a figure for a period that could be priced in part, and for the other measures', () => {
    const partly = buildChart(
      series({
        points: [point({ requests: 5, cost_nanos: 1_000_000_000, unpriced_requests: 3 })],
      }),
      'cost',
    );
    expect(partly.costUnknown).toBe(false);
    expect(partly.display).toBe('1.00');

    // Unpriced traffic is fully counted in requests; only its money is missing.
    const counted = buildChart(
      series({ points: [point({ requests: 40, cost_nanos: 0, unpriced_requests: 40 })] }),
      'requests',
    );
    expect(counted.costUnknown).toBe(false);
    expect(counted.display).toBe('40');
  });

  it('leaves a zero-valued band out of the stack', () => {
    // Drawn, it is a 2px line at the top of the bar that belongs to no band — and it would carry its
    // own separator, so it costs twice what it shows.
    const chart = buildChart(
      series({
        keys: ['chat-1', 'free-1'],
        points: [point({ key: 'chat-1' }), point({ key: 'free-1', cost_nanos: 0 })],
      }),
      'cost',
    );

    expect(chart.columns[0].bands.map((band) => band.key)).toEqual(['chat-1']);
    // …and it is still on the legend, with its figure, because it is a band that ran.
    expect(chart.bands.map((band) => band.key)).toContain('free-1');
  });

  it('carries unpriced requests so a spend figure can admit it is a lower bound', () => {
    // Six requests, four of them unpriced: never more unpriced than requests, which the gateway
    // cannot produce and a fixture should not either.
    const chart = buildChart(
      series({ points: [point({ requests: 6, cost_nanos: 1_000_000_000, unpriced_requests: 4 })] }),
      'cost',
    );

    expect(chart.unpriced).toBe(4);
  });

  it('keeps the band order stable when every value is zero', () => {
    // Spend in a period where no model has a price: every comparison is equal, and a sort left to
    // the engine hands back a different stacking order for two draws of the same data.
    const rows = series({
      keys: ['first', 'second', 'third'],
      points: [
        point({ key: 'third', cost_nanos: 0 }),
        point({ key: 'first', cost_nanos: 0 }),
        point({ key: 'second', cost_nanos: 0 }),
      ],
    });

    expect(buildChart(rows, 'cost').bands.map((band) => band.key)).toEqual([
      'first',
      'second',
      'third',
    ]);
  });

  it('labels a day column with its day and an hour column with its hour, on the UTC clock', () => {
    // The gateway buckets in UTC. A reader in Berlin who reads "14:00" as their own afternoon is two
    // hours off, so the full form — the tooltip, the day list — says which clock it is.
    const days = buildChart(series(), 'cost');
    const hours = buildChart(
      series({
        granularity: 'hour',
        buckets: ['2026-09-01T00', '2026-09-01T14'],
        points: [point({ bucket: '2026-09-01T14' })],
      }),
      'cost',
    );

    expect(days.columns[0].label).toBe('01');
    expect(days.columns[0].title).toBe('2026-09-01');
    expect(hours.columns[1].label).toBe('14:00');
    expect(hours.columns[1].title).toBe('2026-09-01 14:00 UTC');
  });

  it('keeps an hour’s offset on another clock, so the hour that happens twice reads as two', () => {
    // The night the clocks go back in Berlin (`FRD-629`): 02:00 summer time, then 02:00 winter time.
    const twice = ['2026-10-25T02+02:00', '2026-10-25T02+01:00'];
    const hours = buildChart(
      series({
        granularity: 'hour',
        time_zone: 'Europe/Berlin',
        buckets: twice,
        points: twice.map((bucket) => point({ bucket })),
      }),
      'cost',
    );

    expect(hours.columns.map((column) => column.label)).toEqual(['02:00', '02:00']);
    expect(hours.columns.map((column) => column.title)).toEqual([
      '2026-10-25 02:00 UTC+02:00',
      '2026-10-25 02:00 UTC+01:00',
    ]);
  });
});

describe('the palette', () => {
  it('gives the folded tail a neutral rather than a hue', () => {
    // `(other)` is not an entity, it is "the rest". A categorical colour invites a reader to go and
    // look for it in the table.
    expect(colorFor(OTHER, 0)).toBe(OTHER_COLOR);
    expect(colorFor('chat-1', 0)).toBe(SERIES_COLORS[0]);
  });

  it('assigns hues in a fixed order', () => {
    expect(colorFor('a', 0)).toBe(SERIES_COLORS[0]);
    expect(colorFor('b', 1)).toBe(SERIES_COLORS[1]);
    expect(colorFor('c', 6)).toBe(SERIES_COLORS[6]);
  });

  it('holds seven hues, the number of bands the gateway keeps before it folds the rest', () => {
    // A pin, not a comparison: `DEFAULT_TOP = 7` and `OTHER = "(other)"` live in the gateway's
    // `reporting/series.py`, which a browser test cannot read. If the gateway changes either, this
    // stays green while a band draws a wrapped colour or `(other)` is drawn as an entity — only a
    // test that reads both planes can see that.
    expect(SERIES_COLORS).toHaveLength(7);
    expect(OTHER).toBe('(other)');
  });

  it('puts a legible ink on every colour a label can sit on', () => {
    // The computable half of the design, so it is computed. A constant white fails outright on the
    // lighter hues — 2.2:1 on the yellow — and a label inside a block is the one place text wears a
    // series colour.
    for (const color of [...SERIES_COLORS, OTHER_COLOR]) {
      expect(contrast(color, inkOn(color))).toBeGreaterThanOrEqual(4.5);
    }
  });

  it('picks between the two inks rather than always the same one', () => {
    // Both arms exercised: a function that returned white unconditionally would pass the contrast
    // test above only because the gate was computed from its own answer.
    const chosen = new Set([...SERIES_COLORS].map(inkOn));

    expect(chosen).toEqual(new Set(INKS));
  });
});
