import {
  Component,
  ElementRef,
  afterRenderEffect,
  computed,
  input,
  signal,
  viewChild,
} from '@angular/core';

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
 * (touch, and for reading something longer without holding the pointer still).
 *
 * **One pinned at a time**, across the whole page. The panels are overlays: two open beside each
 * other overlap one another and cover the figures they describe, and a row of six tiles pinned
 * open is a wall of text where six numbers were. Hover is exempt — only one thing can be pointed
 * at anyway.
 *
 * ## Why the panel is placed by measurement rather than by CSS
 *
 * It began as `position: absolute` centred on the button, and a walkthrough of the model editor
 * found the two failures that arrangement always has, both of them reported as "the window
 * jiggles and the text runs out of its frame":
 *
 * - **An absolutely positioned panel still extends its scroll container.** Open one near the
 *   bottom and the document grows, a scrollbar appears, the page reflows narrower, the "i" slides
 *   out from under the pointer, the panel closes, the scrollbar goes away, and the pointer is back
 *   over the "i" — a flicker loop that never settles.
 * - **A centred panel leaves its container.** Measured in the model editor: a 480px panel centred
 *   on an "i" near the left edge started 58px outside the dialog. There was a hand-written escape
 *   for the last cell of a table, which is the same defect noticed once and fixed in one place.
 *
 * So the panel is `position: fixed` and placed from the button's own rectangle, clamped into the
 * viewport. Fixed means it contributes nothing to any scroll extent and is clipped by no ancestor
 * — the two properties an overlay needs and the two that `absolute` cannot give it. It is rendered
 * invisible for one frame while it is measured, because a panel that is placed after it is seen is
 * a panel that visibly jumps.
 */

/**
 * The hint currently pinned open, if any.
 *
 * Module-level rather than injected: a service would have to be provided somewhere, and the one
 * thing this project has already learned about provider scope is that "somewhere" is where it
 * goes wrong (`live.spec.ts`). There is one pointer and one reader per document, so one value.
 */
const pinnedHint = signal<InfoHint | null>(null);

/** Breathing room between the panel and the edge of the viewport. */
const MARGIN = 8;

@Component({
  selector: 'app-info-hint',
  template: `
    <span class="info-hint">
      <button
        #trigger
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
          #panel
          class="info-hint__panel"
          [class.info-hint__panel--wide]="wide()"
          [class.is-placed]="placed()"
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

  private readonly trigger = viewChild.required<ElementRef<HTMLElement>>('trigger');
  private readonly panel = viewChild<ElementRef<HTMLElement>>('panel');

  /** Whether the panel has been measured and put where it belongs. */
  protected readonly placed = signal(false);

  constructor() {
    afterRenderEffect(() => {
      const panel = this.panel();
      if (!panel) {
        // Closed. Forget the placement, so the next open measures again rather than flashing at
        // wherever this one happened to be.
        this.placed.set(false);
        return;
      }
      // Once per open. The position is written onto the element rather than bound, because
      // placing it requires *reading* where it landed — see `place`.
      if (this.placed()) return;
      this.place(this.trigger().nativeElement, panel.nativeElement);
      this.placed.set(true);
    });
  }

  /**
   * Below the "i" and centred on it, pulled back inside the viewport at either edge.
   *
   * Above it instead when there is no room below — the last row of a long form is exactly where
   * an explanation is most likely to be asked for, and a panel that opens off the bottom of the
   * screen is one nobody reads.
   */
  private place(trigger: HTMLElement, panel: HTMLElement): void {
    // **`fixed` is not always relative to the viewport.** Any ancestor carrying a `transform` (or
    // a filter, or `will-change`) becomes the containing block for its fixed descendants, and the
    // modal these panels most often open inside has one. Writing viewport coordinates straight
    // into `top`/`left` put the first measured panel 201 pixels left of where it was asked to go
    // and 25 below — which reads as a positioning bug and is a coordinate-space one.
    //
    // So the origin is *read* rather than assumed: park the panel at (0, 0) and see where that
    // is. One extra layout read per hover, and it is right whatever the ancestors do.
    panel.style.top = '0px';
    panel.style.left = '0px';
    const origin = panel.getBoundingClientRect();

    const anchor = trigger.getBoundingClientRect();
    const { width, height } = origin;

    const centred = anchor.left + anchor.width / 2 - width / 2;
    const rightmost = window.innerWidth - width - MARGIN;
    // `Math.max` last: with a panel wider than the viewport, staying on the left edge loses the
    // start of the sentence rather than the end of it.
    const left = Math.max(MARGIN, Math.min(centred, rightmost));

    const below = anchor.bottom + 6;
    const fitsBelow = below + height <= window.innerHeight - MARGIN;
    const top = fitsBelow ? below : Math.max(MARGIN, anchor.top - height - 6);

    panel.style.top = `${top - origin.top}px`;
    panel.style.left = `${left - origin.left}px`;
  }

  protected togglePin(): void {
    pinnedHint.set(this.pinned() ? null : this);
  }
}
