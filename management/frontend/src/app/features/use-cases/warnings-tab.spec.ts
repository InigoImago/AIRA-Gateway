import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Observable, of, throwError } from 'rxjs';
import { AnomalyEvent, AnomalyPage, Suspension } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { PageFeedback } from '../../core/ui/page-feedback';
import { RecentFindings, WarningsTab, recentFindings } from './warnings-tab';

/** A finding from a minute ago — recent, as far as the tab's badge is concerned. */
const minutesAgo = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();

const EVENT: AnomalyEvent = {
  id: 'e1',
  created_at: minutesAgo(1),
  rule: 'spend spike',
  kind: 'spend_spike',
  use_case: 'uc-a',
  target: 'use_case',
  target_value: 'uc-a',
  observed: 437,
  threshold: 311,
  sample: 40,
  window_minutes: 60,
  action_taken: 'alert',
  detail: 'spend is 4x the preceding hour',
};

const SUSPENSION: Suspension = {
  id: 's1',
  created_at: '2026-08-08T10:00:00Z',
  use_case: 'uc-a',
  target: 'use_case',
  target_value: 'uc-a',
  action: 'block',
  throttle_rpm: null,
  expires_at: '2099-01-01T00:00:00Z',
  author: 'user:itsec',
  reason: 'runaway client',
  lifted_at: null,
  lifted_by: null,
};

/** Hosted, because `slug` is a required `input()` and `Live`/`PageFeedback` are per-component. */
@Component({
  selector: 'app-warnings-host',
  imports: [WarningsTab],
  template: `<app-warnings-tab [slug]="slug()" (countChanged)="count.set($event)" />`,
  // Only `PageFeedback`: the tab provides its own `Live`, exactly as it does in
  // production, and a harness that provided it here would be testing a different
  // component from the one that ships.
  providers: [PageFeedback],
})
class Host {
  readonly slug = signal('uc-a');
  readonly count = signal<RecentFindings | null>(null);
}

interface Options {
  events?: AnomalyEvent[];
  nextCursor?: string | null;
  /** What a "load older" press answers. */
  older?: AnomalyEvent[];
  /** What every first-page load fails with. */
  anomaliesFail?: unknown;
  /** The stops each poll answers, in order; the last repeats. */
  suspensions?: Observable<{ suspensions: Suspension[] }>[];
  inScope?: boolean;
}

function setup(options: Options = {}) {
  TestBed.resetTestingModule();
  const asked: { useCase?: string; cursor?: string }[] = [];
  const askedStops: (string | undefined)[] = [];
  const stops = options.suspensions ?? [of({ suspensions: [SUSPENSION] })];
  TestBed.configureTestingModule({
    imports: [Host],
    providers: [
      {
        provide: UseCaseService,
        useValue: {
          anomalies: (
            limit: number,
            useCase?: string,
            cursor?: string,
          ): Observable<AnomalyPage> => {
            asked.push({ useCase, cursor });
            if (cursor) {
              return of({ next_cursor: null, events: options.older ?? [], scope: 'use_cases' });
            }
            if (options.anomaliesFail) return throwError(() => options.anomaliesFail);
            return of({
              next_cursor: options.nextCursor ?? null,
              events: options.events ?? [EVENT],
              scope: 'use_cases',
              ...(options.inScope === undefined ? {} : { in_scope: options.inScope }),
            });
          },
          suspensions: (useCase?: string) => {
            askedStops.push(useCase);
            return stops[Math.min(askedStops.length - 1, stops.length - 1)];
          },
        },
      },
    ],
  });
  const fixture = TestBed.createComponent(Host);
  fixture.detectChanges();
  const element = fixture.nativeElement as HTMLElement;
  return {
    fixture,
    element,
    asked,
    askedStops,
    host: fixture.componentInstance,
    feedback: () => fixture.debugElement.injector.get(PageFeedback),
    text: () => element.textContent ?? '',
    testid: (id: string) => element.querySelector(`[data-testid="${id}"]`),
    click: (id: string) => {
      element.querySelector<HTMLElement>(`[data-testid="${id}"]`)?.click();
      fixture.detectChanges();
    },
  };
}

describe('WarningsTab', () => {
  it('shows the findings about this use case, with the numbers behind them', () => {
    // A warning only IT Security can see is a warning nobody who could fix the cause ever reads.
    const { text } = setup();

    expect(text()).toContain('spend spike');
    expect(text()).toContain('437');
    expect(text()).toContain('311');
  });

  it('asks the server for this use case rather than filtering a global page', () => {
    // Filtering in the browser would show a quiet use case nothing on a busy installation: its
    // findings would have been pushed off the end of the newest hundred by somebody else's.
    const { asked } = setup();
    expect(asked.map((ask) => ask.useCase)).toContain('uc-a');
  });

  it('does not print "nothing found" when the truth is "you cannot see this"', () => {
    const { testid, text } = setup({ events: [], inScope: false });

    expect(testid('no-warnings')).toBeNull();
    expect(testid('not-in-scope')?.textContent).toContain('identity provider');
    expect(text()).toContain('/use-cases/uc-a');
  });

  it('tells the parent how many recent findings there are, for the tab badge', () => {
    const { host } = setup({ events: [EVENT, { ...EVENT, id: 'e2' }] });
    expect(host.count()).toEqual({ count: 2, more: false });
  });

  it('puts "you are stopped right now" above everything else', () => {
    // The one fact a member needs before reading anything: requests are being refused, and this
    // is why. Without it the 429s look like a broken gateway.
    const { testid } = setup();
    const banner = testid('stopped-banner');

    expect(banner?.textContent).toContain('runaway client');
    expect(banner?.getAttribute('role')).toBe('alert');
  });

  it('does not claim a lifted or expired suspension is in force', () => {
    const lifted = { ...SUSPENSION, id: 's2', lifted_at: '2026-08-08T11:00:00Z' };
    const expired = { ...SUSPENSION, id: 's3', expires_at: '2020-01-01T00:00:00Z' };
    const { testid } = setup({ suspensions: [of({ suspensions: [lifted, expired] })] });

    expect(testid('stopped-banner')).toBeNull();
  });

  it('stays a working page when suspensions are refused', () => {
    // A caller the gateway refuses gets a 403. That is a real answer about a real permission, and
    // must not turn the whole tab into an error.
    const harness = setup({ suspensions: [throwError(() => ({ status: 403 }))] });

    expect(harness.testid('stopped-banner')).toBeNull();
    expect(harness.testid('stops-unknown')).toBeNull();
    expect(harness.feedback().error()).toBeNull();
    expect(harness.text()).toContain('spend spike');
  });

  it('says "nothing found" rather than showing an empty table', () => {
    const { testid, host } = setup({ events: [] });

    expect(testid('no-warnings')).not.toBeNull();
    expect(host.count()).toEqual({ count: 0, more: false });
  });
});

describe('WarningsTab — when something cannot be read', () => {
  it('reports a failed load in the page banner instead of loading for ever', () => {
    // No load reported anything: a gateway that was down left "Loading warnings…" on screen
    // indefinitely, with the server's explanation nowhere.
    const harness = setup({
      anomaliesFail: { status: 503, error: { error: { message: 'The gateway is restarting.' } } },
    });

    expect(harness.feedback().error()).toBe('The gateway is restarting.');
    expect(harness.element.querySelector('.loading')).toBeNull();
  });

  it('says the stops could not be checked, rather than that nothing is stopped', () => {
    // "You are stopped" is the fact a member most needs; a failed check that left the banner
    // away said the opposite without anybody having looked.
    const harness = setup({
      suspensions: [
        throwError(() => ({ status: 500, error: { error: { message: 'stops unreachable' } } })),
      ],
    });

    expect(harness.testid('stops-unknown')).not.toBeNull();
    expect(harness.feedback().error()).toBe('stops unreachable');
    // The findings beside it still arrived, and are shown.
    expect(harness.text()).toContain('spend spike');
  });
});

describe('WarningsTab — what changes while the tab is open', () => {
  afterEach(() => vi.useRealTimers());

  it('reads the stops again with every poll, so a lifted stop stops being shown', () => {
    vi.useFakeTimers();
    const harness = setup({
      suspensions: [of({ suspensions: [SUSPENSION] }), of({ suspensions: [] })],
    });
    expect(harness.testid('stopped-banner')).not.toBeNull();

    vi.advanceTimersByTime(20_000);
    harness.fixture.detectChanges();

    expect(harness.askedStops.length).toBe(2);
    expect(harness.testid('stopped-banner')).toBeNull();
  });

  it('stops showing a stop as in force once it has run out, without a reload', () => {
    vi.useFakeTimers();
    const soon = new Date(Date.now() + 5_000).toISOString();
    const harness = setup({
      suspensions: [of({ suspensions: [{ ...SUSPENSION, expires_at: soon }] })],
    });
    harness.click('warnings-live'); // live off: only the clock moves
    expect(harness.testid('stopped-banner')).not.toBeNull();

    vi.advanceTimersByTime(6_000);
    harness.fixture.detectChanges();

    expect(harness.testid('stopped-banner')).toBeNull();
  });

  it('keeps counting while live is off, instead of saying "0s ago" for good', () => {
    vi.useFakeTimers();
    const harness = setup();
    harness.click('warnings-live');

    vi.advanceTimersByTime(3 * 60 * 1000);
    harness.fixture.detectChanges();

    expect(harness.testid('warnings-stamp')?.textContent).toContain('3m ago');
  });
});

describe('WarningsTab — older findings', () => {
  it('offers the older findings when there are more, and appends them', () => {
    // The listing is all-time and paged by cursor; the tab showed the newest page and nothing
    // said there was more.
    const harness = setup({
      nextCursor: 'c1',
      older: [
        { ...EVENT, id: 'old', rule: 'an older finding', created_at: '2026-01-01T00:00:00Z' },
      ],
    });

    harness.click('warnings-load-older');

    expect(harness.asked.at(-1)?.cursor).toBe('c1');
    expect(harness.text()).toContain('spend spike');
    expect(harness.text()).toContain('an older finding');
    // Paused, or the next refresh would replace the first page and drop what was appended.
    expect((harness.testid('warnings-live') as HTMLInputElement).checked).toBe(false);
  });

  it('offers nothing more when there is nothing more', () => {
    expect(setup().testid('warnings-load-older')).toBeNull();
  });
});

describe('recentFindings', () => {
  const page = (events: AnomalyEvent[], next: string | null = null): AnomalyPage => ({
    events,
    next_cursor: next,
    scope: 'use_cases',
  });
  const now = new Date('2026-10-12T12:00:00Z');

  it('counts the last day, not all history', () => {
    // A finding from last month is history the tab lists; a red badge is a call to look now.
    const events = [
      { ...EVENT, id: 'a', created_at: '2026-10-12T11:00:00Z' },
      { ...EVENT, id: 'b', created_at: '2026-10-11T12:30:00Z' },
      { ...EVENT, id: 'c', created_at: '2026-09-01T00:00:00Z' },
    ];
    expect(recentFindings(page(events, 'more'), now)).toEqual({ count: 2, more: false });
  });

  it('says when a full page of recent findings may not be all of them', () => {
    const events = [{ ...EVENT, created_at: '2026-10-12T11:00:00Z' }];
    expect(recentFindings(page(events, 'more'), now)).toEqual({ count: 1, more: true });
    expect(recentFindings(page(events, null), now)).toEqual({ count: 1, more: false });
  });
});

describe('WarningsTab — reading the stopped banner', () => {
  function stopped(over: Partial<Suspension>) {
    return setup({ suspensions: [of({ suspensions: [{ ...SUSPENSION, ...over }] })] });
  }

  it('asks for the stops of this use case, which a member may read', () => {
    expect(setup().askedStops).toEqual(['uc-a']);
  });

  it('says whom a stop is on: the use case, a person, or one key', () => {
    // The server hands a member only what applies to them and an oversight reader everything in
    // the use case; "this use case is stopped" over a colleague's stop would be false for both.
    expect(stopped({}).testid('stopped-banner')?.textContent).toContain(
      'This use case is stopped.',
    );
    expect(
      stopped({ target: 'subject', target_value: 'ada' }).testid('stopped-banner')?.textContent,
    ).toContain('ada is stopped here.');
    expect(
      stopped({ target: 'credential', target_value: 'aira_7f2a' }).testid('stopped-banner')
        ?.textContent,
    ).toContain('API key aira_7f2a is stopped here.');
  });

  it('says what a throttle allows and when it ends', () => {
    // A member reading "you are stopped" needs the two numbers that tell them whether to wait or
    // to call somebody: how much traffic is left, and until when.
    const { testid } = stopped({ action: 'throttle', throttle_rpm: 5 });

    expect(testid('stopped-banner')?.textContent).toContain('Throttled to 5/min');
    expect(testid('stopped-banner')?.textContent).toContain('Until');
  });

  it('says "until it is lifted" when there is no expiry', () => {
    const { testid } = stopped({ expires_at: null, reason: '' });
    const banner = testid('stopped-banner')?.textContent ?? '';

    expect(banner).toContain('Blocked');
    expect(banner).toContain('Until it is lifted.');
  });

  it('reads the same when a stop names this use case as its target', () => {
    // A use case can be stopped either as a scope or as a target; a member must not have to know
    // which shape IT Security used.
    const { testid } = stopped({ use_case: null, target: 'use_case', target_value: 'uc-a' });
    expect(testid('stopped-banner')).not.toBeNull();
  });
});

describe('WarningsTab — reading a finding', () => {
  function withAction(action: string) {
    return setup({ events: [{ ...EVENT, action_taken: action }] }).text();
  }

  it('says what was done about each finding', () => {
    expect(withAction('alert')).toContain('recorded');
    expect(withAction('blocked')).toContain('blocked');
    expect(withAction('throttled')).toContain('throttled');
    expect(withAction('detected_not_enforced')).toContain('not enforced');
  });
});
