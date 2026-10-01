import { SeriesPoint, UsageSeries } from '../../api/types/reporting';
import { displayAmount, displayCount } from '../money';

/**
 * Turning a usage series into something drawable (`FRD-626`).
 *
 * Kept out of the components on purpose: everything here is arithmetic with a right answer — a
 * share, a peak, which colour belongs to which band — and a component is the one place that is hard
 * to test a right answer in.
 */

/** Which measure the chart draws. Three, because they answer three different questions. */
export type Metric = 'cost' | 'requests' | 'tokens';

/**
 * The categorical hues, in the order bands take them.
 *
 * Validated as a set rather than chosen by eye: every adjacent pair is distinguishable under the
 * three common forms of colour-vision deficiency (worst adjacent ΔE 9.1, against a floor of 8) and
 * for full-colour vision (worst 19.6, floor 15). **The order is the mechanism**, not decoration —
 * re-ordering them re-opens the question. Three of them sit below 3:1 against white, which is why
 * every chart here also carries labels and a table of its own numbers; colour is never the only
 * channel.
 *
 * There are seven, and the gateway folds the eighth band onward into `(other)` for the same reason:
 * a generated hue is indistinguishable from one of these under CVD.
 *
 * Red is deliberately absent although the validated set has eight slots. `--aira-danger` is red
 * throughout this console, and a band that happens to land in slot eight would be read as the
 * failing one.
 */
export const SERIES_COLORS = [
  '#2a78d6', // blue
  '#eb6834', // orange
  '#1baf7a', // aqua
  '#eda100', // yellow
  '#e87ba4', // magenta
  '#008300', // green
  '#4a3aa7', // violet
] as const;

/** The band the gateway folds a long tail into. Must match `reporting.series.OTHER`. */
export const OTHER = '(other)';

/**
 * What `(other)` wears: a neutral, not a hue. It is not an entity, it is "the rest", and giving it
 * a categorical colour invites a reader to look for it in the table.
 */
export const OTHER_COLOR = '#6b7280';

/** One band of the chart: an entity, its colour, and what it did. */
export interface Band {
  key: string;
  color: string;
  /** The measure's raw value — nano-units for spend, a count otherwise. Never a formatted string. */
  value: number;
  /** The same value, written the way a reader reads it. */
  display: string;
  /** Percent of the whole this band is, 0–100. For a width; never for arithmetic. */
  share: number;
  /** Requests in this band whose cost is unknown — so a spend figure can say so. */
  unpriced: number;
  /**
   * Whether this band's spend is **entirely** unknown: every request in it was unpriced.
   *
   * Then `0.00` is not a smaller figure, it is a **wrong** one — and it is the figure a local
   * embedding model gets, because the runtime reports no tokens for anything to be priced from.
   * Spotted on the showcase: *"allminilm hat auch kosten von 0.0, was nicht so ganz gut passt."*
   */
  costUnknown: boolean;
}

/** One column of the histogram: a bucket, and the bands stacked in it. */
export interface Column {
  bucket: string;
  /** The axis label — short, because there may be thirty-one of them. */
  label: string;
  /** The full label, for the tooltip and the table, where there is room to be unambiguous. */
  title: string;
  total: number;
  display: string;
  /** Percent of the tallest column. The column's own height. */
  height: number;
  /** Biggest band first, so the stack reads the same way the legend does. */
  bands: Band[];
}

/** Everything a usage chart needs, with the arithmetic already done. */
export interface ChartModel {
  metric: Metric;
  granularity: UsageSeries['granularity'];
  split: UsageSeries['split'];
  columns: Column[];
  /** The bands over the whole period: the legend, and the composition blocks. */
  bands: Band[];
  total: number;
  display: string;
  /** The tallest column's value, which the y-axis is scaled to. */
  peak: number;
  peakDisplay: string;
  /** Whether a tail of small bands was folded into `(other)` by the gateway. */
  folded: boolean;
  /** Nothing happened in this period. Distinct from "the series did not arrive". */
  empty: boolean;
  /** Requests whose cost is unknown. */
  unpriced: number;
  /**
   * Of those, the ones where the upstream reported **no token usage** for anything to be priced
   * from — as opposed to a model that has no price on file (`FRD-626` FR-18).
   *
   * Two causes with two different remedies. Telling somebody to add a price for the first sends
   * them to a form that already has one.
   */
  unmetered: number;
}

/** What each measure is called, and the unit a reader needs beside it. */
export const METRIC_LABELS: Record<Metric, string> = {
  cost: 'Spend',
  requests: 'Requests',
  tokens: 'Tokens',
};

/**
 * What each measure counts — the same sentences the figures on the reporting screen carry, because
 * a chart is exactly where somebody starts reconciling against an invoice (`FRD-206`).
 */
export const METRIC_HELP: Record<Metric, string> = {
  cost: `Money, priced per model from the catalogue at the time of each request. Traffic on a model
      with no price on file contributes nothing here and is counted apart — the caveat below the
      chart appears whenever any of it is in the period, and the bars are then a lower bound.`,
  requests: `Every model call in the bucket, one per call, including the ones the gateway refused
      and the ones a pipeline step made on the caller's behalf. A refusal cost nothing and is still
      something that happened. An embedding batch is one call however many texts it carries — a
      request budget weighs the same batch as one per text, so the two figures differ on purpose.`,
  tokens: `Prompt and completion tokens together. Output is billed several times higher than input
      and prices differ more than tenfold between models, so this is a volume figure and the spend
      view is the cost one — a tall token bar is not necessarily the expensive day.`,
};

/** What a figure nobody measured looks like. Never `0.00`, which is a measurement. */
export const UNKNOWN = '—';

/**
 * Whether a spend figure is **entirely** unknown rather than small.
 *
 * Only for money: unpriced traffic is fully counted in requests and in tokens, and only its cost
 * could not be computed. Every request in the group has to be unpriced — a group with one priced
 * request has a figure, and that figure is a lower bound the caveat already explains.
 */
function unpricedThrough(metric: Metric, requests: number, unpriced: number): boolean {
  return metric === 'cost' && requests > 0 && unpriced === requests;
}

/** The raw value of one measure on one row. Spend stays in nano-units: an integer, never a float. */
function valueOf(row: SeriesPoint, metric: Metric): number {
  switch (metric) {
    case 'cost':
      return row.cost_nanos;
    case 'requests':
      return row.requests;
    default:
      return row.total_tokens;
  }
}

/** A measure written out. Money carries the no-non-zero-shown-as-zero rule; counts are grouped. */
export function formatMetric(value: number, metric: Metric): string {
  return metric === 'cost' ? displayAmount(value) : displayCount(value);
}

/** The colour of the band in position `index`. `(other)` is a neutral wherever it sits. */
export function colorFor(key: string, index: number): string {
  if (key === OTHER) return OTHER_COLOR;
  // Modulo as a last resort only: the gateway folds past seven bands, so this wraps for nobody.
  // Left in rather than left to throw, because a palette that runs out should draw a wrong colour
  // and not an empty chart.
  return SERIES_COLORS[index % SERIES_COLORS.length];
}

/** The two inks a label set *inside* a filled block may wear. */
export const INKS = ['#ffffff', '#000000'] as const;

/** WCAG relative luminance of an `#rrggbb` colour. */
export function luminance(hex: string): number {
  const channel = (offset: number): number => {
    const value = parseInt(hex.slice(offset, offset + 2), 16) / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5);
}

/** WCAG contrast ratio between two `#rrggbb` colours, 1 (identical) to 21 (black on white). */
export function contrast(one: string, other: string): number {
  const [light, dark] = [luminance(one), luminance(other)].sort((a, b) => b - a);
  return (light + 0.05) / (dark + 0.05);
}

/**
 * Which ink a label inside a block of `hex` wears: **whichever of the two has more contrast**.
 *
 * Computed, not assumed, and not from a lightness threshold either. A label is the one place text may
 * sit on a series colour, and a constant white fails outright on the lighter half of the palette —
 * 2.2:1 on the yellow. Picking the better of the two clears 4.5:1 on every colour here, which
 * `usage-series.spec.ts` asserts rather than leaving to this comment.
 */
export function inkOn(hex: string): string {
  const [white, black] = INKS;
  return contrast(hex, white) >= contrast(hex, black) ? white : black;
}

/**
 * The axis label for a bucket.
 *
 * Short by design — thirty-one of them share a line. The day of the month for a day bucket and the
 * hour for an hour one; `title` carries the unambiguous form for the tooltip and the table.
 */
function labelFor(bucket: string, granularity: UsageSeries['granularity']): string {
  if (granularity === 'hour') return `${bucket.slice(11, 13)}:00`;
  return bucket.slice(8, 10);
}

/** The unambiguous form of a bucket: what the tooltip and the table say. */
function titleFor(bucket: string, granularity: UsageSeries['granularity']): string {
  return granularity === 'hour' ? `${bucket.slice(0, 10)} ${bucket.slice(11, 13)}:00` : bucket;
}

/**
 * Build everything the chart draws from one series and one measure.
 *
 * Every bucket the gateway listed gets a column, **including the empty ones**: a histogram assembled
 * only from buckets that have rows puts Monday beside Friday and calls it a week.
 */
export function buildChart(series: UsageSeries, metric: Metric): ChartModel {
  const order = new Map(series.keys.map((key, index) => [key, index]));
  const perBucket = new Map<string, SeriesPoint[]>();
  const perKey = new Map<string, { value: number; requests: number; unpriced: number }>();

  for (const point of series.points) {
    const inBucket = perBucket.get(point.bucket) ?? [];
    inBucket.push(point);
    perBucket.set(point.bucket, inBucket);

    const running = perKey.get(point.key) ?? { value: 0, requests: 0, unpriced: 0 };
    perKey.set(point.key, {
      value: running.value + valueOf(point, metric),
      // Carried even when the measure on screen is not requests: whether a band's **spend** is
      // entirely unknown is a question about how many of its requests could be priced, and the
      // answer must not change when the picture does.
      requests: running.requests + point.requests,
      unpriced: running.unpriced + point.unpriced_requests,
    });
  }

  const total = [...perKey.values()].reduce((sum, entry) => sum + entry.value, 0);
  const unpriced = [...perKey.values()].reduce((sum, entry) => sum + entry.unpriced, 0);
  const unmetered = series.points.reduce((sum, point) => sum + (point.unmetered_requests ?? 0), 0);

  // Ranked by **this measure**, so switching to requests re-orders the blocks to what they now say.
  // `order` still decides the colour, so a band keeps its hue when the ranking changes — colour
  // follows the entity, never its position.
  const bands = series.keys
    .map((key, index) => {
      const entry = perKey.get(key) ?? { value: 0, requests: 0, unpriced: 0 };
      const allUnpriced = unpricedThrough(metric, entry.requests, entry.unpriced);
      return {
        key,
        color: colorFor(key, index),
        value: entry.value,
        // An em dash, not `0.00`: nothing here was measured (`LESSONS.md` §2, *unknown is not zero*).
        display: allUnpriced ? UNKNOWN : formatMetric(entry.value, metric),
        share: total ? (entry.value / total) * 100 : 0,
        unpriced: entry.unpriced,
        costUnknown: allUnpriced,
      };
    })
    .sort(byValueThenOrder(order));

  const totals = series.buckets.map((bucket) =>
    (perBucket.get(bucket) ?? []).reduce((sum, point) => sum + valueOf(point, metric), 0),
  );
  const peak = totals.reduce((most, value) => Math.max(most, value), 0);

  const columns = series.buckets.map((bucket, index) => {
    const points = perBucket.get(bucket) ?? [];
    const bucketTotal = totals[index];
    return {
      bucket,
      label: labelFor(bucket, series.granularity),
      title: titleFor(bucket, series.granularity),
      total: bucketTotal,
      display: formatMetric(bucketTotal, metric),
      height: peak ? (bucketTotal / peak) * 100 : 0,
      bands: points
        .map((point) => {
          const slot = order.get(point.key) ?? series.keys.length;
          const value = valueOf(point, metric);
          const allUnpriced = unpricedThrough(metric, point.requests, point.unpriced_requests);
          return {
            key: point.key,
            color: colorFor(point.key, slot),
            value,
            display: allUnpriced ? UNKNOWN : formatMetric(value, metric),
            share: bucketTotal ? (value / bucketTotal) * 100 : 0,
            unpriced: point.unpriced_requests,
            costUnknown: allUnpriced,
          };
        })
        // A zero-valued band is dropped from the stack rather than drawn at no height: a segment of
        // 0 % still costs its 2px separator and would show as a stray line at the top of the bar.
        .filter((band) => band.value > 0)
        .sort(byValueThenOrder(order)),
    };
  });

  return {
    metric,
    granularity: series.granularity,
    split: series.split,
    columns,
    bands,
    total,
    display: formatMetric(total, metric),
    peak,
    peakDisplay: formatMetric(peak, metric),
    folded: series.folded,
    empty: total === 0,
    unpriced,
    unmetered,
  };
}

/** One band of one day, with **every** measure — this is the view that answers "and what did it
 *  cost me", so it cannot be about one measure at a time. */
export interface DayBand {
  key: string;
  color: string;
  requests: number;
  promptTokens: number;
  completionTokens: number;
  cachedTokens: number;
  costNanos: number;
  cost: string;
  unpriced: number;
  /** Nothing in this row could be priced, so its spend is unknown rather than zero. */
  costUnknown: boolean;
  failed: number;
}

/** One day: what was used, by what, and what it cost. */
export interface Day {
  bucket: string;
  title: string;
  requests: number;
  promptTokens: number;
  completionTokens: number;
  costNanos: number;
  cost: string;
  unpriced: number;
  /** Nothing on this day could be priced at all. */
  costUnknown: boolean;
  bands: DayBand[];
}

/**
 * The period as a list of days, newest first — the reading the chart cannot give.
 *
 * A column says *how much*, against the tallest day. This says **what**, in the three measures at
 * once, for one day at a time: *on the twelfth I made fourteen calls on this model and four on that
 * one, and together they cost me this much.* Somebody reconciling a figure reads it here; somebody
 * looking for a trend reads the chart. They are two questions and the same rows.
 *
 * **Independent of which measure the chart draws**, on purpose. Switching the picture to Requests
 * must not take the money off this list, because the money is half the sentence.
 *
 * Days with nothing in them are left out. On the axis an empty day is a reading — the week has a
 * shape — and in a list it is a row that says nothing, thirty times over.
 */
export function buildDays(series: UsageSeries, metric: Metric = 'cost'): Day[] {
  const order = new Map(series.keys.map((key, index) => [key, index]));
  const byBucket = new Map<string, DayBand[]>();

  for (const point of series.points) {
    if (!point.requests && !point.total_tokens && !point.cost_nanos) continue;
    const bands = byBucket.get(point.bucket) ?? [];
    bands.push({
      key: point.key,
      color: colorFor(point.key, order.get(point.key) ?? series.keys.length),
      requests: point.requests,
      promptTokens: point.prompt_tokens,
      completionTokens: point.completion_tokens,
      cachedTokens: point.cached_input_tokens ?? 0,
      costNanos: point.cost_nanos,
      cost: unpricedThrough('cost', point.requests, point.unpriced_requests)
        ? UNKNOWN
        : displayAmount(point.cost_nanos),
      unpriced: point.unpriced_requests,
      costUnknown: unpricedThrough('cost', point.requests, point.unpriced_requests),
      failed: point.failed_requests,
    });
    byBucket.set(point.bucket, bands);
  }

  const sum = (bands: DayBand[], of: (band: DayBand) => number) =>
    bands.reduce((total, band) => total + of(band), 0);

  return (
    series.buckets
      .filter((bucket) => byBucket.has(bucket))
      // Newest first: the question is nearly always about a day somebody remembers, and the ones
      // they remember are the recent ones.
      .reverse()
      .map((bucket) => {
        const bands = (byBucket.get(bucket) ?? []).sort(byMeasureThenOrder(order, metric));
        const costNanos = sum(bands, (band) => band.costNanos);
        const requests = sum(bands, (band) => band.requests);
        const unpriced = sum(bands, (band) => band.unpriced);
        const costUnknown = unpricedThrough('cost', requests, unpriced);
        return {
          bucket,
          title: titleFor(bucket, series.granularity),
          requests,
          promptTokens: sum(bands, (band) => band.promptTokens),
          completionTokens: sum(bands, (band) => band.completionTokens),
          costNanos,
          cost: costUnknown ? UNKNOWN : displayAmount(costNanos),
          unpriced,
          costUnknown,
          bands,
        };
      })
  );
}

/** Within a day, the band the reader is looking at first — ranked by the measure on screen. */
function byMeasureThenOrder(order: Map<string, number>, metric: Metric) {
  const of = (band: DayBand) =>
    metric === 'cost'
      ? band.costNanos
      : metric === 'requests'
        ? band.requests
        : band.promptTokens + band.completionTokens;
  return (left: DayBand, right: DayBand): number =>
    of(right) - of(left) ||
    (order.get(left.key) ?? Number.MAX_SAFE_INTEGER) -
      (order.get(right.key) ?? Number.MAX_SAFE_INTEGER);
}

/**
 * Biggest first, and **the band order breaks the tie** rather than the name or the input order.
 *
 * With one measure at zero everywhere — spend in a period where nothing has a price — every
 * comparison is equal, and a sort left to the engine would hand back a different stacking order for
 * two draws of the same data.
 */
function byValueThenOrder(order: Map<string, number>) {
  return (left: Band, right: Band): number =>
    right.value - left.value ||
    (order.get(left.key) ?? Number.MAX_SAFE_INTEGER) -
      (order.get(right.key) ?? Number.MAX_SAFE_INTEGER);
}
