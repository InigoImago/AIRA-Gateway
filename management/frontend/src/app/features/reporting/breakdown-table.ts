import { Component, computed, input } from '@angular/core';
import { ReportRow } from '../../core/api/models';

/**
 * One breakdown of a report — by use case, by model or by member (FRD-601).
 *
 * The three breakdowns carry the same columns and differ only in what the first one is called,
 * so they are one panel used three times. Writing the table out three times is how the model
 * table ends up with a column the member table quietly lost.
 *
 * The share bar compares in **nano-units**: the integer is here precisely so a proportion can be
 * computed without a monetary amount passing through a float (`FRD-403`).
 */
@Component({
  selector: 'app-breakdown-table',
  templateUrl: './breakdown-table.html',
})
export class BreakdownTable {
  /** What the first column is: "Use case", "Model", "Member". */
  readonly label = input.required<string>();
  readonly rows = input.required<ReportRow[]>();
  /** Shown when there is nothing, in the terms of this particular breakdown. */
  readonly emptyText = input('Nothing in this period.');

  /** The largest spend in the breakdown, so every bar is drawn against the same scale. */
  protected readonly peak = computed(() =>
    this.rows().reduce((most, row) => Math.max(most, row.cost_nanos), 0),
  );

  protected share(row: ReportRow): number {
    const peak = this.peak();
    return peak ? Math.round((row.cost_nanos / peak) * 100) : 0;
  }

  /**
   * Whether a row's spend figure is incomplete. A row with unpriced requests has a cost that is
   * *at least* what it says — omitting the caveat would let it read as the whole story.
   */
  protected incomplete(row: ReportRow): boolean {
    return row.unpriced_requests > 0;
  }
}
