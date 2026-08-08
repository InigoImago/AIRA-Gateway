import { TestBed } from '@angular/core/testing';
import { Observable, of, throwError } from 'rxjs';
import { AnomalyEvent, AnomalyRule, Me, Suspension } from '../../core/api/models';
import { MeService } from '../../core/api/me.service';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { SecurityPage } from './security-page';

const EVENT: AnomalyEvent = {
  id: 'e1',
  created_at: '2026-08-08T10:00:00Z',
  rule: 'too many refusals',
  kind: 'refusal_rate',
  use_case: 'kundenservice',
  target: 'subject',
  target_value: 'ada',
  observed: 90,
  threshold: 50,
  sample: 20,
  window_minutes: 15,
  action_taken: 'alert',
  detail: '90% refusals over 20 requests in 15 min',
};

const SUSPENSION: Suspension = {
  id: 's1',
  created_at: '2026-08-08T10:00:00Z',
  use_case: 'kundenservice',
  target: 'subject',
  target_value: 'ada',
  action: 'block',
  throttle_rpm: null,
  expires_at: '2099-01-01T00:00:00Z',
  author: 'user:itsec',
  reason: 'under investigation',
  lifted_at: null,
  lifted_by: null,
};

const RULE: AnomalyRule = {
  id: 1,
  use_case: null,
  is_global: true,
  name: 'new address',
  kind: 'new_source_ip',
  window_minutes: 60,
  threshold: 1,
  parameter: null,
  min_sample: 0,
  action: 'alert',
  target: 'credential',
  action_minutes: null,
  throttle_rpm: null,
  enabled: true,
};

interface Options {
  roles?: string[];
  events?: AnomalyEvent[];
  suspensions?: Observable<{ suspensions: Suspension[] }>;
  rules?: AnomalyRule[];
  suspend?: Observable<Suspension>;
  lift?: Observable<Suspension>;
  confirmAnswer?: boolean;
}

function setup(options: Options = {}) {
  TestBed.resetTestingModule();
  const calls: string[] = [];
  const service = {
    anomalies: () => of({ events: options.events ?? [EVENT], scope: 'all' }),
    suspensions: () => options.suspensions ?? of({ suspensions: [SUSPENSION] }),
    globalRules: () => of(options.rules ?? [RULE]),
    suspend: (body: Record<string, unknown>) => {
      calls.push(`suspend:${body['target']}:${body['target_value']}`);
      return options.suspend ?? of(SUSPENSION);
    },
    liftSuspension: (id: string) => {
      calls.push(`lift:${id}`);
      return options.lift ?? of({ ...SUSPENSION, lifted_at: 'now', lifted_by: 'user:itsec' });
    },
  };
  TestBed.configureTestingModule({
    imports: [SecurityPage],
    providers: [
      { provide: UseCaseService, useValue: service },
      {
        provide: MeService,
        useValue: {
          get: () =>
            of({
              username: 'itsec',
              roles: options.roles ?? ['it-security'],
              use_cases: [],
            } as unknown as Me),
        },
      },
      { provide: ConfirmService, useValue: { ask: () => options.confirmAnswer ?? true } },
    ],
  });
  const fixture = TestBed.createComponent(SecurityPage);
  fixture.detectChanges();
  const element = fixture.nativeElement as HTMLElement;
  return {
    fixture,
    calls,
    element,
    component: fixture.componentInstance as unknown as Record<string, never>,
    text: () => element.textContent ?? '',
    testid: (id: string) => element.querySelector(`[data-testid="${id}"]`),
    click: (selector: string) => {
      element.querySelector<HTMLElement>(selector)?.click();
      fixture.detectChanges();
    },
  };
}

describe('SecurityPage — findings', () => {
  it('shows a finding with the numbers it was drawn from', () => {
    // A finding nobody can check is a finding nobody acts on, and the first question is "how bad,
    // out of how many".
    const { text } = setup();

    expect(text()).toContain('too many refusals');
    expect(text()).toContain('refusal_rate');
    expect(text()).toContain('90');
    expect(text()).toContain('50');
    expect(text()).toContain('20 request(s)');
  });

  it('says what was actually done, not what the rule asked for', () => {
    // `ADR-0014` §3: recording and enforcing are two facts, and the row says which happened.
    expect(setup({ events: [{ ...EVENT, action_taken: 'alert' }] }).text()).toContain('recorded');
    expect(setup({ events: [{ ...EVENT, action_taken: 'blocked' }] }).text()).toContain('blocked');
    expect(setup({ events: [{ ...EVENT, action_taken: 'throttled' }] }).text()).toContain(
      'throttled',
    );
    expect(
      setup({ events: [{ ...EVENT, action_taken: 'detected_not_enforced' }] }).text(),
    ).toContain('not enforced');
  });

  it('does not let an empty list read as "nothing is wrong"', () => {
    // A quiet detector and an absent one look identical from here, so the screen says which.
    const { text } = setup({ events: [] });

    expect(text()).toContain('Nothing has crossed a threshold');
    expect(text()).toContain('Rules');
  });
});

describe('SecurityPage — suspensions and the kill switch', () => {
  it('warns at the top when anything is stopped right now', () => {
    const { testid } = setup();
    expect(testid('active-banner')?.textContent).toContain('1');
  });

  it('offers the kill switch to an incident role', () => {
    const { component, fixture, testid } = setup({ roles: ['it-security'] });
    (component as unknown as { tab: { set: (v: string) => void } }).tab.set('suspensions');
    fixture.detectChanges();

    expect(testid('stop-toggle')).not.toBeNull();
    expect(testid('stop-readonly')).toBeNull();
  });

  it('withholds it from a read-only governance role, and says who does it', () => {
    // `it-steuerung` sees every use case and every figure and writes nothing anywhere (PRD §154).
    // Offering a button that answers 403 is the defect `FRD-206` was written about.
    const { component, fixture, testid } = setup({ roles: ['it-steuerung'] });
    (component as unknown as { tab: { set: (v: string) => void } }).tab.set('suspensions');
    fixture.detectChanges();

    expect(testid('stop-toggle')).toBeNull();
    expect(testid('stop-readonly')?.textContent).toContain('IT Security');
  });

  it('stops traffic through the server, with a reason', () => {
    const harness = setup();
    const component = harness.component as unknown as {
      tab: { set: (v: string) => void };
      showStop: { set: (v: boolean) => void };
      targetValue: { set: (v: string) => void };
      reason: { set: (v: string) => void };
      stop: () => void;
    };
    component.tab.set('suspensions');
    component.showStop.set(true);
    component.targetValue.set('ada');
    component.reason.set('probing');
    harness.fixture.detectChanges();

    component.stop();

    expect(harness.calls).toContain('suspend:subject:ada');
  });

  it('says the decision takes a moment to reach every instance', () => {
    // The cache is deliberately a few seconds behind (`FRD-503` §4.1). A console that implied
    // "done" would have somebody testing it immediately and concluding it did not work.
    const harness = setup();
    const component = harness.component as unknown as {
      targetValue: { set: (v: string) => void };
      stop: () => void;
      feedback: { notice: () => string | null };
    };
    component.targetValue.set('ada');
    component.stop();

    expect(component.feedback.notice()).toContain('few seconds');
  });

  it('asks before restoring access, and does nothing when declined', () => {
    const declined = setup({ confirmAnswer: false });
    (declined.component as unknown as { lift: (row: Suspension) => void }).lift(SUSPENSION);
    expect(declined.calls).toEqual([]);

    const accepted = setup({ confirmAnswer: true });
    (accepted.component as unknown as { lift: (row: Suspension) => void }).lift(SUSPENSION);
    expect(accepted.calls).toContain('lift:s1');
  });

  it('keeps lifted and expired ones out of the active list but on the page', () => {
    // "Blocked for two hours last Tuesday" is exactly what a review asks.
    const lifted = { ...SUSPENSION, id: 's2', lifted_at: '2026-08-08T11:00:00Z' };
    const expired = { ...SUSPENSION, id: 's3', expires_at: '2020-01-01T00:00:00Z' };
    const harness = setup({ suspensions: of({ suspensions: [SUSPENSION, lifted, expired] }) });
    const component = harness.component as unknown as {
      active: () => Suspension[];
      past: () => Suspension[];
    };

    expect(component.active().map((r) => r.id)).toEqual(['s1']);
    expect(component.past().map((r) => r.id)).toEqual(['s2', 's3']);
  });

  it('does not report a refused suspension list as a page failure', () => {
    // A caller who may see findings and not suspensions gets a 403. That is a real answer about a
    // real permission, not a broken screen.
    const harness = setup({
      roles: ['it-steuerung'],
      suspensions: throwError(() => ({ status: 403 })),
    });
    const component = harness.component as unknown as {
      feedback: { error: () => string | null };
    };

    expect(component.feedback.error()).toBeNull();
  });
});

describe('SecurityPage — rules', () => {
  it('shows what is being watched, so an empty findings list can be read', () => {
    const harness = setup();
    (harness.component as unknown as { tab: { set: (v: string) => void } }).tab.set('rules');
    harness.fixture.detectChanges();

    expect(harness.text()).toContain('new address');
    expect(harness.text()).toContain('everywhere');
  });

  it('says when nothing is being watched at all', () => {
    const harness = setup({ rules: [] });
    (harness.component as unknown as { tab: { set: (v: string) => void } }).tab.set('rules');
    harness.fixture.detectChanges();

    expect(harness.text()).toContain('Nothing is being watched');
  });
});

describe('SecurityPage — live', () => {
  it('shows that it is live, and lets the reader switch it off', () => {
    // A screen that changes under somebody who did not ask it to is a screen they stop trusting.
    const { testid, element, fixture } = setup();
    const toggle = testid('live-toggle') as HTMLInputElement;

    expect(toggle.checked).toBe(true);
    expect(testid('live-stamp')?.textContent).toContain('ago');

    toggle.click();
    fixture.detectChanges();
    expect((element.querySelector('[data-testid="live-toggle"]') as HTMLInputElement).checked).toBe(
      false,
    );
  });
});

describe('SecurityPage — reading a suspension row', () => {
  function onSuspensions(rows: Suspension[]) {
    const harness = setup({ suspensions: of({ suspensions: rows }) });
    (harness.component as unknown as { tab: { set: (v: string) => void } }).tab.set('suspensions');
    harness.fixture.detectChanges();
    return harness;
  }

  it('says what a throttle actually allows, not just that one exists', () => {
    // "An enum member is not a specification" (`FRD-503` §7): a throttle without its rate is a
    // decision nobody can review.
    const { text } = onSuspensions([{ ...SUSPENSION, action: 'throttle', throttle_rpm: 12 }]);

    expect(text()).toContain('12/min');
  });

  it('distinguishes "until lifted" from an expiry', () => {
    expect(onSuspensions([{ ...SUSPENSION, expires_at: null }]).text()).toContain('until lifted');
  });

  it('says when a stop applies everywhere rather than to one use case', () => {
    // A global stop and a use-case-scoped one look identical without this, and they are very
    // different decisions to review.
    expect(onSuspensions([{ ...SUSPENSION, use_case: null }]).text()).toContain('everywhere');
  });

  it('shows an empty state instead of an empty table', () => {
    expect(onSuspensions([]).text()).toContain('Nothing is stopped.');
  });

  it('keeps the past ones readable — who stopped it, why, and how it ended', () => {
    const { text } = onSuspensions([
      { ...SUSPENSION, id: 's2', lifted_at: '2026-08-08T11:00:00Z', lifted_by: 'user:boss' },
      { ...SUSPENSION, id: 's3', expires_at: '2020-01-01T00:00:00Z' },
    ]);

    expect(text()).toContain('lifted by user:boss');
    expect(text()).toContain('expired');
  });

  it('will not submit a stop with no target', () => {
    // The server would refuse it, but a form that posts an empty target teaches the reader that
    // the console is unreliable rather than that the input was.
    const harness = onSuspensions([SUSPENSION]);
    const component = harness.component as unknown as {
      showStop: { set: (v: boolean) => void };
      targetValue: { set: (v: string) => void };
      stop: () => void;
    };
    component.showStop.set(true);
    component.targetValue.set('   ');
    component.stop();

    expect(harness.calls).toEqual([]);
  });

  it('clears the form once the stop is in force', () => {
    const harness = setup();
    const component = harness.component as unknown as {
      targetValue: { set: (v: string) => void; (): string };
      reason: { set: (v: string) => void; (): string };
      showStop: { set: (v: boolean) => void; (): boolean };
      stop: () => void;
    };
    component.showStop.set(true);
    component.targetValue.set('ada');
    component.reason.set('probing');
    component.stop();

    // A form that keeps its text after a successful submit is how the same decision gets made
    // twice — the zoneless re-render bug this project already fixed once.
    expect(component.targetValue()).toBe('');
    expect(component.reason()).toBe('');
    expect(component.showStop()).toBe(false);
  });
});

describe('SecurityPage — reading a rule', () => {
  function onRules(rules: AnomalyRule[]) {
    const harness = setup({ rules });
    (harness.component as unknown as { tab: { set: (v: string) => void } }).tab.set('rules');
    harness.fixture.detectChanges();
    return harness;
  }

  it('names the use case a scoped rule belongs to', () => {
    expect(onRules([{ ...RULE, is_global: false, use_case: 'uc-a' }]).text()).toContain('uc-a');
  });

  it('says when a rule is switched off, so it is not read as watching', () => {
    expect(onRules([{ ...RULE, enabled: false }]).text()).toContain('off');
  });

  it('shows the second number a two-number rule needs', () => {
    // `payload_size` needs a byte figure as well as a count; the kind that had nowhere to put it
    // was the defect stage A shipped (`FRD-501` §7).
    const { text } = onRules([
      { ...RULE, kind: 'payload_size', threshold: 10, parameter: 1_000_000 },
    ]);

    expect(text()).toContain('1000000 bytes');
  });

  it('reports a refused rule list, because that one is not a permission boundary', () => {
    // Unlike suspensions, every reader of this page may list rules. A failure here is a failure.
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [SecurityPage],
      providers: [
        {
          provide: UseCaseService,
          useValue: {
            anomalies: () => of({ events: [], scope: 'all' }),
            suspensions: () => of({ suspensions: [] }),
            globalRules: () => throwError(() => ({ status: 500 })),
          },
        },
        { provide: MeService, useValue: { get: () => of({ roles: [] } as unknown as Me) } },
        { provide: ConfirmService, useValue: { ask: () => true } },
      ],
    });
    const fixture = TestBed.createComponent(SecurityPage);
    fixture.detectChanges();

    expect((fixture.nativeElement as HTMLElement).textContent).toContain(
      'Could not load the anomaly rules.',
    );
  });
});
