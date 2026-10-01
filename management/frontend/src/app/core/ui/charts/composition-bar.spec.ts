import { TestBed } from '@angular/core/testing';
import { CompositionBar } from './composition-bar';
import { Band, inkOn } from './usage-series';

function band(over: Partial<Band> = {}): Band {
  return {
    key: 'chat-1',
    color: '#2a78d6',
    value: 1_000_000_000,
    display: '1.00',
    share: 100,
    unpriced: 0,
    costUnknown: false,
    ...over,
  };
}

function setup(bands: Band[]) {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({ imports: [CompositionBar] });
  const fixture = TestBed.createComponent(CompositionBar);
  fixture.componentRef.setInput('bands', bands);
  fixture.detectChanges();
  const element = fixture.nativeElement as HTMLElement;
  return {
    element,
    text: () => element.textContent ?? '',
    blocks: () => Array.from(element.querySelectorAll<HTMLElement>('.blocks__block')),
  };
}

describe('CompositionBar', () => {
  it('draws one block per band, as wide as its share', () => {
    const { blocks } = setup([
      band({ key: 'chat-1', share: 70 }),
      band({ key: 'embed-1', share: 30 }),
    ]);

    expect(blocks()).toHaveLength(2);
    expect(blocks()[0].style.width).toBe('70%');
    expect(blocks()[1].style.width).toBe('30%');
  });

  it('keeps a tiny band visible rather than drawing it as nothing', () => {
    // 0.02 % of a month is still a model somebody has to account for, and a block of zero width is
    // a band that does not exist as far as the reader can tell.
    const { blocks } = setup([
      band({ key: 'big', share: 99.98 }),
      band({ key: 'sliver', share: 0.02 }),
    ]);

    expect(parseFloat(blocks()[1].style.width)).toBeGreaterThan(0);
  });

  it('labels a block only where the name fits inside it', () => {
    // A clipped label is worse than none: it crops the first characters of a model name, which is
    // exactly where the family is.
    const { blocks } = setup([
      band({ key: 'roomy', share: 80 }),
      band({ key: 'cramped', share: 2 }),
    ]);

    expect(blocks()[0].textContent).toContain('roomy');
    expect(blocks()[1].textContent?.trim()).toBe('');
  });

  it('names and measures every block, labelled or not, for a pointer', () => {
    const { blocks } = setup([band({ key: 'cramped', share: 2, display: '0.02' })]);

    expect(blocks()[0].title).toContain('cramped');
    expect(blocks()[0].title).toContain('0.02');
  });

  it('puts a legible ink on a label set inside a block', () => {
    // The yellow is the case: white on it is 2.2:1. Asserted against the shared helper rather than
    // against a colour literal, so the two cannot drift.
    const { blocks } = setup([band({ key: 'yellow-band', color: '#eda100', share: 80 })]);

    expect(blocks()[0].style.color).toBe(hexToRgb(inkOn('#eda100')));
  });

  it('writes a small share with a decimal rather than rounding it to nothing', () => {
    // `0%` beside a band that spent money is the same mistake as `0.00` beside one that did. A small
    // band has no room for an inline label, so this is read on the pointer — which is why the share
    // is in the title and not only in the block.
    const { blocks, text } = setup([
      band({ key: 'a', share: 95.4 }),
      band({ key: 'b', share: 0.4 }),
    ]);

    expect(text()).toContain('95%');
    expect(blocks()[1].title).toContain('0.4%');
  });

  it('says there is nothing rather than drawing a bar of nothing', () => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({ imports: [CompositionBar] });
    const fixture = TestBed.createComponent(CompositionBar);
    fixture.componentRef.setInput('bands', [band({ value: 0, share: 0 })]);
    fixture.componentRef.setInput('emptyText', 'Nothing was used in this period.');
    fixture.detectChanges();
    const element = fixture.nativeElement as HTMLElement;

    expect(element.querySelector('[data-testid="blocks"]')).toBeNull();
    expect(element.textContent).toContain('Nothing was used in this period.');
  });

  it('hides the blocks from assistive technology, since their meaning is width and colour', () => {
    const { element } = setup([band()]);

    expect(element.querySelector('[data-testid="blocks"]')?.getAttribute('aria-hidden')).toBe(
      'true',
    );
  });
});

/** The browser normalises an inline colour to `rgb(...)`; the helper returns hex. */
function hexToRgb(hex: string): string {
  const channel = (offset: number) => parseInt(hex.slice(offset, offset + 2), 16);
  return `rgb(${channel(1)}, ${channel(3)}, ${channel(5)})`;
}
