import { Component, computed, effect, input } from '@angular/core';
import { ReportRow } from '../../core/api/models';
import { InfoHint } from '../../core/ui/info-hint';
import { TablePager } from '../../core/ui/table-pager';
import { TableView } from '../../core/ui/table-view';

/**
 * One breakdown of a report — by use case, model, member or outcome (FRD-601).
 *
 * The breakdowns share their columns, so they are one panel: separate tables drift apart. The share
 * bar compares in **nano-units**, so no monetary amount passes through a float (`FRD-403`).
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

  /** Searched and paged: a breakdown by member can run to hundreds of rows. */
  protected readonly view = new TableView<ReportRow>(this.rows, (row) => row.key);

  /**
   * The largest spend, so every bar shares one scale. Over **all** rows, not the page: a per-page
   * scale would make page three's smallest row look like the biggest spender.
   */
  protected readonly peak = computed(() =>
    this.rows().reduce((most, row) => Math.max(most, row.cost_nanos), 0),
  );

  constructor() {
    // A new period or breakdown is a different list; staying on page 4 would read as "no data".
    effect(() => {
      this.rows();
      this.view.page.set(1);
    });
  }

  protected share(row: ReportRow): number {
    const peak = this.peak();
    return peak ? Math.round((row.cost_nanos / peak) * 100) : 0;
  }

  /** Whether a row's spend is a lower bound: unpriced requests are unknown, not zero. */
  protected incomplete(row: ReportRow): boolean {
    return row.unpriced_requests > 0 && !this.unknown(row);
  }

  /**
   * Whether the row's spend is **entirely** unknown — every request in it was unpriced.
   *
   * Then `0.00` is not a smaller figure, it is a wrong one: the row is a model nobody could price,
   * not a model that was free. Found on the running showcase, where the local embedding model
   * reports no tokens and this column read `0.00` beside a price that is on file.
   */
  protected unknown(row: ReportRow): boolean {
    return row.requests > 0 && row.unpriced_requests === row.requests;
  }

  /** What each row's spend cell says. An em dash is a measurement nobody made. */
  protected spend(row: ReportRow): string {
    return this.unknown(row) ? '—' : row.cost;
  }
}
