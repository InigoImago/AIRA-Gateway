import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Observable, of, throwError } from 'rxjs';
import { AnomalyEvent, Suspension } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { PageFeedback } from '../../core/ui/page-feedback';
import { WarningsTab } from './warnings-tab';

const EVENT: AnomalyEvent = {
  id: 'e1',
  created_at: '2026-08-08T10:00:00Z',
  rule: 'spend spike',
  kind: 'spend_spike',
  use_case: 'uc-a',
  target: 'use_case',
  target_value: 'uc-a',
  observed: 4,
  threshold: 3,
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
  readonly count = signal(-1);
}

interface Options {
  events?: AnomalyEvent[];
  suspensions?: Observable<{ suspensions: Suspension[] }>;
  inScope?: boolean;
}

function setup(options: Options = {}) {
  TestBed.resetTestingModule();
  const asked: (string | undefined)[] = [];
  TestBed.configureTestingModule({
    imports: [Host],
    providers: [
      {
        provide: UseCaseService,
        useValue: {
          anomalies: (limit: number, useCase?: string) => {
            asked.push(useCase);
            return of({
              events: options.events ?? [EVENT],
              scope: 'use_cases',
              ...(options.inScope === undefined ? {} : { in_scope: options.inScope }),
            });
          },
          suspensions: () => options.suspensions ?? of({ suspensions: [SUSPENSION] }),
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
    host: fixture.componentInstance,
    text: () => element.textContent ?? '',
    testid: (id: string) => element.querySelector(`[data-testid="${id}"]`),
  };
}

describe('WarningsTab', () => {
  it('shows the findings about this use case, with the numbers behind them', () => {
    // A warning only IT Security can see is a warning nobody who could fix the cause ever reads.
    const { text } = setup();

    expect(text()).toContain('spend spike');
    expect(text()).toContain('4');
    expect(text()).toContain('3');
  });

  it('asks the server for this use case rather than filtering a global page', () => {
    // Filtering in the browser would show a quiet use case nothing on a busy installation: its
    // findings would have been pushed off the end of the newest hundred by somebody else's.
    const { asked } = setup();
    expect(asked).toContain('uc-a');
  });

  it('does not print "nothing found" when the truth is "you cannot see this"', () => {
    const { testid, text } = setup({ events: [], inScope: false });

    expect(testid('no-warnings')).toBeNull();
    expect(testid('not-in-scope')?.textContent).toContain('identity provider');
    expect(text()).toContain('/use-cases/uc-a');
  });

  it('tells the parent how many there are, so the tab badge is not a second source of truth', () => {
    const { host } = setup({ events: [EVENT, { ...EVENT, id: 'e2' }] });
    expect(host.count()).toBe(2);
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
    const { testid } = setup({ suspensions: of({ suspensions: [lifted, expired] }) });

    expect(testid('stopped-banner')).toBeNull();
  });

  it('stays a working page when suspensions are refused', () => {
    // Listing suspensions needs an incident role; a member gets a 403. That is a real answer about
    // a real permission, and must not turn the whole tab into an error.
    const { text, testid } = setup({ suspensions: throwError(() => ({ status: 403 })) });

    expect(testid('stopped-banner')).toBeNull();
    expect(text()).toContain('spend spike');
  });

  it('says "nothing found" rather than showing an empty table', () => {
    const { testid, host } = setup({ events: [] });

    expect(testid('no-warnings')).not.toBeNull();
    expect(host.count()).toBe(0);
  });
});

describe('WarningsTab — reading the stopped banner', () => {
  function stopped(over: Partial<Suspension>) {
    return setup({ suspensions: of({ suspensions: [{ ...SUSPENSION, ...over }] }) });
  }

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
