import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Modal } from './modal';

/**
 * The window itself, tested where it lives rather than through the five screens that open one.
 *
 * Its three promises are the ones every hand-rolled dialog forgets one of, and each fails
 * silently: a window with no Escape traps whoever opened it, a window that leaves the keyboard on
 * the page behind puts Tab somewhere invisible, and a backdrop that does nothing is the first
 * thing people try.
 */
@Component({
  selector: 'app-host',
  imports: [Modal],
  template: `<button type="button" id="outside">outside</button>
    <app-modal
      [open]="open()"
      title="Add a budget"
      testid="budget-editor"
      [withFoot]="withFoot()"
      [dismissible]="dismissible()"
      [panelClass]="panelClass()"
      [error]="error()"
      (closed)="open.set(false)"
    >
      <div modal-body><input id="first" /></div>
      <div modal-foot><button type="button" id="save">Save</button></div>
    </app-modal>`,
})
class Host {
  readonly open = signal(true);
  readonly withFoot = signal(true);
  readonly dismissible = signal(true);
  readonly panelClass = signal('');
  readonly error = signal<string | null>(null);
}

function setup() {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({ imports: [Host] });
  const fixture = TestBed.createComponent(Host);
  fixture.detectChanges();
  const root = () => fixture.nativeElement as HTMLElement;
  return {
    fixture,
    host: fixture.componentInstance,
    q: <T extends Element>(selector: string) => root().querySelector<T>(selector),
    text: () => root().textContent ?? '',
    escape: () => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      fixture.detectChanges();
    },
  };
}

describe('Modal', () => {
  it('closes on Escape, on the backdrop and on the ✕', () => {
    for (const close of [
      (h: ReturnType<typeof setup>) => h.escape(),
      (h: ReturnType<typeof setup>) => h.q<HTMLElement>('.modal-backdrop')!.click(),
      (h: ReturnType<typeof setup>) =>
        h.q<HTMLElement>('[data-testid="budget-editor-close"]')!.click(),
    ]) {
      const harness = setup();
      expect(harness.q('[data-testid="budget-editor"]')).not.toBeNull();

      close(harness);
      harness.fixture.detectChanges();

      expect(harness.host.open()).toBe(false);
      expect(harness.q('[data-testid="budget-editor"]')).toBeNull();
    }
  });

  it('does nothing on Escape when it is not open', () => {
    /** The host owns `open`, so a window that emitted `closed` while shut would close whatever the
     *  page had opened *since* — which is how an Escape aimed at a picker shuts the form behind
     *  it. */
    const harness = setup();
    harness.host.open.set(false);
    harness.fixture.detectChanges();

    harness.escape();
    expect(harness.host.open()).toBe(false); // not an error, but nothing was emitted to observe
  });

  it('moves the keyboard into the window', () => {
    const harness = setup();
    expect(document.activeElement).toBe(harness.q('[data-testid="budget-editor"]'));
  });

  it('names itself for a screen reader by the heading it draws', () => {
    const harness = setup();
    const dialog = harness.q<HTMLElement>('[data-testid="budget-editor"]')!;

    expect(dialog.getAttribute('role')).toBe('dialog');
    expect(dialog.getAttribute('aria-modal')).toBe('true');
    expect(dialog.getAttribute('aria-labelledby')).toBe('budget-editor-title');
    expect(harness.q('#budget-editor-title')!.textContent).toContain('Add a budget');
  });

  it('draws no action row for a form that already has one', () => {
    /** Otherwise a form rendering its own Cancel and Save grows a second, empty bar underneath —
     *  which reads as a control that failed to load rather than as one that was never wanted. */
    const harness = setup();
    expect(harness.q('.modal__foot')).not.toBeNull();

    harness.host.withFoot.set(false);
    harness.fixture.detectChanges();

    expect(harness.q('.modal__foot')).toBeNull();
    expect(harness.q('.modal__body')).not.toBeNull();
  });

  it('takes a modifier class on the window without losing its own', () => {
    /** A tabbed window keeps its size with `modal--steady`; a binding that replaced `modal` instead
     *  of adding to it would drop the window's position and frame. */
    const harness = setup();
    harness.host.panelClass.set('modal--steady');
    harness.fixture.detectChanges();

    const dialog = harness.q<HTMLElement>('[data-testid="budget-editor"]')!;
    expect(dialog.classList.contains('modal')).toBe(true);
    expect(dialog.classList.contains('modal--steady')).toBe(true);

    harness.host.panelClass.set('');
    harness.fixture.detectChanges();
    expect(dialog.classList.contains('modal')).toBe(true);
    expect(dialog.classList.contains('modal--steady')).toBe(false);
  });

  it('offers no way out but its own action when it must be answered', () => {
    /** The privacy notice (`FRD-625`): Escape and the backdrop do nothing, and there is no ✕ —
     *  an ✕ that did nothing would be an exit that is not one. */
    const harness = setup();
    harness.host.dismissible.set(false);
    harness.fixture.detectChanges();

    expect(harness.q('[data-testid="budget-editor-close"]')).toBeNull();
    harness.escape();
    harness.q<HTMLElement>('.modal-backdrop')!.click();
    harness.fixture.detectChanges();

    expect(harness.host.open()).toBe(true);
    expect(harness.q('[data-testid="budget-editor"]')).not.toBeNull();
    expect(harness.q('#save')).not.toBeNull();
  });
});

/**
 * "Nothing editable behind it" was true only for the mouse: Tab walked out of the window into the
 * page, Enter on a row's Edit behind the backdrop replaced the unsaved draft, and closing left the
 * focus on `<body>`.
 */
describe('Modal — the keyboard stays in the window', () => {
  const tab = (shift = false) =>
    (document.activeElement as HTMLElement).dispatchEvent(
      new KeyboardEvent('keydown', {
        key: 'Tab',
        shiftKey: shift,
        bubbles: true,
        cancelable: true,
      }),
    );

  it('wraps Tab from the last control to the first, and Shift-Tab back', () => {
    const harness = setup();
    const close = harness.q<HTMLElement>('[data-testid="budget-editor-close"]')!;
    const save = harness.q<HTMLElement>('#save')!;

    save.focus();
    tab();
    expect(document.activeElement).toBe(close);

    tab(true);
    expect(document.activeElement).toBe(save);
  });

  it('wraps Shift-Tab from the window itself, where the keyboard first lands', () => {
    const harness = setup();
    expect(document.activeElement).toBe(harness.q('[data-testid="budget-editor"]'));

    tab(true);

    expect(document.activeElement).toBe(harness.q('#save'));
  });

  it('makes the page behind it inert while open, and gives it back on closing', () => {
    // Inert, not merely covered: a covered button still takes Tab and Enter.
    const harness = setup();
    const outside = harness.q<HTMLElement>('#outside')!;
    expect(outside.closest('[inert]')).not.toBeNull();
    // Its own controls, and the backdrop that closes it, stay live.
    expect(harness.q('#first')!.closest('[inert]')).toBeNull();
    expect(harness.q('.modal-backdrop')!.closest('[inert]')).toBeNull();

    harness.host.open.set(false);
    harness.fixture.detectChanges();

    expect(outside.closest('[inert]')).toBeNull();
  });

  it('does not take back an inert it did not set', () => {
    const harness = setup();
    harness.host.open.set(false);
    harness.fixture.detectChanges();
    const outside = harness.q<HTMLElement>('#outside')!;
    outside.setAttribute('inert', '');

    harness.host.open.set(true);
    harness.fixture.detectChanges();
    harness.host.open.set(false);
    harness.fixture.detectChanges();

    expect(outside.hasAttribute('inert')).toBe(true);
  });

  it('returns the keyboard to whatever opened it', () => {
    const harness = setup();
    harness.host.open.set(false);
    harness.fixture.detectChanges();
    const opener = harness.q<HTMLElement>('#outside')!;
    opener.focus();

    harness.host.open.set(true);
    harness.fixture.detectChanges();
    expect(document.activeElement).not.toBe(opener);
    harness.escape();

    expect(document.activeElement).toBe(opener);
  });
});

/**
 * A refused save, said where the reader is looking. It used to go only to the page banner — behind
 * the backdrop, dimmed, away from the button that had just flickered back to "Save".
 */
describe('Modal — a failure inside the window', () => {
  it('says why, next to the action', () => {
    const harness = setup();
    harness.host.error.set('The longest lifetime this installation allows is 180 days.');
    harness.fixture.detectChanges();

    const said = harness.q<HTMLElement>('[data-testid="budget-editor-error"]');
    expect(said?.textContent).toContain('180 days');
    expect(said?.getAttribute('role')).toBe('alert');
    expect(said?.closest('.modal__foot')).not.toBeNull();
  });

  it('says it in the body when the form brings its own buttons', () => {
    const harness = setup();
    harness.host.withFoot.set(false);
    harness.host.error.set('slug: Already exists.');
    harness.fixture.detectChanges();

    expect(
      harness.q('[data-testid="budget-editor-error"]')?.closest('.modal__body'),
    ).not.toBeNull();
  });

  it('says nothing when nothing failed', () => {
    expect(setup().q('[data-testid="budget-editor-error"]')).toBeNull();
  });
});
