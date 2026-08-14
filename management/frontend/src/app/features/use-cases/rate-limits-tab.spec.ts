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
  rlScope: { set: (v: 'use_case' | 'each_member') => void; (): string };
  rlSubject: { set: (v: string) => void; (): string };
  rlRpm: { set: (v: number | null) => void; (): number | null };
  rlBurst: { set: (v: number | null) => void; (): number | null };
  validationError: () => string | null;
  canAdd: () => boolean;
  add: () => void;
  remove: (id: number | undefined) => void;
  labelFor: (l: RateLimit) => string;
  unmatchedSubject: () => boolean;
  reachesNobodyKnown: (scope: string, subject?: string | null) => boolean;
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
    // Not "Whole use case": a per-person row bounds each caller separately, and reading it as a
    // shared allowance is the one wrong conclusion the table could lead somebody to.
    expect(component.labelFor({ scope: 'each_member', limit_rpm: 60 })).toBe(
      'Each member, individually',
    );
  });

  it('renders the configured limits', () => {
    const harness = setup([{ id: 1, scope: 'each_member', limit_rpm: 90, burst: 9 }]);
    const text = harness.text();
    // Named by its scope now that no scope names a person — and the wording matters: "Each
    // member, individually" rather than "Whole use case", because the row bounds each of them
    // separately and would otherwise read as a limit forty people share.
    expect(text).toContain('Each member, individually');
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

describe('RateLimitsTab — a rate per person, a window, and what Burst means', () => {
  it('offers a rate that applies to everybody separately', () => {
    const harness = setup();
    harness.component.showForm.set(true);
    harness.fixture.detectChanges();

    const scope = (harness.fixture.nativeElement as HTMLElement).querySelector<HTMLSelectElement>(
      '#rl-scope',
    )!;
    expect([...scope.options].map((o) => o.value)).toEqual(['use_case', 'each_member']);

    harness.component.rlScope.set('each_member');
    harness.fixture.detectChanges();
    expect(harness.fixture.nativeElement.querySelector('#rl-subject')).toBeNull();
  });

  it('explains what a burst is, in terms of the bucket it actually is', () => {
    /** Reported: "es ist notwendig die erklärung für Burst haben, sogar mir ist nicht ganz klar
     *  was damit gemeint ist." A number whose meaning the person setting it cannot state is a
     *  control that gets a plausible value and no thought — and this one changes whether an agent
     *  opening 30 connections at once is smoothed out or waved through. */
    const harness = setup();
    harness.component.showForm.set(true);
    harness.fixture.detectChanges();

    // Opened first: a hint that renders nothing until it is asked for is still a hint, and the
    // `FRD-505` failure was hints that rendered nothing **when** asked. So this asks.
    const html = harness.fixture.nativeElement as HTMLElement;
    html.querySelector<HTMLButtonElement>('[data-testid="info-rl-burst"]')!.click();
    harness.fixture.detectChanges();

    const help = html.querySelector('[data-testid="help-rl-burst"]');
    expect(help).not.toBeNull();
    const text = help!.textContent ?? '';
    // The mechanism, not a restatement of the word.
    expect(text).toContain('bucket');
    expect(text).toContain('refills');
    // And what setting it either way does, which is the part somebody is deciding.
    expect(text).toContain('does not raise');
  });

  it('creates in a window', () => {
    const harness = setup();
    const html = harness.fixture.nativeElement as HTMLElement;

    html.querySelector<HTMLButtonElement>('[data-testid="add-rate-limit"]')!.click();
    harness.fixture.detectChanges();

    expect(html.querySelector('[data-testid="rate-limit-editor"]')).not.toBeNull();
  });
});

/**
 * A rule that names nobody.
 *
 * The field accepts anything on purpose — access can come through a Keycloak group, and somebody
 * granted that way belongs to no membership row (`FRD-209`), so a picker would be narrower than the
 * rule it fills in. The cost is that a typo produces a rule which binds **nobody**, saves cleanly,
 * and sits in the list looking exactly like a working one: this project's most repeated defect,
 * a control that is configured, displayed as active, and applies to nothing.
 *
 * Neither refused nor accepted silently. The console says what it *knows*, and is careful to say
 * knows — who is in a group is the identity provider's answer, which is why this cannot be an
 * error and must not be silence either.
 */

/**
 * The fact a per-head figure means something different depending on how people reach the gateway.
 *
 * Measured on the live stack (a limit of one per head): a person's API keys share **one** allowance
 * — a key's subject is its owner's name, so every key they own counts to the same place — while the
 * same person signed in through Keycloak gets a *separate* one. The two credentials answer "who is
 * this" in different alphabets, and nothing reconciles them since the scope that named a person by
 * hand was removed.
 *
 * A warning on the screen rather than a note in a document, because the number somebody types is
 * wrong by a factor of two for anybody running an agent with a key while also working in a browser
 * — and they have no way to know that from the form.
 */
describe('RateLimitsTab — the per-head warning', () => {
  /**
   * Reported: *"where do I find these notes? there is nothing in budgets or rate limits"* — and
   * that was true. The warning lived in the creation window, so anybody **reading** the
   * configuration never met it. A warning nobody meets is a warning that was not given.
   */
  it('shows the warning on the tab wherever such a row already exists', () => {
    const empty = setup();
    expect(empty.fixture.nativeElement.querySelector('[data-testid="rl-two-pots"]')).toBeNull();

    const configured = setup([{ id: 1, scope: 'each_member', limit_rpm: 60, burst: 5 }]);

    expect(
      configured.fixture.nativeElement.querySelector('[data-testid="rl-two-pots"]'),
    ).not.toBeNull();
  });

  /**
   * The qualifier, and it matters as much as the warning: a use-case-wide budget or limit binds
   * **both** credentials — measured with a cap of four requests exhausted, after which the key and
   * the bearer token were each refused. A reader told only the first half concludes the governance
   * is broken by a factor of two.
   */
  it('says that a use-case-wide figure still bounds both allowances', () => {
    const harness = setup([{ id: 1, scope: 'each_member', limit_rpm: 60, burst: 5 }]);

    const warning = harness.fixture.nativeElement.querySelector('[data-testid="rl-two-pots"]');

    expect(warning?.textContent).toContain('whole use case');
  });

  it('warns that a person has two allowances, and only for the per-head scope', () => {
    const harness = setup();
    harness.component.showForm.set(true);
    harness.component.rlScope.set('use_case');
    harness.fixture.detectChanges();
    expect(harness.fixture.nativeElement.querySelector('[data-testid="rl-two-pots"]')).toBeNull();

    harness.component.rlScope.set('each_member');
    harness.fixture.detectChanges();

    const warning = harness.fixture.nativeElement.querySelector('[data-testid="rl-two-pots"]');
    expect(warning).not.toBeNull();
    expect(warning?.textContent).toContain('Two allowances per person');
    expect(warning?.textContent).toContain('Keycloak');
  });
});
