import { TestBed } from '@angular/core/testing';
import { Observable, of, throwError } from 'rxjs';
import { Report, ReportRow } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { ReportingPage, isoDay, windowFor } from './reporting-page';

function row(over: Partial<ReportRow> = {}): ReportRow {
  return {
    key: 'demo-uc',
    requests: 4,
    prompt_tokens: 40,
    completion_tokens: 80,
    total_tokens: 120,
    cost_nanos: 3_750_000_000,
    cost: '3.75',
    unpriced_requests: 0,
    failed_requests: 0,
    avg_latency_ms: 40,
    max_latency_ms: 90,
    ...over,
  };
}

function report(over: Partial<Report> = {}): Report {
  return {
    from: '2026-08-01',
    to: '2026-09-01',
    scope: 'all',
    totals: row({ key: 'total' }),
    by_use_case: [row()],
    by_model: [row({ key: 'mock-1' })],
    by_member: [row({ key: 'alice' })],
    ...over,
  };
}

interface Page {
  preset: { set: (v: string) => void; (): string };
  from: { set: (v: string) => void; (): string };
  to: { set: (v: string) => void; (): string };
  applyPreset: (p: 'this-month' | 'last-month' | 'last-7-days' | 'last-30-days' | 'custom') => void;
  load: () => void;
  validationError: () => string | null;
  canLoad: () => boolean;
  inclusiveEnd: () => string;
}

function setup(response: Observable<Report> = of(report())) {
  TestBed.resetTestingModule();
  const calls: Array<{ from: string; to: string }> = [];
  const service = {
    report: (from: string, to: string) => {
      calls.push({ from, to });
      return response;
    },
  };
  TestBed.configureTestingModule({
    imports: [ReportingPage],
    providers: [{ provide: UseCaseService, useValue: service }],
  });
  const fixture = TestBed.createComponent(ReportingPage);
  fixture.detectChanges();
  const element = fixture.nativeElement as HTMLElement;
  return {
    fixture,
    calls,
    element,
    component: fixture.componentInstance as unknown as Page,
    text: () => element.textContent ?? '',
    testid: (id: string) => element.querySelector(`[data-testid="${id}"]`),
  };
}

describe('ReportingPage', () => {
  it('loads the current month on arrival, without the user asking for a period', () => {
    const { calls, text } = setup();

    expect(calls.length).toBe(1);
    const today = new Date();
    expect(calls[0].from).toBe(isoDay(new Date(today.getFullYear(), today.getMonth(), 1)));
    expect(text()).toContain('Reporting');
  });

  it('renders the totals and all three breakdowns', () => {
    const { text, testid } = setup();

    expect(testid('total-cost')?.textContent).toContain('3.75');
    expect(testid('total-requests')?.textContent).toContain('4');
    // One row from each breakdown, so a missing panel cannot pass as a rendered page.
    expect(text()).toContain('demo-uc');
    expect(text()).toContain('mock-1');
    expect(text()).toContain('alice');
  });

  it('says whether the caller is seeing everything or only their own use cases', () => {
    expect(setup(of(report({ scope: 'all' }))).testid('scope')?.textContent).toContain(
      'Every use case',
    );
    expect(setup(of(report({ scope: 'use_cases' }))).testid('scope')?.textContent).toContain(
      'Your use cases only',
    );
  });

  it('shows the unpriced caveat only when there is unpriced traffic', () => {
    expect(setup().testid('unpriced-caveat')).toBeNull();

    const withUnpriced = setup(of(report({ totals: row({ key: 'total', unpriced_requests: 3 }) })));
    const caveat = withUnpriced.testid('unpriced-caveat');
    expect(caveat).not.toBeNull();
    // The wording is the point of the caveat, not merely that a box appeared.
    expect(caveat?.textContent).toContain('unknown, not zero');
    expect(caveat?.textContent).toContain('3 request(s)');
  });

  it('reports a failed load instead of rendering zeroes', () => {
    const failed = throwError(() => ({
      status: 502,
      error: { error: { message: 'gateway is not reachable' } },
    }));
    const { element, text } = setup(failed as Observable<Report>);

    const alert = element.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain('gateway is not reachable');
    // Nothing that could be mistaken for a figure: no totals block was rendered at all.
    expect(text()).not.toContain('Totals');
  });

  it('does not request a window that ends before it starts', () => {
    const page = setup();
    page.component.applyPreset('custom');
    page.component.from.set('2026-08-10');
    page.component.to.set('2026-08-01');
    page.fixture.detectChanges();

    const before = page.calls.length;
    page.component.load();

    expect(page.calls.length).toBe(before);
    expect(page.component.validationError()).toContain('after the start date');
    expect(page.component.canLoad()).toBe(false);
    expect(page.text()).toContain('after the start date');
  });

  it('keeps the dates in the fields when the user switches to a custom period', async () => {
    const page = setup();
    const chosen = page.component.from();

    page.component.applyPreset('custom');
    page.fixture.detectChanges();
    // `ngModel` writes into a newly created control on a microtask, not during the pass that
    // creates it. Asserting straight after `detectChanges` would read an empty field and call
    // that a bug in the component.
    await page.fixture.whenStable();
    page.fixture.detectChanges();

    // Switching to "custom" must not blank the fields the user is about to adjust.
    expect(page.component.from()).toBe(chosen);
    const input = page.element.querySelector<HTMLInputElement>('#report-from');
    expect(input?.value).toBe(chosen);
  });

  it('shows the last day included, not the exclusive bound the API is given', () => {
    const page = setup();
    page.component.applyPreset('custom');
    page.component.to.set('2026-09-01');
    page.fixture.detectChanges();

    expect(page.component.inclusiveEnd()).toBe('2026-08-31');
    expect(page.text()).toContain('ends on 2026-08-31');
  });

  it('asks the gateway for the window a preset means', () => {
    const page = setup();
    page.component.applyPreset('last-month');

    const asked = page.calls[page.calls.length - 1];
    expect(asked.from).toBe(page.component.from());
    expect(asked.to).toBe(page.component.to());
  });
});

describe('windowFor', () => {
  const today = new Date(2026, 7, 6); // 6 August 2026, local

  it('covers the whole current month, exclusive at the end', () => {
    expect(windowFor('this-month', today)).toEqual({ from: '2026-08-01', to: '2026-09-01' });
  });

  it('covers the previous month and stops where this one begins', () => {
    expect(windowFor('last-month', today)).toEqual({ from: '2026-07-01', to: '2026-08-01' });
  });

  it('includes today in the trailing windows', () => {
    // The exclusive end is tomorrow: a report of "the last 7 days" that stopped at midnight
    // would silently omit everything that happened today, which is what people look at first.
    expect(windowFor('last-7-days', today)).toEqual({ from: '2026-07-31', to: '2026-08-07' });
    expect(windowFor('last-30-days', today)).toEqual({ from: '2026-07-08', to: '2026-08-07' });
  });

  it('crosses a year boundary without landing in the wrong year', () => {
    expect(windowFor('last-month', new Date(2027, 0, 15))).toEqual({
      from: '2026-12-01',
      to: '2027-01-01',
    });
  });
});

describe('isoDay', () => {
  it('formats the local day rather than converting to UTC first', () => {
    // 23:30 local on the 6th. `toISOString()` would give the 7th anywhere west of UTC and the
    // 6th elsewhere — the period would depend on the viewer's time of day.
    expect(isoDay(new Date(2026, 7, 6, 23, 30))).toBe('2026-08-06');
    expect(isoDay(new Date(2026, 0, 1, 0, 15))).toBe('2026-01-01');
  });
});
