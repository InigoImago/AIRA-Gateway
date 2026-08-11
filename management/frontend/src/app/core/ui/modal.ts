import { Component, ElementRef, effect, input, output, viewChild } from '@angular/core';

/**
 * A window: one thing to do, one way out, and nothing editable behind it.
 *
 * Written as a shared control after the third screen needed one. The model catalog had hand-rolled
 * two, and budgets, rate limits and anomaly rules were about to make five — at which point the
 * Escape handler, the focus move and the backdrop exist in five places and differ in four.
 *
 * Why a window at all, rather than a form that unfolds in place: an inline form scrolls the page to
 * a control far from the row it is about, leaves the list behind it clickable, and says nothing
 * about what it is editing. `FRD-206` recorded that with the model editor, where a second Edit
 * silently replaced the first one's unsaved changes.
 *
 * Three properties it owns so no caller has to remember them:
 *
 * - **Escape closes.** A window whose only exit is the mouse is one somebody gets stuck in.
 * - **The keyboard moves in.** Otherwise Tab and Escape still belong to the page underneath.
 * - **The backdrop closes.** Clicking away is what people try first.
 */
@Component({
  selector: 'app-modal',
  templateUrl: './modal.html',
  host: { '(document:keydown.escape)': 'closeIfOpen()' },
})
export class Modal {
  readonly open = input(false);
  readonly title = input.required<string>();
  readonly testid = input('modal');
  /** Whether the window draws its own action row. A form that already renders its own Cancel and
   *  Save passes `false` rather than growing a second, empty one below its buttons. */
  readonly withFoot = input(true);
  /** Raised on Escape, the backdrop, and the ✕. The caller owns `open` and decides what to do. */
  readonly closed = output<void>();

  private readonly dialog = viewChild<ElementRef<HTMLElement>>('dialog');

  constructor() {
    effect(() => {
      if (this.open()) this.dialog()?.nativeElement.focus();
    });
  }

  protected closeIfOpen(): void {
    if (this.open()) this.closed.emit();
  }
}
