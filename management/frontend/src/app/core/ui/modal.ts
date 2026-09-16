import { Component, ElementRef, effect, input, output, viewChild } from '@angular/core';

/**
 * A window: one thing to do, one way out, and nothing editable behind it.
 *
 * A window rather than a form that unfolds in place, because an inline form scrolls the page away
 * from the row it is about, leaves the list behind it clickable, and says nothing about what it is
 * editing (`FRD-206`). Three properties it owns so no caller has to remember them:
 *
 * - **Escape closes.** A window whose only exit is the mouse is one somebody gets stuck in.
 * - **The keyboard moves in.** Otherwise Tab and Escape still belong to the page underneath.
 * - **The backdrop closes.** Clicking away is what people try first.
 *
 * Unless `dismissible` is off: a window that must be answered — the privacy notice (`FRD-625`) —
 * has no ✕, and Escape and the backdrop do nothing. Its one way out is the action it asks for; an
 * ✕ that did nothing would be an exit that is not one.
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
  /** Whether Escape, the backdrop and the ✕ may close it. Off, only the caller's action can. */
  readonly dismissible = input(true);
  /** Raised on Escape, the backdrop, and the ✕. The caller owns `open` and decides what to do. */
  readonly closed = output<void>();

  private readonly dialog = viewChild<ElementRef<HTMLElement>>('dialog');

  constructor() {
    effect(() => {
      if (this.open()) this.dialog()?.nativeElement.focus();
    });
  }

  protected closeIfOpen(): void {
    if (this.open() && this.dismissible()) this.closed.emit();
  }
}
