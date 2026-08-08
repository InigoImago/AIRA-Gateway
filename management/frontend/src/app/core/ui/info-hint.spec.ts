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
