import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
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

/** Many rows, so a pager has something to page. */
function manyRules(count: number): AnomalyRule[] {
  return Array.from({ length: count }, (_, index) => ({
    ...RULE,
    id: index + 1,
    name: `rule ${index + 1}`,
  }));
}

function manySuspensions(count: number) {
  const rows = Array.from({ length: count }, (_, index) => ({
    ...SUSPENSION,
    id: `s${index}`,
    target_value: `caller-${index}`,
    // Half of them lifted, so both lists are long enough to page.
    lifted_at: index % 2 ? '2026-08-09T10:00:00Z' : null,
    lifted_by: index % 2 ? 'sec' : null,
  }));
  return of({ suspensions: rows });
}

interface Options {
  roles?: string[];
  events?: AnomalyEvent[];
  suspensions?: Observable<{ suspensions: Suspension[] }>;
  rules?: AnomalyRule[];
  suspend?: Observable<Suspension>;
  lift?: Observable<Suspension>;
  update?: Observable<AnomalyRule>;
  /** A cursor on the first page, so the "load older" control appears. */
  moreEvents?: string | null;
  olderEvents?: Observable<{ events: AnomalyEvent[]; next_cursor: string | null; scope: string }>;
  confirmAnswer?: boolean;
}

function setup(options: Options = {}) {
  TestBed.resetTestingModule();
  const calls: string[] = [];
  /** Read calls, kept apart from writes: several cases assert that nothing was *written*. */
  const fetches: string[] = [];
  const service = {
    anomalies: (_limit?: number, _useCase?: string, cursor?: string) => {
      fetches.push(cursor ?? '');
      if (cursor) return options.olderEvents ?? of({ events: [], next_cursor: null, scope: 'all' });
      return of({
        events: options.events ?? [EVENT],
        next_cursor: options.moreEvents ?? null,
        scope: 'all',
      });
    },
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
    createGlobalRule: (rule: Record<string, unknown>) => {
      calls.push(`createGlobalRule:${JSON.stringify(rule)}`);
      return of({ ...rule, id: 99, is_global: true });
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
      // The page links to a use case's rules panel, so it needs a router. No routes: what is
      // asserted is the href the console offers, not that navigating it lands anywhere — that
      // belongs to the browser layer.
      provideRouter([]),
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
    fetches,
    element,
    component: fixture.componentInstance as unknown as Record<string, never>,
    text: () => element.textContent ?? '',
    testid: (id: string) => element.querySelector(`[data-testid="${id}"]`),
    click: (selector: string) => {
      element.querySelector<HTMLElement>(selector)?.click();
      fixture.detectChanges();
    },
    /** Switch tabs the way the existing cases do — through the signal, since the tab strip has no
     *  test ids and adding some for this would be a second way to do one thing. */
    toRules: () => {
      (fixture.componentInstance as unknown as { tab: { set: (v: string) => void } }).tab.set(
        'rules',
      );
      fixture.detectChanges();
    },
    toSuspensions: () => {
      (fixture.componentInstance as unknown as { tab: { set: (v: string) => void } }).tab.set(
        'suspensions',
      );
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

  it('links a use-case rule to the panel where it is actually edited', () => {
    // Object-level permission is not in the token, so this console cannot answer "may I edit
    // this" for a use-case rule. It names where instead — and that place now exists: pointing at
    // a screen that was not there is the `FRD-206` defect one level of indirection further out.
    const harness = onRules({ rules: [{ ...RULE, is_global: false, use_case: 'uc-a' }] });
    harness.click('[data-testid="rule-toggle-1"]');

    expect(harness.testid('rule-edit-1')).toBeNull();
    const link = harness.testid('rule-readonly-1')?.querySelector('a');
    expect(link?.getAttribute('href')).toBe('/use-cases/uc-a?tab=rules');
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

describe('SecurityPage — the rule editor is wired to the server', () => {
  // The form's own behaviour — which fields appear, what it refuses, what it sends — lives in
  // `rule-form.spec.ts`, because it is now one component used by two screens. What belongs here
  // is that this screen opens it, and that what it emits reaches the right endpoint.

  function opened() {
    const harness = setup();
    (harness.component as unknown as { tab: { set: (v: string) => void } }).tab.set('rules');
    harness.fixture.detectChanges();
    harness.click('[data-testid="rule-toggle-1"]');
    harness.click('[data-testid="rule-edit-1"]');
    return harness;
  }

  it('opens the shared form on the rule that was pressed', () => {
    const harness = opened();

    expect(harness.element.querySelector('app-rule-form')).not.toBeNull();
    expect(harness.testid('rule-1-threshold')).not.toBeNull();
  });

  it('sends what the form emits to that rule, and nothing else', () => {
    const harness = opened();
    (
      harness.component as unknown as {
        saveRule: (rule: AnomalyRule, changes: Partial<AnomalyRule>) => void;
      }
    ).saveRule(RULE, { threshold: 65, action: 'block' });

    const sent = harness.calls.find((call) => call.startsWith('update:1:'));
    expect(sent).toBeDefined();
    expect(JSON.parse(sent!.slice('update:1:'.length))).toEqual({
      threshold: 65,
      action: 'block',
    });
  });

  it('says the change takes a moment to reach the gateway', () => {
    const harness = opened();
    (
      harness.component as unknown as {
        saveRule: (rule: AnomalyRule, changes: Partial<AnomalyRule>) => void;
      }
    ).saveRule(RULE, { threshold: 65 });

    expect(
      (
        harness.component as unknown as { feedback: { notice: () => string | null } }
      ).feedback.notice(),
    ).toContain('few seconds');
  });

  it('abandons an open form when the row is closed', () => {
    // Otherwise a hidden form saves fields the reader can no longer see.
    const harness = opened();
    harness.click('[data-testid="rule-toggle-1"]');

    expect((harness.component as unknown as { editing: () => number | null }).editing()).toBeNull();
  });
});

describe('SecurityPage — older findings', () => {
  it('offers no "load older" when there is nothing older', () => {
    expect(setup().testid('events-load-more')).toBeNull();
  });

  it('appends older findings rather than replacing the page', () => {
    const older = { ...EVENT, id: 'e2', rule: 'an older finding' };
    const harness = setup({
      moreEvents: 'c1',
      olderEvents: of({ events: [older], next_cursor: null, scope: 'all' }),
    });

    harness.click('[data-testid="events-load-more"]');

    expect(harness.text()).toContain('too many refusals');
    expect(harness.text()).toContain('an older finding');
    // Asked for by cursor: findings are an append-only log, and an offset page over one shows a
    // row twice and skips another while somebody reads.
    expect(harness.fetches).toContain('c1');
  });

  it('pauses the live refresh while paging, so the reader keeps their place', () => {
    const harness = setup({
      moreEvents: 'c1',
      olderEvents: of({ events: [], next_cursor: null, scope: 'all' }),
    });

    harness.click('[data-testid="events-load-more"]');

    const toggle = harness.testid('live-toggle') as HTMLInputElement;
    expect(toggle.checked).toBe(false);
  });
});

describe('SecurityPage — while older findings are in flight', () => {
  it('says it is loading, and refuses a second press', () => {
    const pending = new Subject<{
      events: AnomalyEvent[];
      next_cursor: string | null;
      scope: string;
    }>();
    const harness = setup({
      moreEvents: 'c1',
      olderEvents: pending as unknown as Observable<{
        events: AnomalyEvent[];
        next_cursor: string | null;
        scope: string;
      }>,
    });

    harness.click('[data-testid="events-load-more"]');

    const button = harness.testid('events-load-more') as HTMLButtonElement;
    expect(button.textContent).toContain('Loading…');
    expect(button.disabled).toBe(true);

    // A second press must not start a second request against the same cursor.
    harness.click('[data-testid="events-load-more"]');
    expect(harness.fetches.filter((cursor) => cursor === 'c1').length).toBe(1);
    pending.complete();
  });

  it('reports a failed page through the page banner', () => {
    const harness = setup({
      moreEvents: 'c1',
      olderEvents: throwError(() => ({ status: 500 })) as unknown as Observable<{
        events: AnomalyEvent[];
        next_cursor: string | null;
        scope: string;
      }>,
    });

    harness.click('[data-testid="events-load-more"]');

    expect(
      (
        harness.component as unknown as { feedback: { error: () => string | null } }
      ).feedback.error(),
    ).toContain('Could not load older findings.');
  });
  // ---- authoring a rule that applies everywhere (`FRD-500`, console side) ---------------------

  it('offers a role that may act a way to author a global rule', () => {
    /** The server has accepted this since `FRD-500` — "a global rule is IT Security's to author" —
     *  and the console never offered it, so the only global rules that existed anywhere were the
     *  ones a seed had written straight into the database. `FRD-206`'s defect inverted: not a
     *  control that refuses when used, but a capability nobody could reach. */
    const harness = setup({ roles: ['it-security'] });
    harness.toRules();

    expect(harness.testid('new-global-rule')).not.toBeNull();
  });

  it('withholds it from a role that sees everything and may stop nothing', () => {
    const harness = setup({ roles: ['it-steuerung'] });
    harness.toRules();

    expect(harness.testid('new-global-rule')).toBeNull();
  });

  it('creates the rule it was given, and says where it applies', () => {
    const harness = setup({ roles: ['it-security'] });
    harness.toRules();

    const component = harness.component as unknown as {
      createRule: (changes: Record<string, unknown>) => void;
    };
    component.createRule({ name: 'spend doubled', kind: 'spend_spike', threshold: 2 });
    harness.fixture.detectChanges();

    expect(harness.calls.some((call) => call.startsWith('createGlobalRule:'))).toBe(true);
    expect(harness.text()).toContain('every use case');
  });

  // ---- the three lists that grow without bound ------------------------------------------------

  it('pages the rules rather than printing all of them', () => {
    const harness = setup({ rules: manyRules(30) });
    harness.toRules();

    expect(harness.testid('rule-pager')).not.toBeNull();
    expect(harness.element.querySelectorAll('tbody tr').length).toBeLessThan(30);
  });

  it('pages what is stopped now and what was stopped before', () => {
    /** A suspension is **kept** after it is lifted, because "blocked for two hours last Tuesday"
     *  is what a review asks — so the second list only ever grows. */
    const harness = setup({ suspensions: manySuspensions(30) });
    harness.toSuspensions();

    expect(harness.testid('active-pager')).not.toBeNull();
    expect(harness.testid('past-pager')).not.toBeNull();
  });
  it('searches the rules by name, kind and where they apply', () => {
    const harness = setup({ rules: manyRules(30) });
    harness.toRules();
    const view = harness.component as unknown as { ruleView: { search: (v: string) => void } };

    view.ruleView.search('rule 7');
    harness.fixture.detectChanges();

    expect(harness.text()).toContain('rule 7');
    expect(harness.text()).not.toContain('rule 12');
  });

  it('searches what is stopped now and what was stopped before with one box', () => {
    /** "Has this caller ever been stopped?" is one question. A search that covered only the live
     *  list would answer it wrongly and look like it had answered it. */
    const harness = setup({ suspensions: manySuspensions(30) });
    harness.toSuspensions();
    const page = harness.component as unknown as {
      searchSuspensions: (v: string) => void;
      activeView: { matches: () => unknown[] };
      pastView: { matches: () => unknown[] };
    };

    page.searchSuspensions('caller-3');
    harness.fixture.detectChanges();

    // `caller-3` and `caller-30`…`caller-39`; what matters is that **both** lists narrowed.
    expect(page.activeView.matches().length).toBeLessThan(15);
    expect(page.pastView.matches().length).toBeLessThan(15);
  });

  it('finds a rule that applies everywhere by that word', () => {
    /** A global rule has no use case, and the row says "everywhere" — so that is what somebody
     *  types to find one. A haystack that read `null` there would make the word unsearchable. */
    const harness = setup({
      rules: [
        { ...RULE, id: 1, name: 'global one', use_case: null, is_global: true },
        { ...RULE, id: 2, name: 'local one', use_case: 'uc-a', is_global: false },
      ],
    });
    harness.toRules();
    const view = harness.component as unknown as { ruleView: { search: (v: string) => void } };

    view.ruleView.search('everywhere');
    harness.fixture.detectChanges();

    expect(harness.text()).toContain('global one');
    expect(harness.text()).not.toContain('local one');
  });
});
