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
 * A checkbox per row stops working around thirty: the chosen items scatter through a list nobody
 * can search. This shows the **chosen** set as removable chips and everything else behind a search,
 * so "what did I pick" and "what else is there" each have their own place.
 *
 * **Keyboard first** — a picker has no fallback the way a checkbox does. Arrow keys move, Enter
 * toggles and **keeps the list open**, Escape closes, Backspace on an empty query takes the last
 * chip back.
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
    // A stale index points at a different option than the highlight, so Enter would pick
    // something the reader was not looking at.
    effect(() => {
      this.query();
      this.active.set(0);
    });
  }

  protected readonly chosen = computed(() => {
    const known = new Map(this.options().map((option) => [option.value, option]));
    // A value with no option behind it is still shown, as itself: something chosen earlier that the
    // list no longer offers is a state somebody has to act on, not one to hide.
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

  /** The chevron. Opening from it puts the keyboard in the field: the next thing somebody does
   *  after opening a list of fifty is type. */
  protected toggleList(): void {
    if (this.open()) {
      this.close();
      return;
    }
    this.show();
    this.refocus();
  }

  /** Clicking anywhere else closes it. */
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
        // Opening is not moving: advancing on the keypress that opened the list would make the
        // first option unreachable without the mouse.
        this.active.set(down ? 0 : matches.length - 1);
        return;
      }
      this.active.set((this.active() + (down ? 1 : -1) + matches.length) % matches.length);
      return;
    }
    if (event.key === 'Enter') {
      // Never submits the form it sits in, or choosing an option would save the page.
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

  /** Put the keyboard back in the search field after a chip is removed, so removing three things
   *  does not need a click between each. */
  protected refocus(): void {
    this.field()?.nativeElement.focus();
  }
}
