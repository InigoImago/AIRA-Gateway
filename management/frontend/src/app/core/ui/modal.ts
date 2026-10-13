import {
  Component,
  DestroyRef,
  ElementRef,
  effect,
  inject,
  input,
  output,
  viewChild,
} from '@angular/core';

/** What Tab can land on inside the window. */
const FOCUSABLE = 'a[href], button, input, select, textarea, [tabindex]';

/**
 * A window: one thing to do, one way out, and nothing editable behind it.
 *
 * A window rather than a form that unfolds in place, because an inline form scrolls the page away
 * from the row it is about, leaves the list behind it clickable, and says nothing about what it is
 * editing (`FRD-206`). The properties it owns so no caller has to remember them:
 *
 * - **Escape closes.** A window whose only exit is the mouse is one somebody gets stuck in.
 * - **The keyboard moves in, stays in, and goes back.** Tab wraps at the window's edges, the page
 *   behind is `inert` while it is open — a covered button still takes Tab and Enter, and Enter on a
 *   row's Edit behind the backdrop would replace the unsaved draft — and on closing the keyboard
 *   returns to whatever opened it rather than to the top of the page.
 * - **The backdrop closes.** Clicking away is what people try first.
 * - **A refusal is said inside it**, next to the action (`error`), not only in the page banner
 *   behind the backdrop.
 *
 * Unless `dismissible` is off: a window that must be answered — the privacy notice (`FRD-625`) —
 * has no ✕, and Escape and the backdrop do nothing. Its one way out is the action it asks for; an
 * ✕ that did nothing would be an exit that is not one.
 */
@Component({
  selector: 'app-modal',
  templateUrl: './modal.html',
  // The title shares its row with the ✕; the row is the unit, so the heading has no margin.
  styles: `
    .modal__title {
      margin: 0;
    }
  `,
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
  /** Classes added to the window itself, beside `modal` — `modal--steady` for a tabbed window
   *  that must keep its size while its tabs change (`styles.scss`). A modifier rather than a
   *  second window component, so a caller that needs one keeps Escape, focus and the backdrop. */
  readonly panelClass = input('');
  /** Why the window's action failed, in the server's words — shown beside the action. */
  readonly error = input<string | null>(null);
  /** Raised on Escape, the backdrop, and the ✕. The caller owns `open` and decides what to do. */
  readonly closed = output<void>();

  private readonly dialog = viewChild<ElementRef<HTMLElement>>('dialog');
  private readonly backdrop = viewChild<ElementRef<HTMLElement>>('backdrop');

  /** Where the keyboard was when the window opened, to give it back on closing. */
  private opener: HTMLElement | null = null;
  /** What this window made inert — only that is released, never an `inert` somebody else set. */
  private inerted: HTMLElement[] = [];

  constructor() {
    effect(() => {
      const dialog = this.dialog()?.nativeElement;
      if (this.open() && dialog) {
        this.takeKeyboard(dialog);
      } else {
        this.releaseKeyboard();
      }
    });
    inject(DestroyRef).onDestroy(() => this.releaseKeyboard());
  }

  /** Tab and Shift-Tab wrap at the window's edges instead of leaving it. */
  protected keepFocus(event: KeyboardEvent): void {
    const dialog = this.dialog()?.nativeElement;
    if (event.key !== 'Tab' || !dialog) return;
    const stops = [...dialog.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
      (element) => element.tabIndex >= 0 && !element.hasAttribute('disabled'),
    );
    const first = stops[0];
    const last = stops.at(-1);
    const active = document.activeElement;
    if (!first || !last) {
      event.preventDefault();
      dialog.focus();
    } else if (event.shiftKey && (active === first || active === dialog)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  }

  private takeKeyboard(dialog: HTMLElement): void {
    if (this.opener === null && document.activeElement instanceof HTMLElement) {
      this.opener = document.activeElement;
    }
    this.inertAround(dialog);
    dialog.focus();
  }

  /**
   * Everything but the window and its backdrop goes inert: at every level from the window up to
   * `<body>`, the siblings of the branch that holds it.
   */
  private inertAround(dialog: HTMLElement): void {
    if (this.inerted.length) return;
    const keep = new Set<Element>([
      dialog,
      ...(this.backdrop() ? [this.backdrop()!.nativeElement] : []),
    ]);
    for (let node: HTMLElement | null = dialog; node && node !== document.body;) {
      const parent: HTMLElement | null = node.parentElement;
      for (const sibling of parent ? [...parent.children] : []) {
        if (keep.has(sibling) || sibling === node || !(sibling instanceof HTMLElement)) continue;
        if (sibling.hasAttribute('inert')) continue;
        sibling.setAttribute('inert', '');
        this.inerted.push(sibling);
      }
      node = parent;
    }
  }

  private releaseKeyboard(): void {
    for (const element of this.inerted) element.removeAttribute('inert');
    this.inerted = [];
    const opener = this.opener;
    this.opener = null;
    if (opener?.isConnected) opener.focus();
  }

  protected closeIfOpen(): void {
    if (this.open() && this.dismissible()) this.closed.emit();
  }
}
