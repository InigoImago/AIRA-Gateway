import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { MultiSelect, MultiSelectOption } from './multi-select';

/**
 * Picking several things out of a list that only grows.
 *
 * Most of what is asserted here is **keyboard**, and that is deliberate: a picker that only works
 * with a mouse is one a keyboard user cannot use at all — unlike a checkbox, there is no fallback
 * underneath it. The rest is the two ways a picker of this shape goes quietly wrong: it forgets
 * what was already chosen when a search hides it, and it submits the form it sits in.
 */

const OPTIONS: MultiSelectOption[] = [
  { value: 'gemini-2.5-flash', label: 'gemini-2.5-flash', detail: 'Google · 0.075 / 1M in' },
  { value: 'claude-sonnet-4-5', label: 'claude-sonnet-4-5', detail: 'Vertex · no price on file' },
  { value: 'qwen3:0.6b', label: 'qwen3:0.6b', detail: 'ollama' },
];

@Component({
  selector: 'app-host',
  imports: [MultiSelect],
  template: `<form (ngSubmit)="submitted.set(submitted() + 1)">
    <app-multi-select
      label="Models"
      testid="pick"
      [options]="options()"
      [selected]="selected()"
      [disabled]="disabled()"
      emptyHint="Nothing has been approved yet."
      (changed)="selected.set($event)"
    />
  </form>`,
})
class Host {
  readonly options = signal<MultiSelectOption[]>(OPTIONS);
  readonly selected = signal<string[]>([]);
  readonly disabled = signal(false);
  readonly submitted = signal(0);
}

function setup(selected: string[] = [], options?: MultiSelectOption[]) {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({ imports: [Host] });
  const fixture = TestBed.createComponent(Host);
  const host = fixture.componentInstance;
  host.selected.set(selected);
  if (options) host.options.set(options);
  fixture.detectChanges();

  const root = () => fixture.nativeElement as HTMLElement;
  const q = <T extends Element>(selector: string) => root().querySelector<T>(selector);
  const field = () => q<HTMLInputElement>('[data-testid="pick-search"]')!;
  const open = () => {
    q<HTMLButtonElement>('[data-testid="pick-toggle"]')!.click();
    fixture.detectChanges();
  };
  const key = (name: string) => {
    field().dispatchEvent(new KeyboardEvent('keydown', { key: name, bubbles: true }));
    fixture.detectChanges();
  };
  const type = (value: string) => {
    field().value = value;
    field().dispatchEvent(new Event('input'));
    fixture.detectChanges();
  };
  return {
    fixture,
    host,
    root,
    q,
    field,
    open,
    key,
    type,
    text: () => root().textContent ?? '',
    optionIds: () =>
      [...root().querySelectorAll('[data-testid^="pick-option-"]')].map((element) =>
        element.getAttribute('data-testid'),
      ),
  };
}

describe('MultiSelect', () => {
  it('keeps what is chosen on screen while the search hides it', () => {
    /** The failure that would be catastrophic and invisible: a reader searches for a fourth model,
     *  the three they already picked scroll out of the list, and nothing on screen says they are
     *  still selected. The chips are outside the search for exactly this. */
    const { host, open, type, text, optionIds } = setup(['claude-sonnet-4-5']);

    open();
    type('gemini');

    expect(optionIds()).toEqual(['pick-option-gemini-2.5-flash']);
    expect(text()).toContain('claude-sonnet-4-5');
    expect(host.selected()).toEqual(['claude-sonnet-4-5']);
  });

  it('picks and unpicks with the keyboard, and keeps the list open', () => {
    /** The whole point is choosing **several**. A list that closed on the first Enter would make
     *  picking four models four round trips through the control. */
    const { host, key, root } = setup();

    key('ArrowDown'); // opens
    key('Enter'); // toggles the first
    expect(host.selected()).toEqual(['gemini-2.5-flash']);
    expect(root().querySelector('[role="listbox"]')).not.toBeNull();

    key('ArrowDown');
    key('Enter');
    expect(host.selected()).toEqual(['gemini-2.5-flash', 'claude-sonnet-4-5']);

    key('Enter'); // the same one again takes it back
    expect(host.selected()).toEqual(['gemini-2.5-flash']);
  });

  it('opens onto the first option rather than past it, and wraps at both ends', () => {
    /** The first draft advanced the index on the same keypress that opened the list, so ArrowDown
     *  into a closed picker landed on the **second** option — and the first was unreachable
     *  without a mouse, which for a keyboard-only reader means unreachable. Caught by the test
     *  above going red on its very first assertion. */
    const down = setup();
    down.key('ArrowDown');
    expect(down.root().querySelector('.picker__item.is-active')?.textContent).toContain(
      'gemini-2.5-flash',
    );

    const up = setup();
    up.key('ArrowUp'); // opens onto the last
    expect(up.root().querySelector('.picker__item.is-active')?.textContent).toContain('qwen3:0.6b');

    up.key('ArrowDown'); // and wraps round to the first
    expect(up.root().querySelector('.picker__item.is-active')?.textContent).toContain(
      'gemini-2.5-flash',
    );
  });

  it('never submits the form it sits in', () => {
    /** A picker inside a settings form would otherwise save the page every time somebody chose an
     *  option — and the save would carry a half-made selection. */
    const { host, key } = setup();

    key('ArrowDown');
    key('Enter');

    expect(host.submitted()).toBe(0);
  });

  it('takes the last chip back on Backspace in an empty field', () => {
    const { host, key, type } = setup(['gemini-2.5-flash', 'qwen3:0.6b']);

    type('gem');
    key('Backspace'); // the field has text: this is ordinary typing, not a removal
    expect(host.selected()).toEqual(['gemini-2.5-flash', 'qwen3:0.6b']);

    type('');
    key('Backspace');
    expect(host.selected()).toEqual(['gemini-2.5-flash']);
  });

  it('removes one from its chip and puts the keyboard back in the field', () => {
    /** Somebody taking three models out should not have to click back into the search between
     *  each one. */
    const { host, q, field, fixture } = setup(['gemini-2.5-flash', 'qwen3:0.6b']);

    q<HTMLButtonElement>('[data-testid="pick-remove-qwen3:0.6b"]')!.click();
    fixture.detectChanges();

    expect(host.selected()).toEqual(['gemini-2.5-flash']);
    expect(document.activeElement).toBe(field());
  });

  it('shows a value the list no longer offers, rather than dropping it silently', () => {
    /** Usually the interesting one: a model released earlier and since withdrawn from the catalog.
     *  Hiding it would leave it in the saved set with nothing on screen about it. */
    const { text } = setup(['withdrawn-1']);

    expect(text()).toContain('withdrawn-1');
    expect(text()).toContain('not in the list');
  });

  it('tells "nothing matches" apart from "there is nothing"', () => {
    /** One is the reader's to fix, the other is somebody else's. A shared message sends half of
     *  them to the wrong screen. */
    const typed = setup();
    typed.open();
    typed.type('zzz');
    expect(typed.text()).toContain('Nothing matches');

    const bare = setup([], []);
    bare.open();
    expect(bare.text()).toContain('Nothing has been approved yet.');
  });

  it('closes on a click elsewhere and on Escape', () => {
    const { open, root, fixture } = setup();

    open();
    expect(root().querySelector('[role="listbox"]')).not.toBeNull();

    document.body.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    fixture.detectChanges();
    expect(root().querySelector('[role="listbox"]')).toBeNull();

    open();
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    fixture.detectChanges();
    expect(root().querySelector('[role="listbox"]')).toBeNull();
  });

  it('says what it is and what it is doing, for a screen reader', () => {
    /** A `<div>` of buttons is a list only to somebody who can see it. The combobox pattern is
     *  what makes the state — open, how many, which one is under the cursor, which are picked —
     *  available at all without sight. */
    const { field, open, key, root, q } = setup(['gemini-2.5-flash']);

    expect(field().getAttribute('role')).toBe('combobox');
    expect(field().getAttribute('aria-expanded')).toBe('false');
    expect(field().getAttribute('aria-label')).toBe('Models');

    open();
    key('ArrowDown');
    expect(field().getAttribute('aria-expanded')).toBe('true');
    expect(field().getAttribute('aria-controls')).toBe('pick-list');
    // The active option is named, so a screen reader announces the row the arrow keys are on.
    const active = root().querySelector('.picker__item.is-active')!;
    expect(field().getAttribute('aria-activedescendant')).toBe(active.id);
    expect(q('[data-testid="pick-option-gemini-2.5-flash"]')!.getAttribute('aria-selected')).toBe(
      'true',
    );
    expect(q('[data-testid="pick-option-qwen3:0.6b"]')!.getAttribute('aria-selected')).toBe(
      'false',
    );
  });

  it('offers no controls at all when it is read-only', () => {
    /** Inert, not un-saveable (`FRD-206`): a row of disabled controls is a row of things that do
     *  nothing. The chips stay, because what is chosen is still worth reading. */
    const { host, fixture, q, text } = setup(['gemini-2.5-flash']);

    host.disabled.set(true);
    fixture.detectChanges();

    expect(text()).toContain('gemini-2.5-flash');
    expect(q('[data-testid="pick-search"]')).toBeNull();
    expect(q('[data-testid="pick-remove-gemini-2.5-flash"]')).toBeNull();
  });

  it('changes nothing when a read-only picker is driven from code', () => {
    /** The controls are gone from the DOM, which is the visible half. This is the other half: a
     *  picker that is only *visually* read-only is one a stale reference, a keyboard event on a
     *  chip that outlived its render, or a future template can still change — and `FRD-206`'s rule
     *  is that read-only means inert. Asserted through the component because that is the only way
     *  to reach a control the template no longer draws. */
    const { host, fixture } = setup(['gemini-2.5-flash']);
    host.disabled.set(true);
    fixture.detectChanges();

    const picker = fixture.debugElement.children[0].children[0].componentInstance as {
      toggle: (v: string) => void;
      remove: (v: string) => void;
      show: () => void;
      open: () => boolean;
    };
    picker.toggle('qwen3:0.6b');
    picker.remove('gemini-2.5-flash');
    picker.show();

    expect(host.selected()).toEqual(['gemini-2.5-flash']);
    expect(picker.open()).toBe(false);
  });

  it('closes again from the chevron, and moves nowhere in an empty list', () => {
    /** Two small paths that only announce themselves by misbehaving: a chevron that only opens
     *  leaves the reader clicking away to dismiss, and an arrow key in a list of nothing must not
     *  land the highlight on an option that is not there. */
    const { open, key, q, fixture, root } = setup([], []);

    open();
    expect(q('#pick-list')).not.toBeNull();

    key('ArrowDown'); // no options — nothing to move onto
    expect(root().querySelectorAll('[data-testid^="pick-option-"]').length).toBe(0);

    q<HTMLButtonElement>('[data-testid="pick-toggle"]')!.click();
    fixture.detectChanges();
    expect(q('#pick-list')).toBeNull();
  });
});
