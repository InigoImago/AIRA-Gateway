import {
  Component,
  ElementRef,
  computed,
  effect,
  input,
  output,
  signal,
  viewChild,
} from '@angular/core';

/** One choice. `detail` is the second line — what makes two similar names distinguishable. */
export interface MultiSelectOption {
  value: string;
  label: string;
  detail?: string;
  /** Rendered as a warning badge. For the state a reader has to act on, not for decoration. */
  warning?: string;
}

/**
 * Pick several things out of a list that will get long.
 *
 * A checkbox per row is honest and stops working somewhere around thirty: the reader scrolls a
 * list they cannot search to find the four they want, and what is already chosen is scattered
 * through it. This shows the **chosen** set as removable chips and everything else behind a
 * search, so both questions — *what did I pick* and *what else is there* — have their own place.
 *
 * Written as a shared control rather than inside the one screen that needed it first: the model
 * catalog is not the only list here that only grows, and the second copy is where the keyboard
 * handling drifts.
 *
 * **Keyboard first**, because a picker that only works with a mouse is one a keyboard user cannot
 * use at all — there is no fallback the way there is for a checkbox. Arrow keys move, Enter
 * toggles and **keeps the list open** (the whole point is picking several), Escape closes,
 * Backspace on an empty query takes the last chip back.
 */
@Component({
  selector: 'app-multi-select',
  templateUrl: './multi-select.html',
  host: {
    '(document:click)': 'onDocumentClick($event)',
    '(document:keydown.escape)': 'close()',
  },
})
export class MultiSelect {
  readonly label = input.required<string>();
  readonly options = input<MultiSelectOption[]>([]);
  readonly selected = input<string[]>([]);
  readonly disabled = input(false);
  readonly placeholder = input('Type to search…');
  readonly testid = input('multi-select');
  /** What to say when the list is empty for reasons of its own. */
  readonly emptyHint = input('');
  readonly changed = output<string[]>();

  protected readonly query = signal('');
  protected readonly open = signal(false);
  /** Which option the keyboard is on. An index into `matches()`, reset whenever it changes. */
  protected readonly active = signal(0);

  private readonly root = viewChild<ElementRef<HTMLElement>>('root');
  private readonly field = viewChild<ElementRef<HTMLInputElement>>('field');

  constructor() {
    // A stale active index points at a different option than the one under the highlight, so
    // Enter picks something the reader was not looking at.
    effect(() => {
      this.query();
      this.active.set(0);
    });
  }

  protected readonly chosen = computed(() => {
    const known = new Map(this.options().map((option) => [option.value, option]));
    // A value with no option behind it is still shown — as itself. It is usually the interesting
    // one: something chosen earlier that the list no longer offers, and silently dropping it from
    // the chips would hide a state somebody has to act on while leaving it in the saved set.
    return this.selected().map(
      (value) => known.get(value) ?? { value, label: value, warning: 'not in the list' },
    );
  });

  protected readonly matches = computed(() => {
    const query = this.query().trim().toLowerCase();
    const options = this.options();
    if (!query) return options;
    return options.filter((option) =>
      `${option.value} ${option.label} ${option.detail ?? ''}`.toLowerCase().includes(query),
    );
  });

  protected isSelected(value: string): boolean {
    return this.selected().includes(value);
  }

  protected optionId(index: number): string {
    return `${this.testid()}-option-${index}`;
  }

  protected toggle(value: string): void {
    if (this.disabled()) return;
    const current = this.selected();
    this.changed.emit(
      current.includes(value) ? current.filter((v) => v !== value) : [...current, value],
    );
  }

  protected remove(value: string): void {
    if (this.disabled()) return;
    this.changed.emit(this.selected().filter((v) => v !== value));
  }

  protected show(): void {
    if (!this.disabled()) this.open.set(true);
  }

  protected close(): void {
    this.open.set(false);
  }

  /** The chevron. Opening from it puts the keyboard in the field, because the next thing somebody
   *  does after opening a list of fifty is type. */
  protected toggleList(): void {
    if (this.open()) {
      this.close();
      return;
    }
    this.show();
    this.refocus();
  }

  /** Clicking anywhere else closes it. A dropdown that stays open over the page it covers is one
   *  the reader has to dismiss deliberately, having already moved on. */
  protected onDocumentClick(event: Event): void {
    const root = this.root()?.nativeElement;
    if (root && !root.contains(event.target as Node)) this.close();
  }

  protected onKey(event: KeyboardEvent): void {
    const matches = this.matches();
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      const wasOpen = this.open();
      this.open.set(true);
      if (!matches.length) return;
      const down = event.key === 'ArrowDown';
      if (!wasOpen) {
        // **Opening is not moving.** The first draft advanced the index on the same keypress that
        // opened the list, so ArrowDown into a closed picker landed on the *second* option and the
        // first was unreachable without the mouse — a keyboard-only reader would never see it.
        this.active.set(down ? 0 : matches.length - 1);
        return;
      }
      this.active.set((this.active() + (down ? 1 : -1) + matches.length) % matches.length);
      return;
    }
    if (event.key === 'Enter') {
      // Never submits the form it sits in: a picker inside a settings form would otherwise save
      // the page every time somebody chose an option.
      event.preventDefault();
      const option = matches[this.active()];
      if (this.open() && option) this.toggle(option.value);
      else this.open.set(true);
      return;
    }
    if (event.key === 'Backspace' && !this.query()) {
      const chosen = this.selected();
      if (chosen.length) this.remove(chosen[chosen.length - 1]);
    }
  }

  /** Put the keyboard back in the search field after a chip is removed, so a reader taking three
   *  things out does not have to click back in between each one. */
  protected refocus(): void {
    this.field()?.nativeElement.focus();
  }
}
