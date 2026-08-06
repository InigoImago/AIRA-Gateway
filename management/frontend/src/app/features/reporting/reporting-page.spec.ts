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

function setup(response: Observable<Report> = of(report()), csv?: Observable<Blob>) {
  TestBed.resetTestingModule();
  const calls: Array<{ from: string; to: string }> = [];
  const exports: Array<{ from: string; to: string; breakdown: string }> = [];
  const service = {
    report: (from: string, to: string) => {
      calls.push({ from, to });
      return response;
    },
    reportCsv: (from: string, to: string, breakdown: string) => {
      exports.push({ from, to, breakdown });
      return csv ?? of(new Blob(['key,requests\n'], { type: 'text/csv' }));
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
    exports,
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

  it('counts refusals apart from successes, so a wall being hit is a number', () => {
    // A use case grinding against its budget all day used to look like a quiet one: the
    // refusals were 429s and nothing said which control produced them (FRD-122).
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

  it('renders the outcome breakdown alongside the other three', () => {
    const { text } = setup(of(report({ by_outcome: [row({ key: 'rate_limited', requests: 7 })] })));

    expect(text()).toContain('By outcome');
    expect(text()).toContain('rate_limited');
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

  describe('the CSV export (FRD-602)', () => {
    /**
     * The download needs the bearer token, so it is a fetch plus an object URL rather than a
     * plain `<a href>` — a link that 401s looks like a broken export rather than like a browser
     * that cannot authenticate.
     */
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
        const { component, exports } = setup();
        component.exportBreakdown.set('model');
        component.download();

        expect(exports.length).toBe(1);
        expect(exports[0].breakdown).toBe('model');
        // The same window as the screen — an export of a *different* period than the one being
        // looked at is the kind of thing nobody notices until the numbers are in a meeting.
        expect(exports[0].from).toBe(component.from());
        expect(exports[0].to).toBe(component.to());
      } finally {
        capture.restore();
      }
    });

    it('names the file so it says what it contains and sorts', () => {
      const capture = captureDownload();
      try {
        const { component } = setup();
        component.download();

        expect(capture.clicks.length).toBe(1);
        expect(capture.clicks[0].download).toContain('aira-usage_use_case_');
        expect(capture.clicks[0].download.endsWith('.csv')).toBe(true);
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
        // Zoneless: the banner is a signal, and a signal changed from code renders on the next
        // pass. Asserting without one is how a component "has no error message" in a test while
        // showing one perfectly well in a browser.
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
