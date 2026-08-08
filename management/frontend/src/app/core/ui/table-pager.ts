import { Component, input } from '@angular/core';
import { PagedView } from './table-view';

/**
 * The strip under a table: what you are looking at, and how to see the rest.
 *
 * It renders **even on a single page**, showing "8 of 8". That is deliberate: a control that
 * appears once a list grows past a threshold teaches nobody it exists, and the count is worth
 * having anyway — a reader who cannot see a total cannot tell a filtered list from a complete one.
 */
@Component({
  selector: 'app-table-pager',
  template: `
    <div class="pager" [attr.data-testid]="testid()">
      <span class="pager__count">
        @if (view().matches().length === 0) {
          No {{ noun() }}
        } @else {
          {{ view().firstShown() }}–{{ view().lastShown() }} of {{ view().matches().length }}
          {{ noun() }}
          @if (view().filtered()) {
            <span class="muted">(filtered)</span>
          }
        }
      </span>

      @if (view().pageCount() > 1) {
        <span class="pager__controls">
          <button
            type="button"
            class="btn btn--sm"
            [disabled]="view().current() === 1"
            (click)="view().previous()"
            data-testid="pager-previous"
          >
            ← Previous
          </button>
          <span class="pager__position">
            Page {{ view().current() }} of {{ view().pageCount() }}
          </span>
          <button
            type="button"
            class="btn btn--sm"
            [disabled]="view().current() === view().pageCount()"
            (click)="view().next()"
            data-testid="pager-next"
          >
            Next →
          </button>
        </span>
      }
    </div>
  `,
})
export class TablePager {
  readonly view = input.required<PagedView>();
  /** What the rows are, in plural — "use cases", "models". Read out in the count. */
  readonly noun = input.required<string>();
  readonly testid = input<string>('pager');
}
