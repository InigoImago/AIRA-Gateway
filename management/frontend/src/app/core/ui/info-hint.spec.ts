import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { InfoHint } from './info-hint';

@Component({
  selector: 'app-hint-host',
  imports: [InfoHint],
  template: `
    <span>
      Prompt / completion tokens
      <app-info-hint label="prompt and completion tokens" testid="tokens">
        Prompt is what went in, completion is what came out.
      </app-info-hint>
    </span>
  `,
})
class Host {}

function setup() {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({ imports: [Host] });
  const fixture = TestBed.createComponent(Host);
  fixture.detectChanges();
  const element = fixture.nativeElement as HTMLElement;
  const button = element.querySelector('[data-testid="info-tokens"]') as HTMLButtonElement;
  const fire = (event: string) => {
    button.dispatchEvent(new Event(event));
    fixture.detectChanges();
  };
  return {
    fixture,
    element,
    button,
    fire,
    panel: () => element.querySelector('[data-testid="help-tokens"]'),
    click: () => {
      button.click();
      fixture.detectChanges();
    },
  };
}

describe('InfoHint', () => {
  it('shows nothing until it is asked', () => {
    const { panel } = setup();
    expect(panel()).toBeNull();
  });

  it('opens on hover — an "i" is a thing you point at', () => {
    // The first repair for this opened on click only. It worked, and was still wrong: nobody
    // hovers an "i" and expects to have to press it.
    const harness = setup();

    harness.fire('mouseenter');
    expect(harness.panel()?.textContent).toContain('Prompt is what went in');

    harness.fire('mouseleave');
    expect(harness.panel()).toBeNull();
  });

  it('opens on focus, so a keyboard can read it too', () => {
    const harness = setup();

    harness.fire('focus');
    expect(harness.panel()).not.toBeNull();

    harness.fire('blur');
    expect(harness.panel()).toBeNull();
  });

  it('pins open on a click, because a touch screen has no hover at all', () => {
    const harness = setup();

    harness.click();
    expect(harness.panel()).not.toBeNull();
    // Moving the pointer away must not close what was deliberately opened.
    harness.fire('mouseleave');
    expect(harness.panel()).not.toBeNull();

    harness.click();
    expect(harness.panel()).toBeNull();
  });

  it('says what it is about, so it is not an unlabelled "i" to a screen reader', () => {
    const { button } = setup();

    expect(button.getAttribute('aria-label')).toBe('What does prompt and completion tokens mean?');
    expect(button.getAttribute('aria-expanded')).toBe('false');
  });

  it('reports its state to assistive technology when it opens', () => {
    const harness = setup();
    harness.fire('mouseenter');

    expect(harness.button.getAttribute('aria-expanded')).toBe('true');
    expect(harness.panel()?.getAttribute('role')).toBe('note');
  });

  it('closes on Escape, pinned or hovered', () => {
    // Content that appears on hover or focus has to be dismissible without moving the pointer or
    // the focus (WCAG 1.4.13) — a panel covering the figure somebody is reading is in the way.
    const harness = setup();
    const escape = () => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      harness.fixture.detectChanges();
    };

    harness.click();
    escape();
    expect(harness.panel()).toBeNull();

    harness.fire('mouseenter');
    escape();
    expect(harness.panel()).toBeNull();
  });

  it('lets go of a pinned panel on a click anywhere else', () => {
    const harness = setup();
    harness.click();

    document.body.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    harness.fixture.detectChanges();

    expect(harness.panel()).toBeNull();
  });

  it('forgets a pinned hint whose page has gone', () => {
    // Held at module level, the pin outlived its component: the view it belonged to stayed in
    // memory after the navigation, and the next page's hints compared themselves against it.
    const harness = setup();
    harness.click();
    const hint = harness.fixture.debugElement.query(
      (node) => node.componentInstance instanceof InfoHint,
    ).componentInstance as unknown as { pinned: () => boolean };
    expect(hint.pinned()).toBe(true);

    harness.fixture.destroy();

    expect(hint.pinned()).toBe(false);
  });

  it('follows its "i" when the page scrolls under a pinned panel', () => {
    // `position: fixed` and placed once per open: the panel stayed where the "i" had been while
    // the page scrolled the "i" away.
    const harness = setup();
    let bottom = 120;
    vi.spyOn(harness.button, 'getBoundingClientRect').mockImplementation(
      () => ({ top: bottom - 20, bottom, left: 50, right: 70, width: 20, height: 20 }) as DOMRect,
    );
    harness.click();
    const panel = harness.panel() as HTMLElement;
    expect(panel.style.top).toBe('126px');

    bottom = 60;
    document.dispatchEvent(new Event('scroll'));

    expect(panel.style.top).toBe('66px');
  });
});

@Component({
  selector: 'app-two-hints-host',
  imports: [InfoHint],
  template: `
    <app-info-hint label="spend" testid="a">A</app-info-hint>
    <app-info-hint label="latency" testid="b">B</app-info-hint>
  `,
})
class TwoHints {}

describe('InfoHint — one pinned at a time', () => {
  function two() {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({ imports: [TwoHints] });
    const fixture = TestBed.createComponent(TwoHints);
    fixture.detectChanges();
    const element = fixture.nativeElement as HTMLElement;
    return {
      element,
      pin: (id: string) => {
        element.querySelector<HTMLButtonElement>(`[data-testid="info-${id}"]`)!.click();
        fixture.detectChanges();
      },
      panel: (id: string) => element.querySelector(`[data-testid="help-${id}"]`),
    };
  }

  it('closes the previous one when another is pinned', () => {
    // The panels are overlays. Two open beside each other cover one another and the figures they
    // describe — a row of six tiles pinned open is a wall of text where six numbers were.
    const harness = two();

    harness.pin('a');
    expect(harness.panel('a')).not.toBeNull();

    harness.pin('b');
    expect(harness.panel('a')).toBeNull();
    expect(harness.panel('b')).not.toBeNull();
  });

  it('un-pins on a second press of the same one', () => {
    const harness = two();
    harness.pin('a');
    harness.pin('a');

    expect(harness.panel('a')).toBeNull();
  });
});
