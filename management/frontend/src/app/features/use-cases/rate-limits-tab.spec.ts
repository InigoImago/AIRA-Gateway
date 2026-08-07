import { TestBed } from '@angular/core/testing';
import { Observable, of, throwError } from 'rxjs';
import { RateLimit } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { PageFeedback } from '../../core/ui/page-feedback';
import { RateLimitsTab } from './rate-limits-tab';

interface Overrides {
  createRateLimit?: Observable<RateLimit>;
  deleteRateLimit?: Observable<void>;
}

interface Tab {
  showForm: { set: (v: boolean) => void; (): boolean };
  rlScope: { set: (v: 'use_case' | 'member') => void; (): string };
  rlSubject: { set: (v: string) => void; (): string };
  rlRpm: { set: (v: number | null) => void; (): number | null };
  rlBurst: { set: (v: number | null) => void; (): number | null };
  validationError: () => string | null;
  canAdd: () => boolean;
  add: () => void;
  remove: (id: number | undefined) => void;
  labelFor: (l: RateLimit) => string;
  effectiveBurst: (l: RateLimit) => number;
  feedback: PageFeedback;
}

const httpError = (status: number) =>
  throwError(() => ({ status, error: { error: { message: 'refused' } } }));

function setup(
  limits: RateLimit[] = [],
  overrides: Overrides = {},
  confirmAnswer = true,
  canManage = true,
) {
  TestBed.resetTestingModule();
  const calls: string[] = [];
  const service = {
    createRateLimit: (_s: string, limit: RateLimit) => {
      calls.push(`create:${limit.scope}:${limit.subject}:${limit.limit_rpm}:${limit.burst}`);
      return overrides.createRateLimit ?? of(limit);
    },
    deleteRateLimit: (_s: string, id: number) => {
      calls.push(`delete:${id}`);
      return overrides.deleteRateLimit ?? of(undefined as unknown as void);
    },
  };

  TestBed.configureTestingModule({
    imports: [RateLimitsTab],
    providers: [
      PageFeedback,
      { provide: UseCaseService, useValue: service },
      { provide: ConfirmService, useValue: { ask: () => confirmAnswer } },
    ],
  });

  const fixture = TestBed.createComponent(RateLimitsTab);
  fixture.componentRef.setInput('slug', 'demo-uc');
  fixture.componentRef.setInput('limits', limits);
  fixture.componentRef.setInput('canManage', canManage);
  const changes: number[] = [];
  fixture.componentInstance.changed.subscribe(() => changes.push(1));
  fixture.detectChanges();

  return {
    fixture,
    calls,
    changes,
    component: fixture.componentInstance as unknown as Tab,
    text: () => (fixture.nativeElement as HTMLElement).textContent ?? '',
  };
}

describe('RateLimitsTab', () => {
  it('requires a username for a member limit', () => {
    const { component } = setup();
    component.rlScope.set('member');
    component.rlRpm.set(60);
    component.rlSubject.set('  ');
    expect(component.validationError()).toBe('A member limit needs a username.');
    expect(component.canAdd()).toBe(false);
  });

  it('requires a rate to be given at all', () => {
    expect(setup().component.validationError()).toBe(
      'Set how many requests per minute are allowed.',
    );
  });

  it('refuses a rate of zero rather than silently switching the use case off', () => {
    const { component } = setup();
    component.rlRpm.set(0);
    expect(component.validationError()).toBe('At least 1 request per minute.');
  });

  it('refuses a burst below one', () => {
    const { component } = setup();
    component.rlRpm.set(60);
    component.rlBurst.set(0);
    expect(component.validationError()).toBe('A burst must be at least 1, or left empty.');
  });

  it('saves a limit, clears the form and asks the page to reload', () => {
    const { component, calls, changes } = setup();
    component.rlRpm.set(120);
    component.rlBurst.set(20);
    component.add();

    expect(calls).toContain('create:use_case::120:20');
    expect(component.feedback.notice()).toBe('Rate limit saved.');
    expect(component.rlRpm()).toBeNull();
    expect(component.showForm()).toBe(false);
    expect(changes.length).toBe(1);
  });

  it('does not submit an invalid limit', () => {
    const { component, calls } = setup();
    component.add();
    expect(calls).toEqual([]);
  });

  it('surfaces a refused save rather than appearing to have worked', () => {
    const { component, changes } = setup([], { createRateLimit: httpError(403) });
    component.rlRpm.set(60);
    component.add();

    expect(component.feedback.error()).toBe('refused');
    expect(component.feedback.notice()).toBeNull();
    expect(changes.length).toBe(0); // nothing changed, so nothing to reload
  });

  it('asks before removing and does nothing when declined', () => {
    const declined = setup([], {}, false);
    declined.component.remove(3);
    expect(declined.calls).toEqual([]);

    const accepted = setup([], {}, true);
    accepted.component.remove(3);
    expect(accepted.calls).toContain('delete:3');
    expect(accepted.component.feedback.notice()).toBe('Rate limit removed.');
    expect(accepted.changes.length).toBe(1);
  });

  it('ignores a remove with no id', () => {
    const { component, calls } = setup();
    component.remove(undefined);
    expect(calls).toEqual([]);
  });

  it('treats an unset burst as the per-minute figure', () => {
    const { component } = setup();
    expect(component.effectiveBurst({ scope: 'use_case', limit_rpm: 60 })).toBe(60);
    expect(component.effectiveBurst({ scope: 'use_case', limit_rpm: 60, burst: 5 })).toBe(5);
  });

  it('names who a limit applies to', () => {
    const { component } = setup();
    expect(component.labelFor({ scope: 'use_case', limit_rpm: 60 })).toBe('Whole use case');
    expect(component.labelFor({ scope: 'member', subject: 'alice', limit_rpm: 60 })).toBe('alice');
    expect(component.labelFor({ scope: 'member', limit_rpm: 60 })).toBe('member');
  });

  it('renders the configured limits', () => {
    const harness = setup([{ id: 1, scope: 'member', subject: 'alice', limit_rpm: 90, burst: 9 }]);
    const text = harness.text();
    expect(text).toContain('alice');
    expect(text).toContain('90');
    expect(text).toContain('9');
  });

  it('says plainly that no limit means no throttling', () => {
    expect(setup().text()).toContain('may send as fast as it likes');
  });

  it('marks a disabled limit as such instead of showing it as active', () => {
    const harness = setup([{ id: 2, scope: 'use_case', limit_rpm: 30, enabled: false }]);
    expect(harness.text()).toContain('Disabled');
    expect(harness.text()).not.toContain('Active');
  });

  it('reveals the member field and the validation hint when the form is opened', () => {
    const harness = setup();
    harness.component.showForm.set(true);
    harness.component.rlScope.set('member');
    harness.fixture.detectChanges();

    const html = harness.fixture.nativeElement as HTMLElement;
    expect(html.querySelector('#rl-subject')).toBeTruthy();
    expect(html.querySelector('#rl-rpm')).toBeTruthy();
    // The form must say why it will not submit rather than just disabling the button.
    expect(harness.text()).toContain('A member limit needs a username.');
  });
});

describe('RateLimitsTab — a reader', () => {
  it('sees the limits and none of the controls', () => {
    const { fixture, text } = setup(
      [{ id: 1, scope: 'use_case', subject: '', limit_rpm: 60 }],
      {},
      true,
      false,
    );
    const html = fixture.nativeElement as HTMLElement;

    expect(text()).toContain('60');
    expect(text()).not.toContain('+ Add rate limit');
    expect(html.querySelector('[aria-label^="Remove the rate limit"]')).toBeNull();
    expect(html.querySelector('[data-testid="rate-limits-readonly"]')).not.toBeNull();
  });
});
