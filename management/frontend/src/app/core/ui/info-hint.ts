import { Component, computed, input, signal } from '@angular/core';

/**
 * The small "i" that carries the rest of a sentence.
 *
 * A governance console is read by people deciding whether a control is working, and a figure or a
 * control whose meaning is guessed at gets argued about instead of acted on. But a heading long
 * enough to define itself is a heading that wraps and breaks the row it is in. This is where the
 * rest goes.
 *
 * The interaction was got wrong twice before it was extracted here, and both mistakes are the
 * reason this is a component rather than a `title` attribute:
 *
 * - A `title` shows **nothing** on a touch screen, needs a long hover on a mouse, and is invisible
 *   to a keyboard. The control looked clickable and did nothing.
 * - Opening only on click is still wrong: an "i" is a thing you point at.
 *
 * So all three: **hover** shows it, **focus** shows it (keyboard), and a **click pins** it open
 * (touch, and for reading something longer without holding the pointer still). The panel is
 * absolutely positioned, so the row does not grow under the pointer and shove its neighbours.
 *
 * **One pinned at a time**, across the whole page. The panels are overlays: two open beside each
 * other overlap one another and cover the figures they describe, and a row of six tiles pinned
 * open is a wall of text where six numbers were. Hover is exempt — only one thing can be pointed
 * at anyway.
 */

/**
 * The hint currently pinned open, if any.
 *
 * Module-level rather than injected: a service would have to be provided somewhere, and the one
 * thing this project has already learned about provider scope is that "somewhere" is where it
 * goes wrong (`live.spec.ts`). There is one pointer and one reader per document, so one value.
 */
const pinnedHint = signal<InfoHint | null>(null);
@Component({
  selector: 'app-info-hint',
  template: `
    <span class="info-hint">
      <button
        type="button"
        class="info-hint__button"
        [class.is-open]="pinned()"
        [attr.aria-expanded]="shown()"
        [attr.aria-label]="'What does ' + label() + ' mean?'"
        [attr.data-testid]="testid() ? 'info-' + testid() : null"
        (mouseenter)="hovered.set(true)"
        (mouseleave)="hovered.set(false)"
        (focus)="hovered.set(true)"
        (blur)="hovered.set(false)"
        (click)="togglePin()"
      >
        i
      </button>
      @if (shown()) {
        <span
          class="info-hint__panel"
          [class.info-hint__panel--wide]="wide()"
          role="note"
          [attr.data-testid]="testid() ? 'help-' + testid() : null"
        >
          <ng-content />
        </span>
      }
    </span>
  `,
})
export class InfoHint {
  /** What the "i" is about — used for the accessible name, never rendered. */
  readonly label = input.required<string>();
  /** Optional hook so a test can find this particular hint. */
  readonly testid = input<string>('');
  /** For explanations that are a paragraph rather than a clause. */
  readonly wide = input(false);

  protected readonly hovered = signal(false);
  protected readonly pinned = computed(() => pinnedHint() === this);
  protected readonly shown = computed(() => this.hovered() || this.pinned());

  protected togglePin(): void {
    pinnedHint.set(this.pinned() ? null : this);
  }
}
