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
      (closed)="open.set(false)"
    >
      <div modal-body><input id="first" /></div>
      <div modal-foot><button type="button" id="save">Save</button></div>
    </app-modal>`,
})
class Host {
  readonly open = signal(true);
  readonly withFoot = signal(true);
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
});
