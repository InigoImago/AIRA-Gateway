import { Component, computed, input, signal } from '@angular/core';
import { amountAt, displayCount } from '../money';
import { ChartModel, formatMetric } from './usage-series';

/**
 * How many columns may carry an axis label before they start being thinned out.
 *
 * Thirty-one days fit; a month of hours does not, and overlapping labels are worse than every third
 * one — a reader can count the gaps.
 */
const LABELS_FIT = 32;

/** Where the gridlines sit, as fractions of the peak. The baseline needs no label of its own. */
const GRID_FRACTIONS = [1, 0.75, 0.5, 0.25];

/** The precisions a money scale may use, coarsest first. */
const SCALE_PLACES = [2, 4, 6, 9];

/**
 * One precision for the whole money axis: the first that tells every tick apart.
 *
 * Refining each tick on its own is what `displayAmount` does, and it is right for a single figure
 * and wrong for a scale — on the showcase it printed `0.0002 / 0.0001 / 0.0001 / 0.000041`, an axis
 * with two identical marks at different heights.
 */
function scaleOf(values: number[]): string[] {
  for (const places of SCALE_PLACES) {
    const labels = values.map((value) => amountAt(value, places));
    if (new Set(labels).size === labels.length) return labels;
  }
  return values.map((value) => amountAt(value, SCALE_PLACES[SCALE_PLACES.length - 1]));
}

/**
 * The period as stacked columns: one per bucket, each split into its bands (`FRD-626`).
 *
 * **Every bucket of the window has a column**, including the empty ones — the gateway sends the axis
 * for exactly that reason. A chart assembled only from buckets that have rows puts Monday beside
 * Friday and calls it a week, and a quiet Sunday is a reading.
 *
 * Built from `div`s and percentages rather than SVG: the browser's own layout handles a chart that
 * has to work at 390px, and every segment is then a real element a test can find and a pointer can
 * hit. The plot carries `aria-hidden` and the numbers live in a table the parent always renders, so
 * nothing here is the only way to the data.
 *
 * Hover is on the **column**, not the segment: a stacked bar is read by comparing its parts, so the
 * readout lists all of them at once, and one target per bucket is a target somebody can hit. The
 * readout sits under the plot rather than over it — see the template for why that is a correction
 * and not a preference.
 */
@Component({
  selector: 'app-usage-histogram',
  templateUrl: './usage-histogram.html',
})
export class UsageHistogram {
  readonly model = input.required<ChartModel>();
  /** Shown instead of a flat plot when nothing happened in the period. */
  readonly emptyText = input('No traffic in this period.');

  /** Which column the pointer is on, by bucket — not by index, because the list changes under it. */
  protected readonly hovered = signal<string | null>(null);

  protected readonly column = computed(() =>
    this.model().columns.find((candidate) => candidate.bucket === this.hovered()),
  );

  /**
   * Every how-manyth column gets an axis label: one where they fit, fewer where they would collide.
   *
   * From the count rather than from a measurement — a measured label means a layout read on every
   * render, and this answer only has to be roughly right.
   */
  protected readonly labelEvery = computed(() =>
    Math.max(1, Math.ceil(this.model().columns.length / LABELS_FIT)),
  );

  /**
   * The gridlines and what each one is worth.
   *
   * They carry the values that are not directly labelled, which here is all of them: a number on top
   * of thirty-one columns is noise, and the tooltip and the table have the exact figures.
   */
  protected readonly gridlines = computed(() => {
    const model = this.model();
    // Counts are rounded to a whole unit: "7.75 requests" is not a tick a reader trusts. Spend is
    // not, because its interesting magnitudes are below one unit of the currency.
    const values = GRID_FRACTIONS.map((fraction) =>
      model.metric === 'cost' ? model.peak * fraction : Math.round(model.peak * fraction),
    );
    const labels =
      model.metric === 'cost' ? scaleOf(values) : values.map((value) => displayCount(value));

    // **A repeated label is worse than a missing one**, and a small peak produces them whatever the
    // precision: four ticks over two requests are 2, 2, 1, 1. The line stays — it is reference —
    // and the second `1` goes.
    const seen = new Set<string>();
    return GRID_FRACTIONS.map((fraction, index) => {
      const label = labels[index];
      const repeated = seen.has(label);
      seen.add(label);
      return { at: fraction * 100, label: repeated ? '' : label };
    });
  });

  protected labelled(index: number): boolean {
    return index % this.labelEvery() === 0;
  }
}
