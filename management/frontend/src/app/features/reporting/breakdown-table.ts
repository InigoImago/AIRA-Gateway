import { Component, computed, effect, input } from '@angular/core';
import { ReportRow } from '../../core/api/models';
import { InfoHint } from '../../core/ui/info-hint';
import { TablePager } from '../../core/ui/table-pager';
import { TableView } from '../../core/ui/table-view';

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
  imports: [InfoHint, TablePager],
  templateUrl: './breakdown-table.html',
})
export class BreakdownTable {
  /** What the first column is: "Use case", "Model", "Member". */
  readonly label = input.required<string>();
  readonly rows = input.required<ReportRow[]>();
  /** Shown when there is nothing, in the terms of this particular breakdown. */
  readonly emptyText = input('Nothing in this period.');
  /** What one row is, in plural — read out by the pager: "1–25 of 80 use cases". */
  readonly noun = input('rows');

  /**
   * Searched and paged. A breakdown by member on an installation with four hundred people is a
   * table nobody scrolls; the answer is a way to ask for the row you want, not a taller page.
   */
  protected readonly view = new TableView<ReportRow>(this.rows, (row) => row.key);

  /**
   * The largest spend in the breakdown, so every bar is drawn against the same scale.
   *
   * Over **all** rows, not the page: a bar rescaled per page would make the smallest row on page
   * three look like the biggest spender in the report.
   */
  protected readonly peak = computed(() =>
    this.rows().reduce((most, row) => Math.max(most, row.cost_nanos), 0),
  );

  constructor() {
    // A new period, or a different breakdown, is a different list — staying on page 4 of it shows
    // an empty table that reads as "no data".
    effect(() => {
      this.rows();
      this.view.page.set(1);
    });
  }

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
