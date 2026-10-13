import { Type, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { provideRouter } from '@angular/router';
import { Observable, Subject, defer, of, throwError } from 'rxjs';
import { AnomalyEvent, AnomalyPage, AnomalyRule, Me, Suspension } from '../../core/api/models';
import { MeService } from '../../core/api/me.service';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { SecurityPage } from './security-page';
import { SecurityRulesPanel } from './security-rules-panel';
import { SuspensionsPanel } from './suspensions-panel';

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
  /** What `/me` lists. Absent means both of this page's powers: stopping and authoring. */
  permissions?: string[];
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
  /** The first page, one answer per load in order — the last one repeats. Default: `events`. */
  firstPages?: Observable<AnomalyPage>[];
  confirmAnswer?: boolean;
  /** `/me` answers with this failure instead of a caller. */
  meFails?: unknown;
  /** The use-case list answers with this failure. */
  listFails?: unknown;
}

function setup(options: Options = {}) {
  TestBed.resetTestingModule();
  const calls: string[] = [];
  /** Read calls, kept apart from writes: several cases assert that nothing was *written*. */
  const fetches: string[] = [];
  /** How often each list was asked for — a live view is one that asks again. */
  const counts = { firstPages: 0, suspensions: 0 };
  const service = {
    anomalies: (_limit?: number, _useCase?: string, cursor?: string) => {
      fetches.push(cursor ?? '');
      if (cursor) return options.olderEvents ?? of({ events: [], next_cursor: null, scope: 'all' });
      const pages = options.firstPages;
      if (pages?.length) return pages[Math.min(counts.firstPages++, pages.length - 1)];
      counts.firstPages += 1;
      return of({
        events: options.events ?? [EVENT],
        next_cursor: options.moreEvents ?? null,
        scope: 'all',
      });
    },
    suspensions: () =>
      defer(() => {
        counts.suspensions += 1;
        return options.suspensions ?? of({ suspensions: [SUSPENSION] });
      }),
    globalRules: () => of(options.rules ?? [RULE]),
    // The scope picker's source: without it a stop could only ever apply everywhere.
    list: () =>
      options.listFails
        ? throwError(() => options.listFails)
        : of([{ slug: 'kundenservice' }, { slug: 'entwicklung' }]),
    suspend: (body: Record<string, unknown>) => {
      calls.push(
        `suspend:${body['target']}:${body['target_value']}:${body['use_case']}:${body['action']}:${body['throttle_rpm']}`,
      );
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
      // asserted is the href the console offers, not where navigating it lands.
      provideRouter([]),
      { provide: UseCaseService, useValue: service },
      {
        provide: MeService,
        useValue: {
          currency: signal(''),
          get: () =>
            options.meFails
              ? throwError(() => options.meFails)
              : of({
                  username: 'itsec',
                  roles: options.roles ?? [],
                  permissions: options.permissions ?? [
                    'incident.suspend',
                    'anomaly.rule.global.write',
                  ],
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
  /** Switch tabs through the signal: the tab strip has no test ids, and adding some would be a
   *  second way to do one thing. */
  const toTab = (name: string) => {
    (fixture.componentInstance as unknown as { tab: { set: (v: string) => void } }).tab.set(name);
    fixture.detectChanges();
  };
  /** A tab's panel, with its tab opened first: a panel renders its content only while shown. */
  const panel = (name: string, type: Type<unknown>) => {
    toTab(name);
    return fixture.debugElement.query(By.directive(type)).componentInstance as Record<
      string,
      never
    >;
  };
  /**
   * Type into a field, or choose in a select, the way a person does: through its DOM event.
   *
   * Awaited, because a control inside a `<form>` is attached to it in a microtask
   * (`NgForm.addControl`): typing into a field that has just appeared reaches no handler, and a
   * test that does not wait proves nothing about the form.
   */
  const enter = async (selector: string, value: string) => {
    await fixture.whenStable();
    const field = element.querySelector<HTMLInputElement | HTMLSelectElement>(selector)!;
    field.value = value;
    field.dispatchEvent(new Event(field instanceof HTMLSelectElement ? 'change' : 'input'));
    fixture.detectChanges();
    await fixture.whenStable();
  };
  /** Submit a form the way the Enter key or its submit button does. */
  const submit = (selector: string) => {
    element.querySelector<HTMLFormElement>(selector)!.dispatchEvent(new Event('submit'));
    fixture.detectChanges();
  };
  return {
    fixture,
    calls,
    counts,
    fetches,
    element,
    enter,
    submit,
    /** The banner the page reports through. */
    alert: () => element.querySelector('[role="alert"]')?.textContent?.trim() ?? null,
    notice: () => element.querySelector('[role="status"]')?.textContent?.trim() ?? null,
    component: fixture.componentInstance as unknown as Record<string, never>,
    text: () => element.textContent ?? '',
    testid: (id: string) => element.querySelector(`[data-testid="${id}"]`),
    click: (selector: string) => {
      element.querySelector<HTMLElement>(selector)?.click();
      fixture.detectChanges();
    },
    toTab,
    toRules: () => toTab('rules'),
    toSuspensions: () => toTab('suspensions'),
    /** The suspensions tab's panel, which owns the kill switch. */
    suspensionsPanel: () => panel('suspensions', SuspensionsPanel),
    /** The rules tab's panel, which owns authoring and editing. */
    rulesPanel: () => panel('rules', SecurityRulesPanel),
  };
}

describe('SecurityPage — findings', () => {
  it('shows a finding with the numbers it was drawn from', () => {
    // A finding nobody can check is a finding nobody acts on: "how bad, out of how many".
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

  it('offers the kill switch to whoever may suspend', () => {
    const { component, fixture, testid } = setup({ permissions: ['incident.suspend'] });
    (component as unknown as { tab: { set: (v: string) => void } }).tab.set('suspensions');
    fixture.detectChanges();

    expect(testid('stop-toggle')).not.toBeNull();
    expect(testid('stop-readonly')).toBeNull();
  });

  it('withholds it from a reader who only sees, and says who does it', () => {
    // Seeing every finding is not stopping anything; a button that answers 403 is the defect
    // `FRD-206` names.
    const { component, fixture, testid } = setup({ permissions: ['anomaly.read_all'] });
    (component as unknown as { tab: { set: (v: string) => void } }).tab.set('suspensions');
    fixture.detectChanges();

    expect(testid('stop-toggle')).toBeNull();
    expect(testid('stop-readonly')?.textContent).toContain('IT Security');
  });

  it('stops traffic through the server, with a reason, from the form', async () => {
    // Driven through the form a person submits: a method call would stay green with the form's
    // `(ngSubmit)` unbound or bound to the wrong handler.
    const harness = setup();
    harness.toSuspensions();
    harness.click('[data-testid="stop-toggle"]');
    await harness.enter('[data-testid="stop-value"]', 'ada');
    await harness.enter('#stop-reason', 'probing');

    harness.submit('form.disclosure');

    expect(harness.calls).toContain('suspend:subject:ada:null:block:null');
    expect(harness.notice()).toContain('ada is stopped');
  });

  it('keeps a half-typed stop while the reader looks at another tab', () => {
    // Looking a subject up under Findings mid-incident must not cost the form.
    const harness = setup();
    const panel = harness.suspensionsPanel() as unknown as {
      showStop: { set: (v: boolean) => void };
      targetValue: { set: (v: string) => void; (): string };
    };
    panel.showStop.set(true);
    panel.targetValue.set('ada');
    (harness.component as unknown as { tab: { set: (v: string) => void } }).tab.set('findings');
    harness.fixture.detectChanges();
    expect(harness.testid('stop-toggle')).toBeNull();

    harness.toSuspensions();

    expect(harness.suspensionsPanel()).toBe(panel as never);
    expect(panel.targetValue()).toBe('ada');
    expect(harness.testid('stop-toggle')?.textContent).toContain('Cancel');
  });

  it('scopes a stop to one use case, and holds a caller to a rate', async () => {
    /** A scope, a throttle and its rate: the table renders them (a *rule* can produce them), and a
     *  person must be able to set them too, or every manual decision is a full block everywhere. */
    const harness = setup();
    harness.toSuspensions();
    harness.click('[data-testid="stop-toggle"]');
    await harness.enter('[data-testid="stop-value"]', 'ada');
    await harness.enter('[data-testid="stop-action"]', 'throttle');

    // The server refuses a throttle without a rate; the form refuses it first.
    const submitButton = harness.testid('stop-submit') as HTMLButtonElement;
    expect(submitButton.disabled).toBe(true);

    await harness.enter('[data-testid="stop-rpm"]', '10');
    await harness.enter('[data-testid="stop-scope"]', 'kundenservice');
    harness.submit('form.disclosure');

    expect(harness.calls).toContain('suspend:subject:ada:kundenservice:throttle:10');
    // A throttle holds traffic to a rate; "stopped" would tell the reader it is refused outright.
    expect(harness.notice()).toContain('ada is held to 10 requests a minute');
    expect(harness.notice()).not.toContain('stopped');
  });

  it('asks no "Where" of a whole use case, and sends no scope for one', async () => {
    /** The matcher needs the request's use case to equal the scope **and** the stopped value, so
     *  "uc-a, only in kundenservice" matched no request at all while it was listed as in force. A
     *  whole use case is stopped wherever it is called. */
    const harness = setup();
    harness.toSuspensions();
    harness.click('[data-testid="stop-toggle"]');
    await harness.enter('[data-testid="stop-scope"]', 'kundenservice');

    await harness.enter('[data-testid="stop-target"]', 'use_case');
    expect(harness.testid('stop-scope')).toBeNull();

    await harness.enter('[data-testid="stop-value"]', 'uc-a');
    harness.submit('form.disclosure');

    expect(harness.calls).toContain('suspend:use_case:uc-a:null:block:null');
  });

  it('offers the scope and action controls, with the use cases it could load', () => {
    const harness = setup();
    const panel = harness.suspensionsPanel() as unknown as {
      showStop: { set: (v: boolean) => void };
    };
    panel.showStop.set(true);
    harness.fixture.detectChanges();

    const html = harness.fixture.nativeElement as HTMLElement;
    expect(html.querySelector('[data-testid="stop-scope"]')).not.toBeNull();
    expect(html.querySelector('[data-testid="stop-action"]')).not.toBeNull();
    // The rate only exists for a throttle — an input that is always there reads as always needed.
    expect(html.querySelector('[data-testid="stop-rpm"]')).toBeNull();
    expect(html.textContent).toContain('only in kundenservice');
  });

  it('says the decision takes a moment to reach every instance', () => {
    // The cache is deliberately a few seconds behind (`FRD-503` §4.1); a console implying "done"
    // would have somebody test it at once and conclude it did not work. Read from the page's
    // banner, which the panel reports into.
    const harness = setup();
    const panel = harness.suspensionsPanel() as unknown as {
      targetValue: { set: (v: string) => void };
      stop: () => void;
    };
    panel.targetValue.set('ada');
    panel.stop();

    const page = harness.component as unknown as { feedback: { notice: () => string | null } };
    expect(page.feedback.notice()).toContain('few seconds');
  });

  it('asks before restoring access, and does nothing when declined', () => {
    const restore = '[aria-label="Restore access for ada"]';
    const declined = setup({ confirmAnswer: false });
    declined.toSuspensions();
    declined.click(restore);
    expect(declined.calls).toEqual([]);

    const accepted = setup({ confirmAnswer: true });
    accepted.toSuspensions();
    accepted.click(restore);
    expect(accepted.calls).toContain('lift:s1');
  });

  it('keeps lifted and expired ones out of the active list but on the page', () => {
    // "Blocked for two hours last Tuesday" is exactly what a review asks.
    const now = { ...SUSPENSION, target_value: 'still-stopped' };
    const lifted = {
      ...SUSPENSION,
      id: 's2',
      target_value: 'was-lifted',
      lifted_at: '2026-08-08T11:00:00Z',
    };
    const expired = {
      ...SUSPENSION,
      id: 's3',
      target_value: 'ran-out',
      expires_at: '2020-01-01T00:00:00Z',
    };
    const harness = setup({ suspensions: of({ suspensions: [now, lifted, expired] }) });
    harness.toSuspensions();
    const active = harness.testid('active-suspensions')?.textContent ?? '';
    const past = harness.testid('past-suspensions')?.textContent ?? '';

    expect(active).toContain('still-stopped');
    expect(active).not.toContain('was-lifted');
    expect(active).not.toContain('ran-out');
    expect(past).toContain('was-lifted');
    expect(past).toContain('ran-out');
    expect(past).not.toContain('still-stopped');
  });

  it('does not report a refused suspension list as a page failure', () => {
    // A caller who may see findings and not suspensions gets a 403: an answer about a permission,
    // not a broken screen.
    const harness = setup({
      permissions: ['anomaly.read_all'],
      suspensions: throwError(() => ({ status: 403 })),
    });
    const component = harness.component as unknown as {
      feedback: { error: () => string | null };
    };

    expect(component.feedback.error()).toBeNull();
  });

  it('says who may see stopped traffic instead of claiming nothing is stopped', () => {
    // IT Steuerung was told "You can see everything that is stopped" and "Nothing is stopped"
    // over a list the gateway had refused to give it.
    const harness = setup({
      permissions: ['anomaly.read_all'],
      suspensions: throwError(() => ({ status: 403 })),
    });
    (harness.component as unknown as { tab: { set: (v: string) => void } }).tab.set('suspensions');
    harness.fixture.detectChanges();
    const element = harness.fixture.nativeElement as HTMLElement;

    expect(element.querySelector('[data-testid="suspensions-hidden"]')).not.toBeNull();
    expect(harness.text()).not.toContain('Nothing is stopped');
    expect(harness.text()).not.toContain('You can see everything that is stopped');
    expect(element.querySelector('.tab.is-active .tab__count')).toBeNull();
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
    // A throttle without its rate is a decision nobody can review (`FRD-503` §7).
    const { text } = onSuspensions([{ ...SUSPENSION, action: 'throttle', throttle_rpm: 12 }]);

    expect(text()).toContain('12/min');
  });

  it('distinguishes "until lifted" from an expiry', () => {
    expect(onSuspensions([{ ...SUSPENSION, expires_at: null }]).text()).toContain('until lifted');
  });

  it('says when a stop applies everywhere rather than to one use case', () => {
    // A global stop and a scoped one are very different decisions to review.
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
    // The server would refuse it; a form that posts an empty target makes the console look
    // unreliable rather than the input.
    const harness = setup({ suspensions: of({ suspensions: [SUSPENSION] }) });
    const panel = harness.suspensionsPanel() as unknown as {
      showStop: { set: (v: boolean) => void };
      targetValue: { set: (v: string) => void };
      stop: () => void;
    };
    panel.showStop.set(true);
    panel.targetValue.set('   ');
    panel.stop();

    expect(harness.calls).toEqual([]);
  });

  it('clears the form once the stop is in force', () => {
    const harness = setup();
    const panel = harness.suspensionsPanel() as unknown as {
      targetValue: { set: (v: string) => void; (): string };
      reason: { set: (v: string) => void; (): string };
      showStop: { set: (v: boolean) => void; (): boolean };
      stop: () => void;
    };
    panel.showStop.set(true);
    panel.targetValue.set('ada');
    panel.reason.set('probing');
    panel.stop();

    // A form that keeps its text after a successful submit is how one decision gets made twice.
    expect(panel.targetValue()).toBe('');
    expect(panel.reason()).toBe('');
    expect(panel.showStop()).toBe(false);
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
    // `payload_size` needs a byte figure as well as a count (`FRD-501` §7).
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
            list: () => of([]),
            globalRules: () => throwError(() => ({ status: 500 })),
          },
        },
        {
          provide: MeService,
          useValue: {
            currency: signal(''),
            get: () => of({ roles: [], permissions: [] } as unknown as Me),
          },
        },
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
    // Six columns is as much as a table can be read at, so the rest goes under the row.
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
    // As the gateway records it: a global rule raised about API keys carries no use case.
    const harness = setup({
      events: [
        {
          ...EVENT,
          rule: 'new address',
          kind: 'new_source_ip',
          use_case: null,
          target: 'credential',
        },
      ],
      rules: [RULE],
    });
    harness.click('[data-testid="event-toggle-e1"]');

    expect(harness.text()).toContain('The rule behind it');
  });
});

describe('SecurityPage — which rule a finding came from', () => {
  /** A global rule and a use-case rule may share a name (two separate unique constraints), and a
   *  finding carries the name, not the rule's id. Listed use-case rule first, so a lookup by name
   *  alone finds the wrong one. */
  const LOCAL: AnomalyRule = {
    ...RULE,
    id: 2,
    name: 'spend spike',
    kind: 'spend_spike',
    target: 'subject',
    is_global: false,
    use_case: 'uc-a',
    threshold: 200,
    min_sample: 20,
    action: 'alert',
  };
  const GLOBAL: AnomalyRule = {
    ...LOCAL,
    id: 3,
    is_global: true,
    use_case: null,
    threshold: 300,
    action: 'block',
    action_minutes: 30,
  };
  const FINDING: AnomalyEvent = {
    ...EVENT,
    rule: 'spend spike',
    kind: 'spend_spike',
    target: 'subject',
    observed: 450,
    threshold: 300,
  };

  function behind(event: AnomalyEvent): string {
    const harness = setup({ events: [event], rules: [LOCAL, GLOBAL] });
    harness.click('[data-testid="event-toggle-e1"]');
    const sentences = [...harness.element.querySelectorAll('.detail-sentence')];
    return sentences.find((p) => p.textContent?.includes('The rule behind it'))?.textContent ?? '';
  }

  it('explains a finding with no use case by the global rule', () => {
    const sentence = behind({ ...FINDING, use_case: null });

    expect(sentence).toContain('3× the previous window');
    expect(sentence).toContain('stopped for 30 minutes');
  });

  it("explains a use case's finding by that use case's rule", () => {
    const sentence = behind({ ...FINDING, use_case: 'uc-a', threshold: 200 });

    expect(sentence).toContain('2× the previous window');
    expect(sentence).toContain('nothing is taken away');
  });
});

describe('SecurityPage — a threshold in the unit it means', () => {
  it('shows a spike rule as the multiple it is, in the table and the detail grid', () => {
    /** Stored as 300 — percent of the previous window — and printed "300 × the previous window"
     *  in the table, three hundred times, while the sentence below said 3×. */
    const harness = setup({
      rules: [{ ...RULE, kind: 'spend_spike', threshold: 300, min_sample: 20, target: 'subject' }],
    });
    harness.toRules();
    const row = harness.element.querySelector('[data-testid="rules-panel"] tbody tr')!;
    expect(row.textContent).toContain('3× the previous window');
    expect(row.textContent).not.toContain('300');

    harness.click('[data-testid="rule-toggle-1"]');
    const grid = harness.element.querySelector('[data-testid="rules-panel"] .detail-grid')!;
    expect(grid.textContent).toContain('3× the previous window');
    expect(grid.textContent).not.toContain('300');
  });

  it("shows a spike finding's measurement as a multiple too", () => {
    const harness = setup({
      events: [{ ...EVENT, kind: 'spend_spike', observed: 450, threshold: 300 }],
    });
    const measured =
      harness.element.querySelector('[data-testid="findings-table"] tbody tr td:nth-child(5)')!
        .textContent ?? '';

    expect(measured).toContain('4.5×');
    expect(measured).toContain('3×');
    expect(measured).not.toContain('450');
  });
});

describe('SecurityPage — what live refreshes', () => {
  afterEach(() => vi.useRealTimers());

  it('re-reads what is stopped on the live tick, and lets an expired stop leave "in force"', () => {
    /** A rule's `block` creates a suspension when it fires, so a page that only re-polled findings
     *  never showed it; and "in force" was judged against a clock read once, so a 30-minute block
     *  stayed listed, with a Restore button, long after it had run out. */
    vi.useFakeTimers({ now: new Date('2026-10-12T10:00:00Z') });
    const expiring = { ...SUSPENSION, expires_at: '2026-10-12T10:00:30Z' };
    const harness = setup({ suspensions: of({ suspensions: [expiring] }) });
    expect(harness.testid('active-banner')).not.toBeNull();
    const asked = harness.counts.suspensions;

    vi.advanceTimersByTime(5 * 60 * 1000);
    harness.fixture.detectChanges();

    expect(harness.counts.suspensions).toBeGreaterThan(asked);
    expect(harness.testid('active-banner')).toBeNull();
  });

  it('reports a failed findings load, and stops saying it is loading', () => {
    // The call-site half of `Live` requiring `onError`: "Loading findings…" for ever, with no
    // banner, is how a gateway outage looked from here.
    const harness = setup({
      firstPages: [
        throwError(() => ({ status: 502, error: { error: { message: 'gateway down' } } })),
      ],
    });

    expect(harness.alert()).toContain('gateway down');
    expect(harness.element.querySelector('.spinner')).toBeNull();
    // Nothing was read, so nothing may be said about the traffic.
    expect(harness.text()).not.toContain('Nothing has crossed a threshold');
    expect(harness.testid('findings-failed')).not.toBeNull();
  });

  it('takes its own banner down once the findings load again', () => {
    const harness = setup({
      firstPages: [
        throwError(() => ({ status: 502, error: { error: { message: 'gateway down' } } })),
        of({ events: [EVENT], next_cursor: null, scope: 'all' }),
      ],
    });
    expect(harness.alert()).toContain('gateway down');

    harness.click('[data-testid="refresh-now"]');

    expect(harness.alert()).toBeNull();
    expect(harness.text()).toContain('too many refusals');
  });
});

describe('SecurityPage — how stale the screen is', () => {
  afterEach(() => vi.useRealTimers());

  it('lets the "updated" stamp age by itself while nothing reloads', async () => {
    /** Zoneless, a label computed from `new Date()` re-renders only when something else does —
     *  with live off, never, so it said "updated 0s ago" indefinitely. The stamp is `Live.ago`, a
     *  signal over a ticking clock, and the scheduler re-renders it on its own. */
    vi.useFakeTimers();
    const harness = setup();
    harness.fixture.autoDetectChanges();
    harness.click('[data-testid="live-toggle"]');
    await vi.advanceTimersByTimeAsync(0);
    expect(harness.testid('live-stamp')?.textContent).toContain('updated 0s ago');

    // Half a second past the twentieth tick, so the render that tick scheduled has landed.
    await vi.advanceTimersByTimeAsync(20_500);

    expect(harness.testid('live-stamp')?.textContent).toContain('updated 20s ago');
  });
});

describe('SecurityPage — paging findings under a live refresh', () => {
  it('keeps the older findings when a refresh that was in flight lands afterwards', () => {
    const inFlight = new Subject<AnomalyPage>();
    const older = { ...EVENT, id: 'e2', rule: 'an older finding' };
    const harness = setup({
      firstPages: [of({ events: [EVENT], next_cursor: 'c1', scope: 'all' }), inFlight],
      olderEvents: of({ events: [older], next_cursor: null, scope: 'all' }),
    });
    harness.click('[data-testid="refresh-now"]');
    harness.click('[data-testid="events-load-more"]');
    expect(harness.text()).toContain('an older finding');

    inFlight.next({ events: [EVENT], next_cursor: 'c1', scope: 'all' });
    inFlight.complete();
    harness.fixture.detectChanges();

    expect(harness.text()).toContain('an older finding');
  });

  it('counts what is loaded, and says when there is more', () => {
    // The tab counted the first page as if it were every finding.
    expect(setup({ moreEvents: 'c1' }).testid('findings-count')?.textContent?.trim()).toBe('1+');
    expect(setup().testid('findings-count')?.textContent?.trim()).toBe('1');
  });
});

describe('SecurityPage — saying what could not be read', () => {
  it('says at the scope picker that the use cases could not be loaded', () => {
    /** Mid-incident, a picker offering only "everywhere" with nothing said reads as "nothing
     *  narrower exists" — and everywhere is the broadest stop there is. */
    const harness = setup({
      listFails: { status: 500, error: { error: { message: 'Management is unreachable.' } } },
    });
    harness.toSuspensions();
    harness.click('[data-testid="stop-toggle"]');

    expect(harness.testid('stop-scope-error')?.textContent).toContain('Management is unreachable.');
  });

  it('reports a failed /me, and does not tell the reader who else stops traffic', () => {
    // An IT Security user whose `/me` failed was told stopping traffic is done by IT Security.
    const harness = setup({ meFails: { status: 503 } });
    expect(harness.alert()).toContain('Could not load your permissions');

    harness.toSuspensions();
    const readonly = harness.testid('stop-readonly')?.textContent ?? '';
    expect(readonly).toContain('could not be loaded');
    expect(readonly).not.toContain('done by IT');
  });

  it("says when only the findings on the reader's own use cases are shown", () => {
    const narrowed = setup({
      firstPages: [of({ events: [EVENT], next_cursor: null, scope: 'use_cases' })],
    });
    expect(narrowed.testid('findings-scope')?.textContent).toContain('your own use cases');

    expect(setup().testid('findings-scope')).toBeNull();
  });
});

describe('SecurityPage — the record of past decisions', () => {
  it('says when each one started and ended, what it did, and where it applied', () => {
    /** "Was this key blocked everywhere or only in uc-a, and for how long?" — a review's question,
     *  which a name, an author and a reason cannot answer. */
    const lifted: Suspension = {
      ...SUSPENSION,
      id: 's2',
      created_at: '2026-08-08T12:00:00Z',
      lifted_at: '2026-08-08T13:30:00Z',
      lifted_by: 'user:boss',
      action: 'throttle',
      throttle_rpm: 12,
    };
    const expired: Suspension = {
      ...SUSPENSION,
      id: 's3',
      use_case: null,
      created_at: '2026-08-01T12:00:00Z',
      expires_at: '2026-08-01T13:00:00Z',
    };
    const harness = setup({ suspensions: of({ suspensions: [lifted, expired] }) });
    harness.toSuspensions();
    const headers = [
      ...harness.element.querySelectorAll('[data-testid="past-suspensions"] th'),
    ].map((th) => th.textContent?.trim());
    const when = /\d{4}-\d{2}-\d{2} \d{2}:\d{2}/g;

    expect(headers).toEqual(['Target', 'Action', 'From', 'Ended', 'By', 'Reason']);
    const first = harness.testid('past-s2')?.textContent ?? '';
    expect(first.match(when)?.length).toBe(2);
    expect(first).toContain('subject · kundenservice');
    expect(first).toContain('12/min');
    expect(first).toContain('lifted by user:boss');
    const second = harness.testid('past-s3')?.textContent ?? '';
    expect(second.match(when)?.length).toBe(2);
    expect(second).toContain('everywhere');
    expect(second).toContain('blocked');
    expect(second).toContain('expired');
  });
});

describe('SecurityPage — the two tabs look like one page', () => {
  it('gives the rules and the suspensions tabs one card-header pattern', () => {
    // Switching tabs moved the title by 8 px and the create action changed size.
    const harness = setup();
    harness.toRules();
    const rules = harness.testid('rules-panel') as HTMLElement;
    expect(rules.getAttribute('style')).toBeNull();
    expect(rules.querySelector(':scope > .spread')?.getAttribute('style')).toBeNull();
    expect(harness.testid('new-global-rule')?.className).toBe('btn btn--sm btn--primary');

    harness.toSuspensions();
    const stops = harness.element.querySelector('app-suspensions-panel .card') as HTMLElement;
    expect(stops.getAttribute('style')).toBeNull();
    expect(harness.testid('stop-toggle')?.classList.contains('btn--sm')).toBe(true);
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
    const harness = onRules();
    harness.click('[data-testid="rule-toggle-1"]');

    const detail = harness.testid('rule-detail-1')?.textContent ?? '';
    expect(detail).toContain('address that has not been seen before');
    expect(detail).toContain('per API key');
    expect(detail).toContain('60 minutes');
  });

  it('offers Edit to whoever may write global rules and explains the absence to everyone else', () => {
    const allowed = onRules({ permissions: ['anomaly.rule.global.write'] });
    allowed.click('[data-testid="rule-toggle-1"]');
    expect(allowed.testid('rule-edit-1')).not.toBeNull();

    const readOnly = onRules({ permissions: ['anomaly.read_all'] });
    readOnly.click('[data-testid="rule-toggle-1"]');
    expect(readOnly.testid('rule-edit-1')).toBeNull();
    expect(readOnly.testid('rule-readonly-1')?.textContent).toContain('IT Security');
  });

  it('links a use-case rule to the panel where it is actually edited', () => {
    // Object-level permission is not in the token, so the console names where the rule is edited
    // instead of answering "may I edit this" (`FRD-206`).
    const harness = onRules({ rules: [{ ...RULE, is_global: false, use_case: 'uc-a' }] });
    harness.click('[data-testid="rule-toggle-1"]');

    expect(harness.testid('rule-edit-1')).toBeNull();
    const link = harness.testid('rule-readonly-1')?.querySelector('a');
    expect(link?.getAttribute('href')).toBe('/use-cases/uc-a?tab=rules');
  });

  it('asks before deleting a rule, and says what stops being watched', () => {
    const declined = onRules({ confirmAnswer: false });
    declined.click('[data-testid="rule-toggle-1"]');
    declined.click('[data-testid="rule-delete-1"]');
    expect(declined.calls).toEqual([]);

    const accepted = onRules({ confirmAnswer: true });
    accepted.click('[data-testid="rule-toggle-1"]');
    accepted.click('[data-testid="rule-delete-1"]');
    expect(accepted.calls).toContain('delete-rule:1');
    expect(accepted.notice()).toContain('"new address" deleted.');
  });
});

describe('SecurityPage — saying what a control is', () => {
  it('explains how far the kill switch reaches, and how far it does not', () => {
    // A reader has to know what a stop reaches before deciding to use it, and there is
    // deliberately no switch for the installation.
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
    // The summary is a sentence for the reader, not a note to whoever wrote the code.
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
  // The form's own behaviour lives in `rule-form.spec.ts`. What belongs here is that this screen
  // opens it, and that what it emits reaches the right endpoint.

  async function opened() {
    const harness = setup();
    harness.toRules();
    harness.click('[data-testid="rule-toggle-1"]');
    harness.click('[data-testid="rule-edit-1"]');
    // The form fills its fields from an effect and `ngModel` writes in a microtask.
    await harness.fixture.whenStable();
    return harness;
  }

  it('opens the shared form on the rule that was pressed', async () => {
    const harness = await opened();

    expect(harness.element.querySelector('app-rule-form')).not.toBeNull();
    expect((harness.testid('rule-1-threshold') as HTMLInputElement).value).toBe('1');
  });

  it('sends what the form emits to that rule, from the form', async () => {
    // Through the submitted form, so the panel's `(saved)` binding is what is under test.
    const harness = await opened();
    await harness.enter('[data-testid="rule-1-threshold"]', '3');
    await harness.enter('[data-testid="rule-1-action"]', 'block');
    await harness.enter('[data-testid="rule-1-minutes"]', '30');

    harness.submit('app-rule-form form');

    const sent = harness.calls.find((call) => call.startsWith('update:1:'));
    expect(sent).toBeDefined();
    expect(JSON.parse(sent!.slice('update:1:'.length))).toMatchObject({
      kind: 'new_source_ip',
      threshold: 3,
      action: 'block',
      action_minutes: 30,
    });
    expect(harness.calls.some((call) => call.startsWith('createGlobalRule:'))).toBe(false);
  });

  it('says the change takes a moment to reach the gateway', async () => {
    const harness = await opened();
    await harness.enter('[data-testid="rule-1-threshold"]', '2');
    harness.submit('app-rule-form form');

    expect(harness.notice()).toContain('few seconds');
  });

  it('keeps a half-typed edit while the reader looks at another tab', async () => {
    /** The panel's own comment promised it: the form owns what was typed, and a panel removed on
     *  a tab switch rebuilt the form from the stored rule — Save would then store the old value. */
    const harness = await opened();
    await harness.enter('[data-testid="rule-1-threshold"]', '450');

    harness.toTab('findings');
    expect((harness.testid('rules-panel') as HTMLElement).hidden).toBe(true);
    harness.toRules();
    await harness.fixture.whenStable();

    expect((harness.testid('rule-1-threshold') as HTMLInputElement).value).toBe('450');
  });

  it('abandons an open form when the row is closed', async () => {
    // Otherwise a hidden form saves fields the reader can no longer see.
    const harness = await opened();
    harness.click('[data-testid="rule-toggle-1"]');

    expect(harness.element.querySelector('app-rule-form')).toBeNull();
    expect(
      (harness.rulesPanel() as unknown as { editing: () => number | null }).editing(),
    ).toBeNull();
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

  it('offers whoever may write global rules a way to author one', () => {
    // A capability with no way in is `FRD-206`'s defect inverted.
    const harness = setup({ permissions: ['anomaly.rule.global.write'] });
    harness.toRules();

    expect(harness.testid('new-global-rule')).not.toBeNull();
  });

  it('withholds it from a reader who sees everything and may author nothing', () => {
    const harness = setup({ permissions: ['anomaly.read_all'] });
    harness.toRules();

    expect(harness.testid('new-global-rule')).toBeNull();
  });

  it('creates the rule it was given, and says where it applies', async () => {
    // Through the window's form: 200 percent of the previous window is "spend doubled", which is
    // the threshold the server accepts — `2` it refuses as no growth at all.
    const harness = setup({ permissions: ['anomaly.rule.global.write'] });
    harness.toRules();
    harness.click('[data-testid="new-global-rule"]');
    await harness.fixture.whenStable();
    await harness.enter('[data-testid="new-global-rule-name"]', 'spend doubled');
    await harness.enter('[data-testid="new-global-rule-kind"]', 'spend_spike');
    await harness.enter('[data-testid="new-global-rule-threshold"]', '200');

    harness.submit('[data-testid="global-rule-editor"] form');

    const sent = harness.calls.find((call) => call.startsWith('createGlobalRule:'));
    expect(JSON.parse(sent!.slice('createGlobalRule:'.length))).toMatchObject({
      name: 'spend doubled',
      kind: 'spend_spike',
      threshold: 200,
    });
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
    // A suspension is kept after it is lifted, so the second list only ever grows.
    const harness = setup({ suspensions: manySuspensions(30) });
    harness.toSuspensions();

    expect(harness.testid('active-pager')).not.toBeNull();
    expect(harness.testid('past-pager')).not.toBeNull();
  });
  it('searches the rules by name, kind and where they apply', () => {
    const harness = setup({ rules: manyRules(30) });
    const view = harness.rulesPanel() as unknown as { ruleView: { search: (v: string) => void } };

    view.ruleView.search('rule 7');
    harness.fixture.detectChanges();

    expect(harness.text()).toContain('rule 7');
    expect(harness.text()).not.toContain('rule 12');
  });

  it('searches what is stopped now and what was stopped before with one box', () => {
    // "Has this caller ever been stopped?" is one question; a search over only the live list would
    // answer it wrongly.
    const harness = setup({ suspensions: manySuspensions(30) });
    const panel = harness.suspensionsPanel() as unknown as {
      searchSuspensions: (v: string) => void;
      activeView: { matches: () => unknown[] };
      pastView: { matches: () => unknown[] };
    };

    panel.searchSuspensions('caller-3');
    harness.fixture.detectChanges();

    // `caller-3` and `caller-30`…`caller-39`; what matters is that **both** lists narrowed.
    expect(panel.activeView.matches().length).toBeLessThan(15);
    expect(panel.pastView.matches().length).toBeLessThan(15);
  });

  it('finds a rule that applies everywhere by that word', () => {
    // A global rule's row says "everywhere", so that is what somebody types to find one.
    const harness = setup({
      rules: [
        { ...RULE, id: 1, name: 'global one', use_case: null, is_global: true },
        { ...RULE, id: 2, name: 'local one', use_case: 'uc-a', is_global: false },
      ],
    });
    const view = harness.rulesPanel() as unknown as { ruleView: { search: (v: string) => void } };

    view.ruleView.search('everywhere');
    harness.fixture.detectChanges();

    expect(harness.text()).toContain('global one');
    expect(harness.text()).not.toContain('local one');
  });
});

describe('SecurityPage — stopping and authoring are separate permissions', () => {
  /** Everything permission-gated this page renders, with both tabs visited and the rule opened. */
  function offered(options: Options): string[] {
    const harness = setup(options);
    const found: string[] = [];
    harness.toSuspensions();
    if (harness.testid('stop-toggle')) found.push('stop');
    if (harness.element.querySelector('[aria-label="Restore access for ada"]'))
      found.push('restore');
    harness.toRules();
    if (harness.testid('new-global-rule')) found.push('new-rule');
    harness.click('[data-testid="rule-toggle-1"]');
    if (harness.testid('rule-edit-1')) found.push('edit-rule');
    return found;
  }

  it('offers the kill switch and restoring to `incident.suspend`, and no rule authoring', () => {
    expect(offered({ permissions: ['incident.suspend'] })).toEqual(['stop', 'restore']);
  });

  it('offers rule authoring to `anomaly.rule.global.write`, and no kill switch', () => {
    expect(offered({ permissions: ['anomaly.rule.global.write'] })).toEqual([
      'new-rule',
      'edit-rule',
    ]);
  });

  it('offers nothing to a reader who investigates and does not stop or author', () => {
    expect(offered({ permissions: ['anomaly.read_all', 'incident.investigate'] })).toEqual([]);
  });

  it('asks the permissions and never the roles', () => {
    // The server decides what a role holds, so a role with nothing listed is offered nothing…
    expect(offered({ roles: ['global-admin', 'it-security'], permissions: [] })).toEqual([]);
    // …and the permissions are enough on their own.
    expect(
      offered({ roles: [], permissions: ['incident.suspend', 'anomaly.rule.global.write'] }),
    ).toEqual(['stop', 'restore', 'new-rule', 'edit-rule']);
  });
});
