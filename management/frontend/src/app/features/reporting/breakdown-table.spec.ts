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
    unpriced_requests: 0,
    failed_requests: 0,
    avg_latency_ms: 40,
    max_latency_ms: 90,
    ...over,
  };
}

function setup(rows: ReportRow[], label = 'Use case', emptyText?: string) {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({ imports: [BreakdownTable] });
  const fixture = TestBed.createComponent(BreakdownTable);
  fixture.componentRef.setInput('label', label);
  fixture.componentRef.setInput('rows', rows);
  if (emptyText !== undefined) fixture.componentRef.setInput('emptyText', emptyText);
  fixture.detectChanges();
  const element = fixture.nativeElement as HTMLElement;
  return {
    fixture,
    element,
    text: () => element.textContent ?? '',
    bars: () => Array.from(element.querySelectorAll<HTMLElement>('.progress__bar')),
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

  it('says there is nothing, in the terms of this breakdown', () => {
    const { text, element } = setup([], 'Model', 'No model was called in this period.');

    expect(text()).toContain('No model was called in this period.');
    expect(element.querySelector('table')).toBeNull();
  });
});
