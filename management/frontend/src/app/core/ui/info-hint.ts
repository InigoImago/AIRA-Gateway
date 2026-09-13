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
 * The hint currently pinned open, if any. Module-level rather than a service: there is one pointer
 * and one reader per document, and a provided service's scope is exactly where this would go wrong.
 */
const pinnedHint = signal<InfoHint | null>(null);

/** Breathing room between the panel and the edge of the viewport. */
const MARGIN = 8;

/**
 * The small "i" that carries the rest of a sentence a heading has no room for.
 *
 * - **Hover and focus** show it and a **click pins** it: a `title` attribute shows nothing on a
 *   touch screen or to a keyboard.
 * - **One pinned at a time**, page-wide: the panels are overlays, and two open cover each other and
 *   the figures they describe. Hover is exempt — only one thing can be pointed at anyway.
 * - The panel is `position: fixed`, placed from the button's rectangle and clamped into the
 *   viewport. An absolutely positioned panel extends its scroll container (a flicker loop near the
 *   bottom of a page) and leaves its container when centred near an edge. It stays invisible for
 *   the one frame it is measured, so it never visibly jumps.
 */
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
        // Closed: forget the placement, so the next open measures again.
        this.placed.set(false);
        return;
      }
      // Once per open. Written onto the element rather than bound, because placing it requires
      // reading where it landed.
      if (this.placed()) return;
      this.place(this.trigger().nativeElement, panel.nativeElement);
      this.placed.set(true);
    });
  }

  /**
   * Below the "i" and centred on it, pulled back inside the viewport at either edge — or above it
   * when there is no room below, since the last row of a form is where help is most often asked.
   */
  private place(trigger: HTMLElement, panel: HTMLElement): void {
    // `fixed` is relative to an ancestor carrying a `transform` (the modal has one), not always to
    // the viewport. So the origin is read — park the panel at (0, 0) and see where that lands —
    // rather than assumed.
    panel.style.top = '0px';
    panel.style.left = '0px';
    const origin = panel.getBoundingClientRect();

    const anchor = trigger.getBoundingClientRect();
    const { width, height } = origin;

    const centred = anchor.left + anchor.width / 2 - width / 2;
    const rightmost = window.innerWidth - width - MARGIN;
    // `Math.max` last: a panel wider than the viewport loses the end of the sentence, not the start.
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
