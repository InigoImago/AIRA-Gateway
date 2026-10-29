import { MeService } from '../../core/api/me.service';
import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { provideRouter } from '@angular/router';
import { Observable, Subject, of, throwError } from 'rxjs';
import {
  Budget,
  Granularity,
  Report,
  ReportRow,
  SeriesSplit,
  UsageSeries,
} from '../../core/api/models';
import { SeriesRequest } from '../../core/api/clients/reporting';
import { UseCaseService } from '../../core/api/use-case.service';
import { InstallationBudgetCard } from './installation-budget-card';
import { ReportingPage } from './reporting-page';
import { InstallationClock } from '../../core/ui/clock';
import { isoDay, utcDay, windowFor } from '../../core/ui/periods';

function row(over: Partial<ReportRow> = {}): ReportRow {
  return {
    key: 'demo-uc',
    requests: 4,
    prompt_tokens: 40,
    completion_tokens: 80,
    total_tokens: 120,
    cost_nanos: 3_750_000_000,
    cost: '3.75',
    cached_input_tokens: 0,
    unpriced_requests: 0,
    failed_requests: 0,
    avg_latency_ms: 40,
    max_latency_ms: 90,
    ...over,
  };
}

/** The last day a `[from, to)` window includes, as the screen and the file name state it. */
function lastDayOf(to: string): string {
  const [year, month, day] = to.split('-').map(Number);
  return isoDay(new Date(year, month - 1, day - 1));
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
    by_outcome: [row({ key: 'served' })],
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
  exportBreakdown: { set: (v: string) => void; (): string };
  exporting: () => boolean;
  download: () => void;
}

/** A report per call, for a test about which of several answers ends up on screen. */
type Answer = Observable<Report> | ((from: string, to: string) => Observable<Report>);

function setup(
  response: Answer = of(report()),
  csv?: Observable<Blob>,
  budgets: () => Observable<Budget[]> = () => of([] as Budget[]),
  clock?: string,
  // `/v1/me`'s answer, for a test that decides when it arrives; at once otherwise.
  meAnswer?: Observable<unknown>,
) {
  TestBed.resetTestingModule();
  const calls: Array<{ from: string; to: string }> = [];
  const exports: Array<{ from: string; to: string; breakdown: string }> = [];
  const service = {
    report: (from: string, to: string) => {
      calls.push({ from, to });
      return typeof response === 'function' ? response(from, to) : response;
    },
    reportCsv: (from: string, to: string, breakdown: string) => {
      exports.push({ from, to, breakdown });
      return csv ?? of(new Blob(['key,requests\n'], { type: 'text/csv' }));
    },
    // The installation-budget card is a child and loads on its own; answered so it cannot throw
    // and take the page down with it.
    installationBudgets: budgets,
  };
  // A reader who may read every figure and set none: the card shows a configured limit and offers
  // no form, without the double reaching for a network.
  const me = {
    currency: signal(''),
    get: () =>
      meAnswer ?? of({ subject: 's', username: 'u', roles: [], permissions: ['report.read_all'] }),
  };
  TestBed.configureTestingModule({
    imports: [ReportingPage],
    providers: [
      provideRouter([]),
      { provide: UseCaseService, useValue: service },
      { provide: MeService, useValue: me },
    ],
  });
  // What `/v1/me` would have named, before the page asks for its first window.
  if (clock) TestBed.inject(InstallationClock).zone.set(clock);
  const fixture = TestBed.createComponent(ReportingPage);
  fixture.detectChanges();
  const element = fixture.nativeElement as HTMLElement;
  return {
    fixture,
    calls,
    exports,
    element,
    component: fixture.componentInstance as unknown as Page,
    text: () => element.textContent ?? '',
    testid: (id: string) => element.querySelector(`[data-testid="${id}"]`),
  };
}

const MONTHLY_CAP: Budget = {
  id: 7,
  scope: 'installation',
  subject: '',
  period: 'month',
  limit_cost: '20.000000',
  limit_tokens: null,
  limit_requests: null,
  enabled: true,
};

describe('ReportingPage', () => {
  it('labels spend in the installation’s currency', () => {
    // With a cap on the page: the budget card is the one place on it that prints an amount it was
    // not handed a label for, so a page with no budget could not show a stray symbol at all.
    const harness = setup(of(report()), undefined, () => of([MONTHLY_CAP]));
    TestBed.inject(MeService).currency.set('CHF');
    harness.fixture.detectChanges();

    expect(harness.text()).toContain('Spend (CHF)');
    expect(harness.testid('installation-budget')?.textContent).toContain('20.00 CHF');
    expect(harness.text()).not.toContain('$');
  });

  it('loads the current month on arrival, without the user asking for a period', () => {
    const { calls, text } = setup();

    expect(calls.length).toBe(1);
    // The gateway's month, not the reader's: its counters and buckets run on UTC.
    const now = new Date();
    expect(calls[0].from).toBe(
      utcDay(new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1))),
    );
    expect(text()).toContain('Reporting');
  });

  it('asks for the installation’s month and says which clock its days are on', () => {
    // 00:30 on 1 October in Berlin is still September in UTC (`FRD-629`).
    vi.useFakeTimers({ toFake: ['Date'], now: new Date('2026-09-30T22:30:00Z') });
    try {
      const { calls, testid } = setup(of(report()), undefined, undefined, 'Europe/Berlin');

      expect(calls[0]).toEqual({ from: '2026-10-01', to: '2026-11-01' });
      expect(testid('report-period')?.textContent).toContain('(Europe/Berlin)');
    } finally {
      vi.useRealTimers();
    }
  });

  it('renders the totals and the chosen breakdown', () => {
    const { text, testid } = setup();

    expect(testid('total-cost')?.textContent).toContain('3.75');
    expect(testid('total-requests')?.textContent).toContain('4');
    // Use case is where the page starts, and only that table is on screen.
    expect(text()).toContain('demo-uc');
    expect(text()).not.toContain('mock-1');
  });

  it('swaps the table when another breakdown is chosen', () => {
    const harness = setup();
    const component = harness.component;

    component.exportBreakdown.set('model');
    harness.fixture.detectChanges();
    expect(harness.text()).toContain('mock-1');
    expect(harness.text()).not.toContain('demo-uc');

    component.exportBreakdown.set('member');
    harness.fixture.detectChanges();
    expect(harness.text()).toContain('alice');
  });

  it('says whether the caller is seeing everything or only their own use cases', () => {
    expect(setup(of(report({ scope: 'all' }))).testid('scope')?.textContent).toContain(
      'Every use case',
    );
    expect(setup(of(report({ scope: 'use_cases' }))).testid('scope')?.textContent).toContain(
      'Your use cases only',
    );
  });

  it('counts refusals apart from successes, so a wall being hit is a number', () => {
    // Refusals are counted apart, so a use case grinding against a limit is not a quiet one
    // (FRD-122).
    const { testid } = setup(
      of(
        report({
          by_outcome: [
            row({ key: 'served', requests: 40 }),
            row({ key: 'rate_limited', requests: 7 }),
            row({ key: 'budget_exceeded', requests: 3 }),
          ],
        }),
      ),
    );

    expect(testid('total-refused')?.textContent).toContain('10');
  });

  it('reports no refusals when everything was served', () => {
    const { testid } = setup(of(report({ by_outcome: [row({ key: 'served', requests: 12 })] })));

    expect(testid('total-refused')?.textContent?.trim()).toBe('0');
  });

  it('counts a refusal and a failure each once, and a model check as neither', () => {
    // The server's `failed_requests` is every status of 400 or more, refusals included, so a
    // headline built from it counted a rate-limited request as failed *and* refused. Each figure
    // is built from the outcomes that mean it; a model check an administrator ran is served
    // traffic of nobody's and neither of the two.
    const { testid } = setup(
      of(
        report({
          totals: row({ key: 'total', requests: 19, failed_requests: 6 }),
          by_outcome: [
            row({ key: 'served', requests: 10 }),
            row({ key: 'diagnostic', requests: 3 }),
            row({ key: 'upstream_error', requests: 2 }),
            row({ key: 'rate_limited', requests: 4 }),
          ],
        }),
      ),
    );

    expect(testid('total-refused')?.textContent?.trim()).toBe('4');
    expect(testid('total-failed')?.textContent?.trim()).toBe('2');
  });

  it('counts every refusal the gateway makes, and no outcome it does not know', () => {
    // An allow-list: an outcome added to the gateway later is in neither figure until somebody
    // decides which one it belongs to, rather than silently in "refused".
    const refusals = [
      'rate_limited',
      'budget_exceeded',
      'suspended',
      'blocked_by_pipeline',
      'model_not_found',
      'invalid_request',
      'request_too_large',
      'no_capable_model',
    ];
    const { testid } = setup(
      of(
        report({
          by_outcome: [
            ...refusals.map((key) => row({ key, requests: 1 })),
            row({ key: 'client_gone', requests: 5 }),
            row({ key: 'internal_error', requests: 1 }),
            row({ key: 'upstream_error', requests: 2 }),
            row({ key: 'something_new', requests: 7 }),
          ],
        }),
      ),
    );

    expect(testid('total-refused')?.textContent?.trim()).toBe('8');
    expect(testid('total-failed')?.textContent?.trim()).toBe('3');
  });

  it('shows the outcome breakdown, and says plainly that it is not exported', () => {
    // The CSV renderer takes three breakdowns (`FRD-602`), so the button is replaced by the
    // reason rather than answering 400 (`FRD-206`).
    const harness = setup(of(report({ by_outcome: [row({ key: 'rate_limited', requests: 7 })] })));
    harness.component.exportBreakdown.set('outcome');
    harness.fixture.detectChanges();

    expect(harness.text()).toContain('By outcome');
    expect(harness.text()).toContain('rate_limited');
    expect(harness.testid('export-download')).toBeNull();
    expect(harness.testid('export-unavailable')?.textContent).toContain('not exported');
  });

  it('downloads the table that is on screen, never a different one', () => {
    // One idea of "which table" for the screen and the file, so the two cannot disagree.
    const harness = setup();
    harness.component.exportBreakdown.set('member');
    harness.fixture.detectChanges();
    harness.component.download();

    expect(harness.exports.at(-1)?.breakdown).toBe('member');
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

  it('says the spend is a lower bound when requests were booked at a floor, and only then', () => {
    // A stream whose caller left before the provider reported usage is booked at what could be
    // established (`FRD-128` FR-6); every such request makes the spend a floor.
    expect(setup().testid('partial-usage-caveat')).toBeNull();

    const withFloor = setup(
      of(report({ totals: row({ key: 'total', partial_usage_requests: 4 }) })),
    );
    const caveat = withFloor.testid('partial-usage-caveat');
    expect(caveat?.textContent).toContain('4 request(s)');
    expect(caveat?.textContent).toContain('a lower bound');
  });

  it('does not send somebody to add a price for an upstream that reported no usage', () => {
    // `FRD-626` FR-18: two unknowns with two remedies. A local embedding model that reports no
    // tokens already has its price on file, and a price cannot fix a count that was never made.
    const harness = setup(
      of(
        report({
          totals: row({
            key: 'total',
            requests: 9,
            cost: '0.00',
            cost_nanos: 0,
            unpriced_requests: 9,
            unmetered_requests: 9,
          }),
          by_outcome: [row({ key: 'served', requests: 9 })],
        }),
      ),
    );
    const caveat = harness.testid('unpriced-caveat');

    expect(caveat?.textContent).toContain('no token usage');
    expect(caveat?.textContent).not.toContain('no price on file');
    expect(caveat?.querySelector('a')).toBeNull();
  });

  it('names each cause with its own count, and links the one a price fixes', () => {
    const harness = setup(
      of(
        report({
          totals: row({ key: 'total', unpriced_requests: 5, unmetered_requests: 2 }),
        }),
      ),
    );
    const caveat = harness.testid('unpriced-caveat');

    expect(caveat?.textContent).toContain('5 request(s)');
    expect(caveat?.textContent).toMatch(/2 of them reported\s+no token usage/);
    expect(caveat?.textContent).toMatch(/3 of them ran on a model with\s+no price on file/);
    expect(caveat?.querySelector('a')?.getAttribute('href')).toBe('/models');
  });

  it('says the spend is unknown, not 0.00, when nothing that ran could be priced', () => {
    // The breakdown rows already do (FR-18); the headline printed the server's `0.00` above them.
    // A refusal ran nothing and has no cost to be unknown, so it does not keep the figure a figure.
    const allUnpriced = setup(
      of(
        report({
          totals: row({
            key: 'total',
            requests: 12,
            cost: '0.00',
            cost_nanos: 0,
            unpriced_requests: 9,
          }),
          by_outcome: [
            row({ key: 'served', requests: 9 }),
            row({ key: 'rate_limited', requests: 3 }),
          ],
        }),
      ),
    );
    expect(allUnpriced.testid('total-cost')?.textContent?.trim()).toBe('—');

    // Some of it priced: the figure stands, as a lower bound the caveat explains.
    const partly = setup(
      of(report({ totals: row({ key: 'total', cost: '3.75', unpriced_requests: 2 }) })),
    );
    expect(partly.testid('total-cost')?.textContent?.trim()).toBe('3.75');
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
    // creates it — so wait before reading the field.
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

  it('keeps a period the reader chose before `/v1/me` answered', async () => {
    // The clock arrives with `/v1/me` (`FRD-629`). A reader quicker than that keeps "custom" and
    // the two date fields, rather than being sent back to this month.
    const me = new Subject<unknown>();
    const page = setup(of(report()), undefined, undefined, undefined, me);
    page.component.applyPreset('custom');
    page.fixture.detectChanges();

    me.next({ subject: 's', username: 'u', roles: [], permissions: ['report.read_all'] });
    page.fixture.detectChanges();
    await page.fixture.whenStable();

    expect(page.component.preset()).toBe('custom');
    expect(page.element.querySelector('#report-from')).not.toBeNull();
    expect(page.calls.length).toBe(0);
  });

  it('computes a preset chosen before `/v1/me` answered on the clock it names', () => {
    // 00:30 on 1 October in Berlin: last month is September there and August in UTC.
    vi.useFakeTimers({ toFake: ['Date'], now: new Date('2026-09-30T22:30:00Z') });
    try {
      const me = new Subject<unknown>();
      const page = setup(of(report()), undefined, undefined, undefined, me);
      page.component.applyPreset('last-month');

      // What `MeService` does with the answer, before the page hears of it.
      TestBed.inject(InstallationClock).zone.set('Europe/Berlin');
      me.next({ subject: 's', username: 'u', roles: [], permissions: ['report.read_all'] });

      expect(page.component.preset()).toBe('last-month');
      expect(page.calls[page.calls.length - 1]).toEqual({ from: '2026-09-01', to: '2026-10-01' });
    } finally {
      vi.useRealTimers();
    }
  });

  it('asks the gateway for the window a preset means', () => {
    const page = setup();
    page.component.applyPreset('last-month');

    const asked = page.calls[page.calls.length - 1];
    expect(asked.from).toBe(page.component.from());
    expect(asked.to).toBe(page.component.to());
  });

  it('shows the period asked for last, whichever answer arrives last', () => {
    // A month is a slower query than a week. Switching from one to the other quickly must not
    // leave the first period's figures on screen under the second period's name.
    const pending = new Map<string, Subject<Report>>();
    const page = setup((from) => {
      const answer = new Subject<Report>();
      pending.set(from, answer);
      return answer;
    });
    const thisMonth = page.component.from();

    page.component.applyPreset('last-month');
    const lastMonth = page.component.from();
    pending.get(lastMonth)!.next(report({ totals: row({ key: 'total', cost: '111.00' }) }));
    pending.get(thisMonth)!.next(report({ totals: row({ key: 'total', cost: '999.00' }) }));
    page.fixture.detectChanges();

    expect(page.component.preset()).toBe('last-month');
    expect(page.testid('total-cost')?.textContent).toContain('111.00');
    expect(page.text()).not.toContain('999.00');
  });

  it('says which period the figures on screen are for', () => {
    // The window the report was asked for, ending on the last day it includes — so a custom date
    // typed and not yet shown cannot be mistaken for the period of the figures beside it.
    const page = setup();
    const shown = windowFor('this-month', new Date());
    page.component.applyPreset('custom');
    page.component.from.set('2025-01-01');
    page.component.to.set('2025-02-01');
    page.fixture.detectChanges();

    const period = page.testid('report-period')?.textContent ?? '';
    expect(period).toContain(shown.from);
    expect(period).toContain(lastDayOf(shown.to));
    // Days on the gateway's clock, which is not the reader's: said, not assumed.
    expect(period).toContain('(UTC)');
    expect(period).not.toContain('2025-01-01');
  });

  it('does not leave the budget card blank under a banner the next period cleared', () => {
    // The page clears its one banner for a new period. A load failure the card reported there was
    // the only thing saying why the card is empty, so the card is asked again rather than left
    // blank under a banner that no longer mentions it.
    let asked = 0;
    const page = setup(of(report()), undefined, () =>
      ++asked === 1
        ? (throwError(() => ({
            status: 502,
            error: { error: { message: 'budget store down' } },
          })) as Observable<Budget[]>)
        : of([MONTHLY_CAP]),
    );
    expect(page.element.querySelector('[role="alert"]')?.textContent).toContain(
      'budget store down',
    );

    page.component.applyPreset('last-month');
    page.fixture.detectChanges();

    expect(page.element.querySelector('[role="alert"]')).toBeNull();
    expect(page.testid('installation-budget')?.textContent).toContain('20.00');
  });

  describe('the CSV export (FRD-602)', () => {
    /** The download needs the bearer token, so it is a fetch plus an object URL, not a link. */
    function captureDownload() {
      const clicks: Array<{ download: string; href: string }> = [];
      const created: string[] = [];
      const revoked: string[] = [];
      const realCreate = URL.createObjectURL;
      const realRevoke = URL.revokeObjectURL;
      const realClick = HTMLAnchorElement.prototype.click;

      URL.createObjectURL = ((blob: Blob) => {
        const url = `blob:test/${created.length}`;
        created.push(url);
        void blob;
        return url;
      }) as typeof URL.createObjectURL;
      URL.revokeObjectURL = ((url: string) => revoked.push(url)) as typeof URL.revokeObjectURL;
      HTMLAnchorElement.prototype.click = function (this: HTMLAnchorElement) {
        clicks.push({ download: this.download, href: this.href });
      };

      return {
        clicks,
        created,
        revoked,
        restore: () => {
          URL.createObjectURL = realCreate;
          URL.revokeObjectURL = realRevoke;
          HTMLAnchorElement.prototype.click = realClick;
        },
      };
    }

    it('downloads the period on screen, for the table the user chose', () => {
      const capture = captureDownload();
      try {
        const { component, exports, calls } = setup();
        component.exportBreakdown.set('model');
        component.download();

        expect(exports.length).toBe(1);
        expect(exports[0].breakdown).toBe('model');
        // The window the figures on screen were asked for.
        expect(exports[0].from).toBe(calls[0].from);
        expect(exports[0].to).toBe(calls[0].to);
      } finally {
        capture.restore();
      }
    });

    it('downloads the period whose figures are on screen, not dates typed since', () => {
      // "A download is exactly this table": dates typed into Custom… and not yet shown are not the
      // table's period, and a file for them would be a file nobody has looked at.
      const capture = captureDownload();
      try {
        const page = setup();
        const shown = page.calls[0];
        page.component.applyPreset('custom');
        page.component.from.set('2025-01-01');
        page.component.to.set('2025-02-01');
        page.fixture.detectChanges();

        page.component.download();

        expect(page.exports).toEqual([{ ...shown, breakdown: 'use_case' }]);
      } finally {
        capture.restore();
      }
    });

    it('names the file so it says what it contains and sorts', () => {
      const capture = captureDownload();
      try {
        const { component, calls } = setup();
        component.download();

        expect(capture.clicks.length).toBe(1);
        // The last day included, as the screen says it — not the exclusive bound, which named a
        // file for October after the first of November.
        expect(capture.clicks[0].download).toBe(
          `aira-usage_use_case_${calls[0].from}_${lastDayOf(calls[0].to)}.csv`,
        );
      } finally {
        capture.restore();
      }
    });

    it('releases the object URL, so a dozen exports do not pin a dozen blobs in memory', () => {
      const capture = captureDownload();
      try {
        setup().component.download();
        expect(capture.revoked).toEqual(capture.created);
      } finally {
        capture.restore();
      }
    });

    it('reports a failed export instead of appearing to do nothing', () => {
      const capture = captureDownload();
      try {
        const failing = throwError(() => ({ status: 500 }));
        const { component, text, fixture } = setup(of(report()), failing as Observable<Blob>);
        component.download();
        // Zoneless: the banner is a signal, and renders only on the next pass.
        fixture.detectChanges();

        expect(component.exporting()).toBe(false);
        expect(text()).toContain('Could not export');
      } finally {
        capture.restore();
      }
    });

    it('does not start a second export while one is running', () => {
      const capture = captureDownload();
      try {
        // An observable that never emits: the first export is still in flight.
        const pending = new Observable<Blob>(() => {});
        const { component, exports } = setup(of(report()), pending);
        component.download();
        component.download();

        expect(exports.length).toBe(1);
      } finally {
        capture.restore();
      }
    });

    it('says that Excel may ask about the separator, rather than letting it surprise somebody', () => {
      // RFC 4180 is right for every tool and script; German Excel will still ask. Being told
      // beforehand is the difference between a quirk and a bug report.
      expect(setup().text()).toContain('separator');
    });
  });
});

describe('windowFor', () => {
  // Noon UTC on 6 August 2026: the windows are the gateway's UTC days, so the moment is fixed in
  // UTC rather than built from the test machine's local calendar.
  const today = new Date(Date.UTC(2026, 7, 6, 12));

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
    expect(windowFor('last-month', new Date(Date.UTC(2027, 0, 15, 12)))).toEqual({
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

describe('ReportingPage — what each figure counts', () => {
  it('shows the explanation on hover, and hides it again on leaving', () => {
    // What anybody reaching for an "i" expects.
    const { fixture, testid } = setup();
    const button = testid('info-stat-refused')!;

    expect(testid('help-stat-refused')).toBeNull();

    button.dispatchEvent(new Event('mouseenter'));
    fixture.detectChanges();
    expect(testid('help-stat-refused')?.textContent).toContain('never reached a model');

    button.dispatchEvent(new Event('mouseleave'));
    fixture.detectChanges();
    expect(testid('help-stat-refused')).toBeNull();
  });

  it('shows it for a keyboard too, on focus', () => {
    // A tooltip only a mouse can reach is a tooltip half the readers never see.
    const { fixture, testid } = setup();
    const button = testid('info-stat-latency')!;

    button.dispatchEvent(new Event('focus'));
    fixture.detectChanges();
    expect(testid('help-stat-latency')).not.toBeNull();

    button.dispatchEvent(new Event('blur'));
    fixture.detectChanges();
    expect(testid('help-stat-latency')).toBeNull();
  });

  it('pins the explanation open when clicked, because a touch screen has no hover', () => {
    const { fixture, testid } = setup();

    expect(testid('help-stat-refused')).toBeNull();

    testid('info-stat-refused')?.dispatchEvent(new Event('click'));
    // A click on a touch screen fires no hover, and the pointer leaves straight away.
    testid('info-stat-refused')?.dispatchEvent(new Event('mouseleave'));
    fixture.detectChanges();

    const help = testid('help-stat-refused');
    expect(help).not.toBeNull();
    // The sentence has to say the thing the short heading cannot: these never reached a model.
    expect(help?.textContent).toContain('never reached a model');
    expect(testid('info-stat-refused')?.getAttribute('aria-expanded')).toBe('true');
  });

  it('closes on a second use, and shows one explanation at a time', () => {
    const { fixture, testid } = setup();

    testid('info-stat-refused')?.dispatchEvent(new Event('click'));
    testid('info-stat-refused')?.dispatchEvent(new Event('mouseleave'));
    fixture.detectChanges();
    testid('info-stat-tokens')?.dispatchEvent(new Event('click'));
    testid('info-stat-tokens')?.dispatchEvent(new Event('mouseleave'));
    fixture.detectChanges();

    // Six open paragraphs is a wall of text where six figures were.
    expect(testid('help-stat-refused')).toBeNull();
    expect(testid('help-stat-tokens')).not.toBeNull();

    testid('info-stat-tokens')?.dispatchEvent(new Event('click'));
    testid('info-stat-tokens')?.dispatchEvent(new Event('mouseleave'));
    fixture.detectChanges();
    expect(testid('help-stat-tokens')).toBeNull();
  });

  it('still renders every figure, with its unit beside the number rather than in the heading', () => {
    const { element, testid } = setup();

    expect(testid('total-cost')).not.toBeNull();
    expect(testid('total-requests')).not.toBeNull();
    expect(testid('total-failed')).not.toBeNull();
    expect(testid('total-refused')).not.toBeNull();
    expect(element.querySelectorAll('.stat').length).toBe(6);
    expect(element.textContent).toContain('prompt / completion');
  });

  it('says a dash for a period with no latency, and drops the unit with it', () => {
    // A period with no served request has no latency. Printing "— average / maximum" labels a
    // measurement that was never taken.
    const { element, text } = setup(
      of(report({ totals: row({ key: 'total', avg_latency_ms: null, max_latency_ms: null }) })),
    );

    expect(text()).toContain('—');
    expect(element.textContent).not.toContain('average / maximum');
  });
});

describe('ReportingPage — each breakdown names itself', () => {
  it('titles the table, the pager and the empty state in its own terms', () => {
    // "No traffic from any use case" and "Nobody made a request" are different sentences about
    // different things; one empty state for all four would be wrong three times out of four.
    const empty = report({ by_use_case: [], by_model: [], by_member: [], by_outcome: [] });

    const harness = setup(of(empty));
    expect(harness.text()).toContain('No traffic from any use case');

    harness.component.exportBreakdown.set('model');
    harness.fixture.detectChanges();
    expect(harness.text()).toContain('By model');
    expect(harness.text()).toContain('No model was called');

    harness.component.exportBreakdown.set('member');
    harness.fixture.detectChanges();
    expect(harness.text()).toContain('By member');
    expect(harness.text()).toContain('Nobody made a request');

    harness.component.exportBreakdown.set('outcome');
    harness.fixture.detectChanges();
    expect(harness.text()).toContain('By outcome');
    expect(harness.text()).toContain('Nothing happened in this period');
  });

  it('counts the rows of whichever breakdown is shown', () => {
    const harness = setup(of(report({ by_member: [row({ key: 'alice' }), row({ key: 'bob' })] })));
    harness.component.exportBreakdown.set('member');
    harness.fixture.detectChanges();

    expect(harness.text()).toContain('1–2 of 2 members');
  });
});

describe('ReportingPage — what the figures say they count', () => {
  it('says that a request budget and the request count differ on an embedding batch', () => {
    // The text used to say a batch "counts as the many texts it carries, not as one". That is true
    // of a budget (`FRD-113` FR-6) and false of this figure: the audit writes one row per call —
    // `gateway/tests/test_serving_options.py` asserts exactly one for a batch of two — and this
    // counts rows. Both rules are deliberate; a reader reconciling one against the other has to be
    // told, or the report is the half they stop believing (`FRD-206`).
    const harness = setup();
    const help = (harness.component as unknown as { stats: () => { key: string; help: string }[] })
      .stats()
      .find((stat) => stat.key === 'requests')!.help;

    expect(help).toContain('one per call');
    expect(help).toContain('budget');
    expect(help).not.toContain('not as one');
  });
});

/**
 * The page passes enforcement down to the card that sets the installation's budget
 * (`FRD-610` §3.3).
 *
 * Asserted on the **child's input** rather than on its rendered banner: what the card does with
 * the three states is its own spec's subject, and the card draws nothing at all until `/v1/me`
 * has answered. What can only be wrong here is the wiring — and `?? null` in particular, since a
 * report from a gateway that never had the field must not become a claim.
 */
describe('ReportingPage — whether budgets are enforced', () => {
  function cardInput(response: Observable<Report>): boolean | null {
    const { fixture } = setup(response);
    const card = fixture.debugElement.query(By.directive(InstallationBudgetCard));
    return (card.componentInstance as { enforced: () => boolean | null }).enforced();
  }

  it("hands the gateway's answer to the installation-budget card", () => {
    expect(cardInput(of(report({ enforced: false })))).toBe(false);
    expect(cardInput(of(report({ enforced: true })))).toBe(true);
  });

  it('hands down "not answered" rather than an answer of its own', () => {
    expect(cardInput(of(report()))).toBeNull();
  });
});

describe('ReportingPage — strict data mode (`FRD-627` FR-5)', () => {
  it('shows the members without a latency column, and keeps the latency of the totals', () => {
    const { avg_latency_ms: _avg, max_latency_ms: _max, ...alice } = row({ key: 'alice' });
    const harness = setup(of(report({ strict_data: true, by_member: [alice] })));
    harness.component.exportBreakdown.set('member');
    harness.fixture.detectChanges();
    const table = harness.element.querySelector('app-breakdown-table')!;

    expect(table.textContent).toContain('alice');
    expect(table.querySelector('[data-testid="latency-column"]')).toBeNull();
    expect(table.textContent).not.toContain(' ms');
    // The installation's own latency is not about a person, and stays.
    expect(harness.text()).toContain('40 / 90 ms');

    harness.component.exportBreakdown.set('model');
    harness.fixture.detectChanges();
    expect(
      harness.element.querySelector('app-breakdown-table [data-testid="latency-column"]'),
    ).not.toBeNull();
  });
});

describe('ReportingPage — a breakdown by member for a reader limited to their own row', () => {
  const offered = (harness: ReturnType<typeof setup>) =>
    Array.from(
      harness.element.querySelectorAll<HTMLOptionElement>(
        '[data-testid="export-breakdown"] option',
      ),
    ).map((option) => option.value);

  it('does not offer it when the gateway narrowed people to the reader, in either mode', () => {
    for (const strict_data of [true, false]) {
      const harness = setup(of(report({ people_limited: true, strict_data })));

      expect(offered(harness)).toEqual(['use_case', 'model', 'outcome']);
      harness.component.exportBreakdown.set('outcome');
      harness.fixture.detectChanges();
      expect(harness.testid('export-unavailable')?.textContent).toContain('use case or model');
      expect(harness.testid('export-unavailable')?.textContent).not.toContain('member');
    }
  });

  it('offers it to a reader whose report was not narrowed', () => {
    const harness = setup(of(report({ people_limited: false, strict_data: true })));

    expect(offered(harness)).toEqual(['use_case', 'model', 'member', 'outcome']);
  });

  it('leaves the member breakdown — and its download — when a narrowed report arrives', () => {
    let limited = false;
    const harness = setup(() => of(report({ people_limited: limited })));
    harness.component.exportBreakdown.set('member');
    limited = true;
    harness.component.load();
    harness.fixture.detectChanges();

    expect(harness.component.exportBreakdown()).toBe('use_case');
    expect(harness.text()).not.toContain('By member');
    harness.component.download();
    expect(harness.exports.at(-1)?.breakdown).toBe('use_case');
  });
});
