import { TestBed } from '@angular/core/testing';
import { ReportRow } from '../../core/api/models';
import { BreakdownTable } from './breakdown-table';

function row(over: Partial<ReportRow> = {}): ReportRow {
  return {
    key: 'demo-uc',
    requests: 10,
    prompt_tokens: 100,
    completion_tokens: 200,
    total_tokens: 300,
    cost_nanos: 1_000_000_000,
    cost: '1.00',
    cached_input_tokens: 0,
    unpriced_requests: 0,
    failed_requests: 0,
    avg_latency_ms: 40,
    max_latency_ms: 90,
    ...over,
  };
}

function setup(rows: ReportRow[], label = 'Use case', emptyText?: string, item?: string) {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({ imports: [BreakdownTable] });
  const fixture = TestBed.createComponent(BreakdownTable);
  fixture.componentRef.setInput('label', label);
  fixture.componentRef.setInput('rows', rows);
  if (emptyText !== undefined) fixture.componentRef.setInput('emptyText', emptyText);
  if (item !== undefined) fixture.componentRef.setInput('item', item);
  fixture.detectChanges();
  const element = fixture.nativeElement as HTMLElement;
  return {
    fixture,
    element,
    text: () => element.textContent ?? '',
    bars: () => Array.from(element.querySelectorAll<HTMLElement>('.progress__bar')),
    search: () => element.querySelector<HTMLInputElement>('[data-testid="breakdown-search"]')!,
    keys: () =>
      Array.from(element.querySelectorAll('tbody th')).map((th) => th.textContent?.trim()),
  };
}

describe('BreakdownTable', () => {
  it('names its first column after what the breakdown is about', () => {
    // `input()` is a signal in a child: rendering the function instead of its value is invisible
    // to a test that only checks the component's own properties.
    const { element, text } = setup([row()], 'Member');
    const heading = element.querySelector('th');

    expect(heading?.textContent?.trim()).toBe('Member');
    expect(text()).toContain('By member');
    expect(text()).not.toContain('function');
  });

  it('renders a row with its spend, requests and token split', () => {
    const { text } = setup([row({ key: 'gemini-2.0', cost: '12.50' })], 'Model');

    expect(text()).toContain('gemini-2.0');
    expect(text()).toContain('12.50');
    expect(text()).toContain('100 / 200');
    expect(text()).toContain('40 ms / 90 ms');
  });

  it('says how much of the spend each row is, against the largest in the breakdown', () => {
    const { bars } = setup([
      row({ key: 'big', cost_nanos: 4_000_000_000 }),
      row({ key: 'small', cost_nanos: 1_000_000_000 }),
    ]);

    expect(bars()[0].style.width).toBe('100%');
    expect(bars()[1].style.width).toBe('25%');
  });

  it('draws no bar at all when nothing was spent, rather than dividing by zero', () => {
    const { bars } = setup([row({ cost_nanos: 0, cost: '0.00' })]);

    expect(bars()[0].style.width).toBe('0%');
  });

  it('marks a row whose spend is incomplete because some of it was unpriced', () => {
    expect(setup([row()]).text()).not.toContain('unpriced');
    expect(setup([row({ unpriced_requests: 2 })]).text()).toContain('2 unpriced');
  });

  it('reports failed requests alongside the total rather than hiding them', () => {
    expect(setup([row({ requests: 10, failed_requests: 3 })]).text()).toContain('(3 failed)');
    expect(setup([row({ failed_requests: 0 })]).text()).not.toContain('failed');
  });

  it('shows a dash where there is no latency to report', () => {
    const { text } = setup([row({ avg_latency_ms: null, max_latency_ms: null })]);

    expect(text()).toContain('—');
    expect(text()).not.toContain('null');
  });

  it('names one of its rows with the right article', () => {
    // Built from the first column's name, the search box asked to "Find a outcome".
    const { element, search } = setup([row({ key: 'served' })], 'Outcome', undefined, 'an outcome');

    expect(element.querySelector('.search span')?.textContent?.trim()).toBe('Find an outcome');
    expect(search().getAttribute('placeholder')).toBe('part of an outcome name');
  });

  it('opens another breakdown unfiltered, whatever was searched in the last one', () => {
    // A term typed among models means nothing among members; a table opening filtered by it says
    // "nothing matches" about rows that are there.
    const harness = setup([row({ key: 'gemini-2.0' }), row({ key: 'mock-1' })], 'Model');
    harness.search().value = 'gem';
    harness.search().dispatchEvent(new Event('input'));
    harness.fixture.detectChanges();
    expect(harness.keys()).toEqual(['gemini-2.0']);

    harness.fixture.componentRef.setInput('label', 'Member');
    harness.fixture.componentRef.setInput('rows', [row({ key: 'alice' }), row({ key: 'bob' })]);
    harness.fixture.detectChanges();

    expect(harness.search().value).toBe('');
    expect(harness.keys()).toEqual(['alice', 'bob']);
  });

  it('groups its counts, as every other figure in the console is', () => {
    const { text } = setup([
      row({ requests: 12840, prompt_tokens: 1_200_000, completion_tokens: 3400 }),
    ]);

    expect(text()).toContain('12,840');
    expect(text()).toContain('1,200,000 / 3,400');
  });

  it('says there is nothing, in the terms of this breakdown', () => {
    const { text, element } = setup([], 'Model', 'No model was called in this period.');

    expect(text()).toContain('No model was called in this period.');
    expect(element.querySelector('table')).toBeNull();
  });
});

describe('BreakdownTable — a spend nobody could compute (FRD-626 FR-18)', () => {
  it('shows a row where nothing could be priced as unknown, not as zero', () => {
    // Found on the running showcase: the local embedding model reports no tokens, so nothing could
    // be priced — and this column read `0.00` beside a price that is on file. `0.00` is a
    // measurement; this one was never made (`LESSONS.md` §2).
    const { element } = setup([
      row({ key: 'all-minilm', requests: 9, cost: '0.00', unpriced_requests: 9 }),
    ]);
    const spend = element.querySelector('tbody td')!;

    expect(spend.textContent).toContain('—');
    expect(spend.textContent).not.toContain('0.00');
    expect(spend.textContent).toContain('nothing priced');
  });

  it('says which kind of unknown a row’s unpriced requests are', () => {
    // FR-18's two causes on the row as well as in the caveat: a price fixes one and not the other.
    const mixed = setup([row({ requests: 10, unpriced_requests: 3, unmetered_requests: 1 })]);
    const mixedTitle = mixed.element.querySelector('tbody td span[title]')!.getAttribute('title');
    expect(mixedTitle).toContain('1 reported no token usage');
    expect(mixedTitle).toContain('2 ran on a model with no price on file');

    const unmetered = setup([row({ requests: 9, unpriced_requests: 9, unmetered_requests: 9 })]);
    const unmeteredTitle = unmetered.element
      .querySelector('tbody td span[title]')!
      .getAttribute('title');
    expect(unmeteredTitle).toContain('9 reported no token usage');
    expect(unmeteredTitle).not.toContain('no price on file');
  });

  it('still shows a lower bound where some of the row was priced', () => {
    // The narrowing: a row with one priced request has a figure, and that figure is a lower bound —
    // which is a different statement from "unknown" and keeps its own wording.
    const { element } = setup([row({ requests: 10, cost: '1.00', unpriced_requests: 3 })]);
    const spend = element.querySelector('tbody td')!;

    expect(spend.textContent).toContain('1.00');
    expect(spend.textContent).toContain('+ 3 unpriced');
    expect(spend.textContent).not.toContain('nothing priced');
  });
});
