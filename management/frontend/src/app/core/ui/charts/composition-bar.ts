import { Component, computed, input } from '@angular/core';
import { Band, inkOn } from './usage-series';

/** Below this share, a block has no room for even a short name and gets no inline label. */
const LABEL_THRESHOLD = 12;

/** Below this share, a block would be a hairline; it is widened so it stays clickable and visible. */
const MIN_WIDTH = 0.8;

/**
 * The whole period as one row of blocks, one per band, width proportional to its share (`FRD-626`).
 *
 * **A length, not an area.** A treemap was the other candidate and reads more like "blocks", but a
 * reader compares two lengths accurately and two areas badly — and the names here are model names,
 * which fit along a bar and not inside a tile.
 *
 * It answers one question the histogram cannot: *what is the shape of the whole*. The histogram
 * answers *when*, and a stack in it is scaled against the tallest day, so the quiet days say nothing
 * about proportion.
 *
 * The blocks are separated by a 2px gap **in the surface colour**, never a border: a stroke adds ink
 * that is not data, and two neighbouring hues read as distinct because of the gap.
 */
@Component({
  selector: 'app-composition-bar',
  templateUrl: './composition-bar.html',
})
export class CompositionBar {
  readonly bands = input.required<Band[]>();
  /** What one band is — "model", "use case" — for the accessible description. */
  readonly noun = input('band');
  /** Shown when every band is zero: a bar of nothing is not a reading. */
  readonly emptyText = input('Nothing in this period.');

  /** Only the bands that actually have something in them. A 0 % block is a stray line. */
  protected readonly shown = computed(() => this.bands().filter((band) => band.value > 0));

  protected readonly empty = computed(() => this.shown().length === 0);

  /**
   * How wide a block is drawn, as a percentage.
   *
   * Floored, so a band that is 0.02 % of the month is still a visible sliver somebody can point at
   * — with the honest consequence stated rather than hidden: the widths then no longer sum to
   * exactly 100, which is why the share is written beside each name as well.
   */
  protected width(band: Band): number {
    return Math.max(MIN_WIDTH, band.share);
  }

  /** Whether a block has room for its name inside it. Measured in share, not in characters. */
  protected labelled(band: Band): boolean {
    return band.share >= LABEL_THRESHOLD;
  }

  /**
   * The ink a name set inside a block wears — white or black, whichever has more contrast on that
   * block's fill. The one place text may wear a series colour is on top of it, and then only if
   * which ink it gets is computed (`usage-series.inkOn`).
   */
  protected ink(band: Band): string {
    return inkOn(band.color);
  }

  /** The share as a reader reads it. One decimal below 10 %, because 0 % is not a reading. */
  protected percent(band: Band): string {
    return band.share >= 10 ? `${Math.round(band.share)}%` : `${band.share.toFixed(1)}%`;
  }
}
