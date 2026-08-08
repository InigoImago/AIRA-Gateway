import { TestBed } from '@angular/core/testing';
import { Observable, Subject, of, throwError } from 'rxjs';
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
  update?: Observable<AnomalyRule>;
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
    updateRule: (id: number, changes: Record<string, unknown>) => {
      calls.push(`update:${id}:${JSON.stringify(changes)}`);
      return options.update ?? of(RULE);
    },
    deleteRule: (id: number) => {
      calls.push(`delete-rule:${id}`);
      return of(undefined);
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

describe('SecurityPage — a finding opens', () => {
  it('shows what was measured, and what was done, in words', () => {
    // A finding nobody can check is a finding nobody acts on. Six columns is as much as a table
    // can be read at, so the rest goes under the row rather than into it.
    const harness = setup();
    expect(harness.testid('event-detail-e1')).toBeNull();

    harness.click('[data-testid="event-toggle-e1"]');

    const detail = harness.testid('event-detail-e1')?.textContent ?? '';
    expect(detail).toContain('15 minutes');
    expect(detail).toContain('kundenservice');
    expect(detail).toContain('20 request(s)');
    // And what the system actually did about it — `ADR-0014` keeps that apart from what it found.
    expect(harness.text()).toContain('nothing was taken away');
  });

  it('closes again, so a long table does not fill with open rows', () => {
    const harness = setup();
    harness.click('[data-testid="event-toggle-e1"]');
    harness.click('[data-testid="event-toggle-e1"]');

    expect(harness.testid('event-detail-e1')).toBeNull();
  });

  it('names the rule behind the finding when the reader can see it too', () => {
    const harness = setup({
      events: [{ ...EVENT, rule: 'new address', kind: 'new_source_ip' }],
      rules: [RULE],
    });
    harness.click('[data-testid="event-toggle-e1"]');

    expect(harness.text()).toContain('The rule behind it');
  });
});

describe('SecurityPage — a rule opens and can be changed', () => {
  function onRules(options: Options = {}) {
    const harness = setup(options);
    (harness.component as unknown as { tab: { set: (v: string) => void } }).tab.set('rules');
    harness.fixture.detectChanges();
    return harness;
  }

  it('says what the rule does rather than printing its kind', () => {
    // `new_source_ip` and two bare numbers is enough for whoever wrote the rule and nothing for
    // whoever has to decide, at eleven at night, whether the alert in front of them matters.
    const harness = onRules();
    harness.click('[data-testid="rule-toggle-1"]');

    const detail = harness.testid('rule-detail-1')?.textContent ?? '';
    expect(detail).toContain('address that has not been seen before');
    expect(detail).toContain('per API key');
    expect(detail).toContain('60 minutes');
  });

  it('offers Edit to an incident role and explains the absence to everyone else', () => {
    const allowed = onRules({ roles: ['it-security'] });
    allowed.click('[data-testid="rule-toggle-1"]');
    expect(allowed.testid('rule-edit-1')).not.toBeNull();

    const readOnly = onRules({ roles: ['it-steuerung'] });
    readOnly.click('[data-testid="rule-toggle-1"]');
    expect(readOnly.testid('rule-edit-1')).toBeNull();
    expect(readOnly.testid('rule-readonly-1')?.textContent).toContain('IT Security');
  });

  it('points a use-case rule at the use case rather than guessing the permission', () => {
    // Object-level permission is not in the token, so the console cannot answer "may I edit this"
    // for a use-case rule. Saying where it is edited beats offering a button that answers 403.
    const harness = onRules({ rules: [{ ...RULE, is_global: false, use_case: 'uc-a' }] });
    harness.click('[data-testid="rule-toggle-1"]');

    expect(harness.testid('rule-edit-1')).toBeNull();
    expect(harness.testid('rule-readonly-1')?.textContent).toContain('uc-a');
  });

  it('saves the fields somebody actually changes, and never the kind', () => {
    // A rule's kind decides what its threshold *means*. Changing it in place would silently
    // reinterpret a number somebody chose deliberately — a different kind is a different rule.
    const harness = onRules();
    harness.click('[data-testid="rule-toggle-1"]');
    harness.click('[data-testid="rule-edit-1"]');

    const component = harness.component as unknown as {
      editThreshold: { set: (v: number) => void };
      editAction: { set: (v: string) => void };
      saveRule: (rule: AnomalyRule) => void;
    };
    component.editThreshold.set(5);
    component.editAction.set('block');
    component.saveRule(RULE);

    const sent = harness.calls.find((call) => call.startsWith('update:1:'));
    expect(sent).toBeDefined();
    const body = JSON.parse(sent!.slice('update:1:'.length));
    expect(body.threshold).toBe(5);
    expect(body.action).toBe('block');
    // Sent unchanged, because the server validates the threshold against the kind: a PATCH that
    // omitted it would be validated against the default rather than against this rule.
    expect(body.kind).toBe(RULE.kind);
  });

  it('asks before deleting a rule, and says what stops being watched', () => {
    const declined = onRules({ confirmAnswer: false });
    (declined.component as unknown as { removeRule: (r: AnomalyRule) => void }).removeRule(RULE);
    expect(declined.calls).toEqual([]);

    const accepted = onRules({ confirmAnswer: true });
    (accepted.component as unknown as { removeRule: (r: AnomalyRule) => void }).removeRule(RULE);
    expect(accepted.calls).toContain('delete-rule:1');
  });
});

describe('SecurityPage — saying what a control is', () => {
  it('explains how far the kill switch reaches, and how far it does not', () => {
    // "Stop traffic" is a verb with no object until you press it, and a reader has to know the
    // object before they can decide whether to use it. There is deliberately no switch for the
    // installation, and the explanation says so.
    const harness = setup();
    (harness.component as unknown as { tab: { set: (v: string) => void } }).tab.set('suspensions');
    harness.fixture.detectChanges();

    harness.click('[data-testid="info-stop-scope"]');
    const help = harness.testid('help-stop-scope')?.textContent ?? '';
    expect(help).toContain('one caller');
    expect(help).toContain('one API key');
    expect(help).toContain('one whole use case');
    expect(help).toContain('no switch for the');
  });

  it('describes the history in terms the reader shares', () => {
    // It used to read: `kept, because "blocked for two hours last Tuesday" is what a review asks`
    // — a note to whoever wrote the code, in the place where a sentence for the reader belongs.
    const lifted = { ...SUSPENSION, id: 's2', lifted_at: '2026-08-08T11:00:00Z' };
    const harness = setup({ suspensions: of({ suspensions: [lifted] }) });
    (harness.component as unknown as { tab: { set: (v: string) => void } }).tab.set('suspensions');
    harness.fixture.detectChanges();

    const summary = harness.testid('past-summary')?.textContent ?? '';
    expect(summary).toContain('Earlier decisions');
    expect(summary).toContain('who stopped what, when, and why');
    expect(summary).not.toContain('last Tuesday');
  });
});

describe('SecurityPage — the rule editor', () => {
  function editing(over: Partial<AnomalyRule> = {}) {
    const harness = setup({ rules: [{ ...RULE, ...over }] });
    (harness.component as unknown as { tab: { set: (v: string) => void } }).tab.set('rules');
    harness.fixture.detectChanges();
    harness.click('[data-testid="rule-toggle-1"]');
    harness.click('[data-testid="rule-edit-1"]');
    return harness;
  }

  it('shows the byte figure only for the kind that has one', () => {
    // `payload_size` needs two numbers and every other kind needs one. A form that showed the
    // second everywhere would invite a value the engine never reads.
    expect(editing({ kind: 'payload_size' }).testid('edit-parameter-1')).not.toBeNull();
    expect(editing({ kind: 'refusal_rate' }).testid('edit-parameter-1')).toBeNull();
  });

  it('asks for a duration and a rate only when the action needs them', () => {
    const harness = editing();
    const component = harness.component as unknown as { editAction: { set: (v: string) => void } };

    expect(harness.testid('edit-minutes-1')).toBeNull();

    component.editAction.set('block');
    harness.fixture.detectChanges();
    expect(harness.testid('edit-minutes-1')).not.toBeNull();
    expect(harness.testid('edit-rpm-1')).toBeNull();

    component.editAction.set('throttle');
    harness.fixture.detectChanges();
    // A throttle without a rate is not a decision (`FRD-503` §7).
    expect(harness.testid('edit-rpm-1')).not.toBeNull();
  });

  it('opens with the rule as it is, not with an empty form', () => {
    const harness = editing({ threshold: 42, window_minutes: 90, min_sample: 7, enabled: false });
    const component = harness.component as unknown as {
      editThreshold: () => number | null;
      editWindow: () => number | null;
      editSample: () => number | null;
      editEnabled: () => boolean;
    };

    expect(component.editThreshold()).toBe(42);
    expect(component.editWindow()).toBe(90);
    expect(component.editSample()).toBe(7);
    expect(component.editEnabled()).toBe(false);
  });

  it('abandons an edit when the row is closed', () => {
    // Otherwise a hidden form saves fields the reader can no longer see.
    const harness = editing();
    harness.click('[data-testid="rule-toggle-1"]');

    expect((harness.component as unknown as { editing: () => number | null }).editing()).toBeNull();
  });

  it('can switch a rule off without deleting it', () => {
    // Deleting stops the watching *and* loses the intent; switching off keeps the rule for
    // whoever asks later why it is not firing.
    const harness = editing();
    const component = harness.component as unknown as {
      editEnabled: { set: (v: boolean) => void };
      saveRule: (rule: AnomalyRule) => void;
    };
    component.editEnabled.set(false);
    component.saveRule(RULE);

    const sent = harness.calls.find((call) => call.startsWith('update:1:'))!;
    expect(JSON.parse(sent.slice('update:1:'.length)).enabled).toBe(false);
  });

  it('says the change takes a moment to reach the gateway', () => {
    const harness = editing();
    (harness.component as unknown as { saveRule: (r: AnomalyRule) => void }).saveRule(RULE);

    expect(
      (
        harness.component as unknown as { feedback: { notice: () => string | null } }
      ).feedback.notice(),
    ).toContain('few seconds');
  });
});

describe('SecurityPage — while something is in flight', () => {
  it('says it is saving, and refuses a second press', () => {
    // Two saves from one intent is how a form that looks stuck gets pressed twice.
    const pending = new Subject<AnomalyRule>();
    const harness = setup({ update: pending as unknown as Observable<AnomalyRule> });
    (harness.component as unknown as { tab: { set: (v: string) => void } }).tab.set('rules');
    harness.fixture.detectChanges();
    harness.click('[data-testid="rule-toggle-1"]');
    harness.click('[data-testid="rule-edit-1"]');

    (harness.component as unknown as { saveRule: (r: AnomalyRule) => void }).saveRule(RULE);
    harness.fixture.detectChanges();

    const save = harness.testid('rule-save-1') as HTMLButtonElement;
    expect(save.textContent).toContain('Saving…');
    expect(save.disabled).toBe(true);
    pending.complete();
  });

  it('shows a bare threshold for a kind that has no unit', () => {
    // "Undeclared means the baseline and nothing more": a kind this console has not met shows the
    // number and no unit, rather than borrowing one that would be wrong.
    const harness = setup({ rules: [{ ...RULE, kind: 'future_kind' }] });
    (harness.component as unknown as { tab: { set: (v: string) => void } }).tab.set('rules');
    harness.fixture.detectChanges();
    harness.click('[data-testid="rule-toggle-1"]');
    harness.click('[data-testid="rule-edit-1"]');

    expect(harness.text()).toContain('as counted');
  });

  it('does not print an empty reason as a blank cell in the history', () => {
    const expired = { ...SUSPENSION, id: 's9', reason: '', expires_at: '2020-01-01T00:00:00Z' };
    const harness = setup({ suspensions: of({ suspensions: [expired] }) });
    (harness.component as unknown as { tab: { set: (v: string) => void } }).tab.set('suspensions');
    harness.fixture.detectChanges();

    expect(harness.text()).toContain('expired');
  });
});
