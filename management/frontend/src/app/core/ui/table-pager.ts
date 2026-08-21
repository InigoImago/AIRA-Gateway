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
        <!--
          **The position reads before the buttons, not between them.**

          "Previous / Page 1 of 3 / Next" is the conventional arrangement and it puts a
          variable-width label **between two targets**. The group is pinned to the right, so Next
          holds still and everything to the left of the label is pushed by it: the moment the page
          count gains or loses a digit — which is what typing in the search box does — Previous
          slides. Measured by a field sweep: **8 px**, on the use-case list under a role that sees
          enough use cases to have more than one page.

          Small, and the same shape as the two the same sweep found in the model editor and the
          budget window: a control moving because a neighbour's *contents* changed. With the label
          first, the two buttons are adjacent at the right edge and nothing between them can move
          them; only the label's own left edge travels, and a label is not something anybody is
          reaching for.

          The alternative was a min-width on the label, wide enough for the longest page count.
          That is a number with no derivation — it holds until a list has more pages than somebody
          guessed, and then fails silently in exactly this way again.
        -->
        <span class="pager__controls">
          <span class="pager__position">
            Page {{ view().current() }} of {{ view().pageCount() }}
          </span>
          <button
            type="button"
            class="btn btn--sm"
            [disabled]="view().current() === 1"
            (click)="view().previous()"
            data-testid="pager-previous"
          >
            ← Previous
          </button>
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
