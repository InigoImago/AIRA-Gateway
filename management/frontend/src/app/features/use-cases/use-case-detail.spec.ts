import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, provideRouter } from '@angular/router';
import { Observable, of, throwError } from 'rxjs';
import {
  ApiKey,
  Budget,
  BudgetUsage,
  Membership,
  RateLimit,
  Report,
  ReportRow,
  UseCase,
  UseCaseConsumption,
} from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { UseCaseDetail } from './use-case-detail';

/**
 * The fields an `update:` call carried, as an object.
 *
 * These assertions used to pin the **exact serialised payload**, which broke twice in one session
 * for a reason that was not a defect: a new setting was added and every one of them had to be
 * retyped. What they are about is that the right values are sent — so they say that, and a field
 * they do not mention no longer makes them fail.
 */
function sentUpdate(calls: string[]): Record<string, unknown> {
  const call = calls.find((entry) => entry.startsWith('update:'));
  return call ? JSON.parse(call.slice('update:'.length)) : {};
}

function reportRow(over: Partial<ReportRow> = {}): ReportRow {
  return {
    key: 'demo-uc',
    requests: 0,
    prompt_tokens: 0,
    completion_tokens: 0,
    total_tokens: 0,
    cost_nanos: 0,
    cost: '0.00',
    cached_input_tokens: 0,
    unpriced_requests: 0,
    failed_requests: 0,
    avg_latency_ms: null,
    max_latency_ms: null,
    ...over,
  };
}

function emptyReport(over: Partial<Report> = {}): Report {
  return {
    from: '2026-08-01',
    to: '2026-09-01',
    scope: 'use_cases',
    totals: reportRow(),
    by_use_case: [],
    by_model: [],
    by_member: [],
    by_outcome: [],
    in_scope: true,
    ...over,
  };
}

const USE_CASE: UseCase = {
  slug: 'demo-uc',
  name: 'Demo',
  description: 'A demo use case',
  processing_notes: '',
  retention_days: 7,
  store_payloads: true,
  // What the server says this caller may do here. An administrator, for most of these cases;
  // the read-only ones say so themselves.
  permissions: { can_admin: true, can_manage: true, is_member: true },
};

/** Only the members the tests touch; the rest of the surface is stubbed with empty results. */
interface Overrides {
  get?: Observable<UseCase>;
  update?: Observable<UseCase>;
  members?: Observable<Membership[]>;
  apiKeys?: Observable<ApiKey[]>;
  budgets?: Observable<Budget[]>;
  budgetUsage?: Observable<{ usage: BudgetUsage[] }>;
  useCaseReport?: Observable<Report>;
  useCaseReportPerCall?: () => Observable<Report>;
  addMember?: Observable<Membership>;
  removeMember?: Observable<void>;
  issueApiKey?: Observable<unknown>;
  models?: Observable<unknown>;
  revokeApiKey?: Observable<void>;
  createBudget?: Observable<Budget>;
  deleteBudget?: Observable<void>;
  rateLimits?: Observable<RateLimit[]>;
  createRateLimit?: Observable<RateLimit>;
  deleteRateLimit?: Observable<void>;
}

interface Detail {
  slug: string;
  tab: () => string;
  selectTab: (tab: string) => void;
  loading: () => boolean;
  feedback: { error: () => string | null; notice: () => string | null; busy: () => boolean };
  usageUnavailable: () => boolean;
  usageRefused: () => boolean;
  consumption: () => UseCaseConsumption;
  retentionDays: { set: (v: number | null) => void; (): number | null };
  storePayloads: { set: (v: boolean) => void; (): boolean };
  retentionError: () => string | null;
  retentionChanged: () => boolean;
  restrictMembers: { set: (v: boolean) => void; (): boolean };
  promptCaching: { set: (v: boolean) => void; (): boolean };
  cacheTtl: { set: (v: string) => void; (): string };
  canManage: () => boolean;
  isMember: () => boolean;
  canSaveRetention: () => boolean;
  saveRetention: () => void;
  useCase: () => UseCase | null;
  showAddMember: { set: (v: boolean) => void; (): boolean };
  showIssueKey: { set: (v: boolean) => void; (): boolean };
  showAddBudget: { set: (v: boolean) => void; (): boolean };
  memberUsername: { set: (v: string) => void; (): string };
  memberRole: { set: (v: string) => void; (): string };
  keyLabel: { set: (v: string) => void; (): string };
  keyExpiresInDays: { set: (v: string) => void; (): string };
  configCopied: { set: (v: boolean) => void; (): boolean };
  openCodeConfig: (issued: unknown) => string;
  copyOpenCodeConfig: (issued: unknown) => void;
  budgetScope: { set: (v: 'use_case' | 'member') => void; (): string };
  budgetSubject: { set: (v: string) => void; (): string };
  budgetTokens: { set: (v: number | null) => void; (): number | null };
  budgetCost: { set: (v: string) => void; (): string };
  costPct: (b: Budget) => number;
  unpricedRequests: () => number;
  budgetRequests: { set: (v: number | null) => void; (): number | null };
  canAddMember: () => boolean;
  canAddBudget: () => boolean;
  budgetError: () => string | null;
  addMember: () => void;
  removeMember: (username: string) => void;
  issueKey: () => void;
  revokeKey: (prefix: string) => void;
  addBudget: () => void;
  removeBudget: (id: number | undefined) => void;
  copyKey: (value: string) => void;
  copied: () => boolean;
  copyFailed: () => boolean;
  issued: () => { api_key: string } | null;
  dismissIssued: () => void;
  usedFor: (b: Budget) => BudgetUsage;
  pct: (used: number, limit: number | null | undefined) => number;
  budgetLabel: (b: Budget) => string;
}

function setup(overrides: Overrides = {}, confirmAnswer = true, queryTab: string | null = null) {
  // Several tests build two components (accepted vs. declined confirmation) in one case.
  TestBed.resetTestingModule();
  const calls: string[] = [];
  const reportCalls: string[] = [];
  const service = {
    // Each panel loads its own; the parent only opens it. Stubbed here so the walkthrough below
    // can visit all eight — which is the point of it.
    useCaseRules: () => of([]),
    groupGrants: () => of([]),
    anomalies: () => of({ events: [], next_cursor: null, scope: 'use_cases' }),
    suspensions: () => of({ suspensions: [] }),
    traces: () => of({ traces: [], next_cursor: null, scope: 'use_cases' }),
    get: () => overrides.get ?? of(USE_CASE),
    update: (_s: string, changes: Partial<UseCase>) => {
      calls.push(`update:${JSON.stringify(changes)}`);
      return overrides.update ?? of({ ...USE_CASE, ...changes });
    },
    members: () => overrides.members ?? of([]),
    apiKeys: () => overrides.apiKeys ?? of([]),
    budgets: () => overrides.budgets ?? of([]),
    budgetUsage: () => overrides.budgetUsage ?? of({ usage: [] }),
    // Kept out of `calls`, which this spec uses for **mutations**: a load that appeared there
    // would make every 'nothing was sent' assertion fail for a reason that is not a change.
    useCaseReport: (slug: string, from: string, to: string) => {
      reportCalls.push(`${slug}:${from}:${to}`);
      if (overrides.useCaseReportPerCall) return overrides.useCaseReportPerCall();
      return overrides.useCaseReport ?? of(emptyReport());
    },
    addMember: (_s: string, username: string) => {
      calls.push(`addMember:${username}`);
      return overrides.addMember ?? of({ username, role: 'user' });
    },
    removeMember: (_s: string, username: string) => {
      calls.push(`removeMember:${username}`);
      return overrides.removeMember ?? of(undefined as unknown as void);
    },
    // The catalog, read on init so the OpenCode config can name the models that actually declare
    // tool calling. Present here because the component legitimately calls it — a harness missing a
    // method the component uses is the third stand-in today to fail for that reason.
    models: () =>
      overrides.models ?? of([{ name: 'qwen2.5:3b', capabilities: ['generate', 'tools'] }]),
    issueApiKey: (_s: string, label: string, expiresInDays?: number | null) => {
      calls.push(`issueApiKey:${label}:${expiresInDays ?? 'never'}`);
      return (
        overrides.issueApiKey ??
        of({ api_key: 'aira_ab_cd', prefix: 'ab', label, use_case: 'demo-uc' })
      );
    },
    revokeApiKey: (_s: string, prefix: string) => {
      calls.push(`revokeApiKey:${prefix}`);
      return overrides.revokeApiKey ?? of(undefined as unknown as void);
    },
    createBudget: (_s: string, budget: Budget) => {
      calls.push(`createBudget:${budget.scope}:${budget.subject}`);
      return overrides.createBudget ?? of(budget);
    },
    deleteBudget: (_s: string, id: number) => {
      calls.push(`deleteBudget:${id}`);
      return overrides.deleteBudget ?? of(undefined as unknown as void);
    },
    rateLimits: () => overrides.rateLimits ?? of([]),
    createRateLimit: (_s: string, limit: RateLimit) => {
      calls.push(
        `createRateLimit:${limit.scope}:${limit.subject}:${limit.limit_rpm}:${limit.burst}`,
      );
      return overrides.createRateLimit ?? of(limit);
    },
    deleteRateLimit: (_s: string, id: number) => {
      calls.push(`deleteRateLimit:${id}`);
      return overrides.deleteRateLimit ?? of(undefined as unknown as void);
    },
  };

  TestBed.configureTestingModule({
    imports: [UseCaseDetail],
    providers: [
      provideRouter([]),
      {
        provide: ActivatedRoute,
        useValue: {
          snapshot: {
            paramMap: { get: () => 'demo-uc' },
            queryParamMap: { get: () => queryTab },
          },
        },
      },
      { provide: UseCaseService, useValue: service },
      { provide: ConfirmService, useValue: { ask: () => confirmAnswer } },
    ],
  });

  const fixture = TestBed.createComponent(UseCaseDetail);
  fixture.detectChanges();
  return {
    fixture,
    calls,
    reportCalls,
    component: fixture.componentInstance as unknown as Detail,
    text: () => (fixture.nativeElement as HTMLElement).textContent ?? '',
    html: () => fixture.nativeElement as HTMLElement,
  };
}

function httpError(status: number, message?: string) {
  return throwError(() => ({
    status,
    error: message ? { error: { code: 'x', message } } : null,
  }));
}

describe('UseCaseDetail', () => {
  it('renders the use case and its tabs', () => {
    const { text } = setup();
    expect(text()).toContain('Demo');
    expect(text()).toContain('demo-uc');
    expect(text()).toContain('Overview');
    expect(text()).toContain('Budgets');
  });

  it('opens the tab named in the query string', () => {
    const { component, text } = setup({}, true, 'keys');
    expect(component.tab()).toBe('keys');
    expect(text()).toContain('No keys issued yet.');
  });

  it('ignores an unknown tab in the query string', () => {
    expect(setup({}, true, 'nope').component.tab()).toBe('overview');
  });

  it('marks the open tab as selected for assistive technology', () => {
    const { component, fixture, html } = setup();
    component.selectTab('members');
    fixture.detectChanges();
    expect(html().querySelector('#tab-members')?.getAttribute('aria-selected')).toBe('true');
    expect(html().querySelector('#tab-overview')?.getAttribute('aria-selected')).toBe('false');
  });

  it('reports a failed load instead of showing an empty page', () => {
    const { component, text } = setup({ get: httpError(403, 'You may not see this use case.') });
    expect(component.loading()).toBe(false);
    expect(component.feedback.error()).toBe('You may not see this use case.');
    expect(text()).toContain('You may not see this use case.');
  });

  it('reports failures of the secondary loads', () => {
    expect(setup({ members: httpError(500) }).component.feedback.error()).toBe(
      'Could not load the members.',
    );
    expect(setup({ apiKeys: httpError(500) }).component.feedback.error()).toBe(
      'Could not load the API keys.',
    );
    expect(setup({ budgets: httpError(500) }).component.feedback.error()).toBe(
      'Could not load the budgets.',
    );
  });

  // ---- members ---------------------------------------------------------------------

  // ---- API keys --------------------------------------------------------------------

  it('issues a key, reveals it once, and closes the form', () => {
    const { component } = setup();
    component.keyLabel.set('laptop');
    component.showIssueKey.set(true);
    component.issueKey();
    expect(component.issued()).toEqual({
      api_key: 'aira_ab_cd',
      prefix: 'ab',
      label: 'laptop',
      use_case: 'demo-uc',
    });
    expect(component.showIssueKey()).toBe(false);
    expect(component.keyLabel()).toBe('');
  });

  // A key with no end date has to be inventoried; one with a date lapses on its own. Optional,
  // because a default lifetime would break every existing integration at whatever mark it picked
  // — and because the break-glass credential must not be able to expire.

  // The console does not carry its own copy of the policy: omitting the field lets the **server**
  // apply the configured default. A client-side default would be a second definition, wrong the
  // first time an installation changed the setting — and the reader would then be shown a date
  // that is not the one in the database.
  it('sends no lifetime of its own, leaving the server to apply the default', () => {
    const { component, calls } = setup();
    component.keyLabel.set('laptop');
    component.issueKey();
    expect(calls).toContain('issueApiKey:laptop:never');
  });

  it('passes the lifetime the reader typed', () => {
    const { component, calls } = setup();
    component.keyLabel.set('laptop');
    component.keyExpiresInDays.set('30');
    component.issueKey();
    expect(calls).toContain('issueApiKey:laptop:30');
  });

  it('clears the lifetime after issuing, so the next key is a fresh decision', () => {
    const { component } = setup();
    component.keyExpiresInDays.set('30');
    component.issueKey();
    expect(component.keyExpiresInDays()).toBe('');
  });

  it('surfaces the membership rule when issuing is refused', () => {
    const { component, fixture, text } = setup({
      issueApiKey: httpError(403, 'Only members of this use case may issue API keys.'),
    });
    component.issueKey();
    fixture.detectChanges();
    expect(component.feedback.error()).toBe('Only members of this use case may issue API keys.');
    expect(text()).toContain('Only members of this use case may issue API keys.');
    expect(component.issued()).toBeNull();
  });

  it('asks before revoking a key', () => {
    const declined = setup({}, false);
    declined.component.revokeKey('ab12');
    expect(declined.calls).toEqual([]);

    const accepted = setup();
    accepted.component.revokeKey('ab12');
    expect(accepted.calls).toContain('revokeApiKey:ab12');
    expect(accepted.component.feedback.notice()).toBe('The key was revoked.');
  });

  it('confirms a successful clipboard copy', async () => {
    const { component } = setup();
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: () => Promise.resolve() },
      configurable: true,
    });
    component.copyKey('aira_ab_cd');
    await Promise.resolve();
    await Promise.resolve();
    expect(component.copied()).toBe(true);
    expect(component.copyFailed()).toBe(false);
  });

  it('tells the user to copy manually when the clipboard is unavailable', () => {
    const { component } = setup();
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });
    component.copyKey('aira_ab_cd');
    expect(component.copyFailed()).toBe(true);
    expect(component.copied()).toBe(false);
  });

  it('reports a rejected clipboard write', async () => {
    const { component } = setup();
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: () => Promise.reject(new Error('denied')) },
      configurable: true,
    });
    component.copyKey('aira_ab_cd');
    await Promise.resolve();
    await Promise.resolve();
    expect(component.copyFailed()).toBe(true);
  });

  it('clears the revealed key when dismissed', () => {
    const { component } = setup();
    component.issueKey();
    component.dismissIssued();
    expect(component.issued()).toBeNull();
    expect(component.copied()).toBe(false);
  });

  // ---- budgets ---------------------------------------------------------------------
});

describe('UseCaseDetail rendering', () => {
  const MEMBERS: Membership[] = [
    { username: 'alice', role: 'admin' },
    { username: 'bob', role: 'user' },
  ];
  const KEYS: ApiKey[] = [
    { prefix: 'ab12', label: 'laptop', owner: 'alice', is_active: true },
    { prefix: 'cd34', label: '', owner: 'bob', is_active: false, revoked_at: '2026-01-01' },
  ];

  function render(overrides: Overrides, tab: string) {
    const harness = setup(overrides);
    harness.component.selectTab(tab);
    harness.fixture.detectChanges();
    return harness;
  }

  it('distinguishes active from revoked keys', () => {
    const { text, html } = render({ apiKeys: of(KEYS) }, 'keys');
    expect(text()).toContain('aira_ab12_…');
    expect(text()).toContain('active');
    expect(text()).toContain('revoked');
    // A revoked key offers no revoke button.
    expect(html().querySelectorAll('[aria-label^="Revoke key"]').length).toBe(1);
  });

  it('reveals an issued key once, with a copy affordance', () => {
    const harness = render({}, 'keys');
    harness.component.issueKey();
    harness.fixture.detectChanges();
    expect(harness.text()).toContain('shown only once');
    expect(harness.html().querySelector('.secret')?.textContent).toContain('aira_ab_cd');
  });

  it('explains a blocked clipboard next to the key', () => {
    const harness = render({}, 'keys');
    harness.component.issueKey();
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });
    harness.component.copyKey('aira_ab_cd');
    harness.fixture.detectChanges();
    expect(harness.text()).toContain('copy it manually');
  });

  it('renders processing notes on the overview when present', () => {
    const { text } = render(
      {
        get: of({ ...USE_CASE, processing_notes: 'No personal data.' }),
      },
      'overview',
    );
    expect(text()).toContain('No personal data.');
  });
});

describe('UseCaseDetail interactions', () => {
  function click(fixture: { nativeElement: unknown; detectChanges: () => void }, selector: string) {
    const el = (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>(selector);
    expect(el, `no element for ${selector}`).not.toBeNull();
    el?.click();
    fixture.detectChanges();
  }

  it('switches tabs by clicking them', () => {
    const harness = setup();
    click(harness.fixture, '#tab-members');
    expect(harness.component.tab()).toBe('members');
    click(harness.fixture, '#tab-keys');
    expect(harness.component.tab()).toBe('keys');
    click(harness.fixture, '#tab-budgets');
    expect(harness.component.tab()).toBe('budgets');
    click(harness.fixture, '#tab-overview');
    expect(harness.component.tab()).toBe('overview');
  });

  it('removes a member from its row button', () => {
    const harness = setup({ members: of([{ username: 'bob', role: 'user' }]) });
    harness.component.selectTab('members');
    harness.fixture.detectChanges();
    click(harness.fixture, '[aria-label="Remove bob"]');
    expect(harness.calls).toContain('removeMember:bob');
  });

  it('revokes a key from its row button and copies the revealed one', () => {
    const harness = setup({
      apiKeys: of([{ prefix: 'ab12', label: '', owner: 'a', is_active: true }]),
    });
    harness.component.selectTab('keys');
    harness.fixture.detectChanges();
    click(harness.fixture, '[aria-label="Revoke key ab12"]');
    expect(harness.calls).toContain('revokeApiKey:ab12');

    harness.component.issueKey();
    harness.fixture.detectChanges();
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });
    click(harness.fixture, '.callout--warning .btn--primary');
    expect(harness.component.copyFailed()).toBe(true);

    click(harness.fixture, '.callout--warning .btn--ghost');
    expect(harness.component.issued()).toBeNull();
  });
});

describe('UseCaseDetail retention', () => {
  it('shows the period the use case currently keeps payloads for', () => {
    const harness = setup();
    expect(harness.component.retentionDays()).toBe(7);
    expect(harness.text()).toContain('Days of payload retention');
    expect(harness.text()).toContain('Deleted automatically once the period has passed');
  });

  it('does not offer to save an unchanged period', () => {
    const { component } = setup();
    expect(component.retentionChanged()).toBe(false);
    expect(component.canSaveRetention()).toBe(false);
  });

  it('refuses a period outside the allowed range', () => {
    const { component } = setup();
    component.retentionDays.set(0);
    expect(component.retentionError()).toContain('Between 1 and 3650');
    component.retentionDays.set(4000);
    expect(component.retentionError()).toContain('Between 1 and 3650');
    component.retentionDays.set(null);
    expect(component.retentionError()).toContain('Set how many days');
    expect(component.canSaveRetention()).toBe(false);
  });

  it('saves a new period and says what it means', () => {
    const { component, calls } = setup();
    component.retentionDays.set(1);
    expect(component.canSaveRetention()).toBe(true);

    component.saveRetention();
    expect(sentUpdate(calls)).toMatchObject({ store_payloads: true, retention_days: 1 });
    expect(component.feedback.notice()).toContain('kept for 1 day(s)');
    expect(component.useCase()?.retention_days).toBe(1);
  });

  it('reports a refused change instead of appearing to succeed', () => {
    const { component } = setup({
      update: throwError(() => ({
        status: 403,
        error: { error: { message: 'You are not an admin of this use case.' } },
      })),
    });
    component.retentionDays.set(30);
    component.saveRetention();
    expect(component.feedback.error()).toBe('You are not an admin of this use case.');
    expect(component.useCase()?.retention_days).toBe(7);
  });

  it('saves from the form in the DOM', () => {
    const harness = setup();
    harness.component.retentionDays.set(14);
    harness.fixture.detectChanges();

    const html = harness.fixture.nativeElement as HTMLElement;
    expect(html.querySelector('label[for="retention-days"]')).not.toBeNull();
    const forms = html.querySelectorAll('form');
    forms[forms.length - 1].dispatchEvent(new Event('submit'));
    harness.fixture.detectChanges();

    expect(sentUpdate(harness.calls)).toMatchObject({ store_payloads: true, retention_days: 14 });
  });
});

describe('UseCaseDetail payload storage', () => {
  it('does not render the settings before the use case has loaded', () => {
    // Regression: the form was editable while the GET was in flight, so unchecking the box was
    // silently undone by the arriving response — the switch appeared not to work at all.
    const harness = setup({ get: new Observable<UseCase>(() => undefined) });
    const html = harness.fixture.nativeElement as HTMLElement;
    expect(harness.component.loading()).toBe(true);
    expect(html.querySelector('#store-payloads')).toBeNull();
    expect(html.querySelector('#retention-days')).toBeNull();
  });

  it('offers a switch that turns storage off entirely', () => {
    const harness = setup();
    expect(harness.component.storePayloads()).toBe(true);
    expect(harness.text()).toContain('Store prompts and responses');
    expect(harness.fixture.nativeElement.querySelector('#retention-days')).not.toBeNull();
  });

  it('the checkbox in the DOM actually toggles the setting', () => {
    // Regression: a one-way ngModel binding on a checkbox inside an NgForm wrote the old value
    // straight back, so the box sprang back and the switch could not be operated at all —
    // invisible to a test that only sets the signal.
    const harness = setup();
    const box = (harness.fixture.nativeElement as HTMLElement).querySelector<HTMLInputElement>(
      '#store-payloads',
    );
    expect(box).not.toBeNull();
    expect(box!.checked).toBe(true);

    box!.click();
    harness.fixture.detectChanges();

    expect(harness.component.storePayloads()).toBe(false);
    expect(box!.checked).toBe(false);
    expect(harness.fixture.nativeElement.querySelector('#retention-days')).toBeNull();

    box!.click();
    harness.fixture.detectChanges();
    expect(harness.component.storePayloads()).toBe(true);
  });

  it('hides the period and explains the consequence when storage is off', () => {
    const harness = setup();
    harness.component.storePayloads.set(false);
    harness.fixture.detectChanges();

    // Nothing is kept, so there is no period to ask for.
    expect(harness.fixture.nativeElement.querySelector('#retention-days')).toBeNull();
    expect(harness.component.retentionError()).toBeNull();
    expect(harness.text()).toContain('Nothing a caller sends or receives is written');
    expect(harness.text()).toContain('only be traced through the metadata');
  });

  it('saves the switch and says what changed', () => {
    const { component, calls } = setup();
    component.storePayloads.set(false);
    expect(component.retentionChanged()).toBe(true);

    component.saveRetention();
    const sent = sentUpdate(calls);
    expect(sent).toMatchObject({ store_payloads: false });
    // With storage off there is no period to send — the one thing this case is really about.
    expect(sent).not.toHaveProperty('retention_days');
    expect(component.feedback.notice()).toContain('no longer stored');
    expect(component.feedback.notice()).toContain('removed on the next run');
  });

  it('opens the rate-limit tab from the URL', () => {
    expect(setup({}, true, 'rate-limits').component.tab()).toBe('rate-limits');
  });

  it('renders the rate-limit panel and its count without owning either', () => {
    // The parent keeps the counts, because the tab bar shows them before any tab is opened;
    // what the panel owns is the form and the mutations.
    const harness = setup({ rateLimits: of([{ id: 1, scope: 'use_case', limit_rpm: 90 }]) });
    harness.fixture.detectChanges();
    expect(harness.text()).toContain('Rate limits');

    harness.component.selectTab('rate-limits');
    harness.fixture.detectChanges();
    expect(harness.text()).toContain('90');
  });

  it('shows storage as off in the overview tile', () => {
    const harness = setup({ get: of({ ...USE_CASE, store_payloads: false }) });
    harness.fixture.detectChanges();
    expect(harness.text()).toContain('Payload storage');
    expect(harness.text()).toContain('off');
  });
});

describe('UseCaseDetail — what a reader may do', () => {
  /** The same use case, seen by somebody who belongs to it but administers nothing. */
  const asReader = () =>
    setup({
      get: of({
        ...USE_CASE,
        permissions: { can_admin: false, can_manage: false, is_member: true },
      }),
      members: of([{ username: 'ada', role: 'user', created_at: '' }]),
      apiKeys: of([{ prefix: 'abc123', label: 'k', owner: 'ada', is_active: true }]),
    });

  it('offers no action the server would refuse', () => {
    // Reported from the running console: a use-case *user* saw "Add member" and "Remove", used
    // one, and got a 403 from the screen that had just invited the click. An action nobody can
    // carry out reads as a broken system rather than as a boundary.
    const harness = asReader();
    const html = () => harness.fixture.nativeElement as HTMLElement;

    harness.component.selectTab('members');
    harness.fixture.detectChanges();
    expect(html().textContent).toContain('ada');
    expect(html().querySelector('[aria-label="Remove ada"]')).toBeNull();
    // The picker is not there at all — granting is not something a reader can start.
    expect(html().querySelector('[data-testid="access-search"]')).toBeNull();
    // …and it says who does it, instead of leaving a table with no explanation.
    expect(html().querySelector('[data-testid="access-readonly"]')).not.toBeNull();

    harness.component.selectTab('keys');
    harness.fixture.detectChanges();
    expect(html().querySelector('[aria-label="Revoke key abc123"]')).toBeNull();

    harness.component.selectTab('overview');
    harness.fixture.detectChanges();
    expect(html().querySelector('#store-payloads')).toBeNull();
    // The setting is still *reported* — it is exactly the kind of thing a member needs to know.
    expect(html().querySelector('[data-testid="retention-readonly"]')?.textContent).toContain('7');
  });

  it('still lets a member issue a key, because membership is what that needs', () => {
    // The other half. `is_member` and `can_manage` are separate answers on purpose: seeing a use
    // case must never imply acting in it (ADR-0007), and belonging to one must not require
    // administering it.
    const harness = asReader();
    harness.component.selectTab('keys');
    harness.fixture.detectChanges();

    expect((harness.fixture.nativeElement as HTMLElement).textContent).toContain('+ Issue key');
  });

  it('assumes nothing while the answer has not arrived', () => {
    // Defaulting to "yes" would flash every button on screen and then take them away, which is
    // worse than showing them a moment later.
    const harness = setup({ get: of({ ...USE_CASE, permissions: undefined }) });
    harness.component.selectTab('members');
    harness.fixture.detectChanges();

    expect((harness.fixture.nativeElement as HTMLElement).textContent).not.toContain(
      '+ Add member',
    );
  });
});

describe('UseCaseDetail — an oversight role', () => {
  /** Sees every use case (FRD-201) and belongs to none of them (ADR-0007). */
  const asOversight = () =>
    setup({
      get: of({
        ...USE_CASE,
        store_payloads: false,
        permissions: { can_admin: false, can_manage: false, is_member: false },
      }),
    });

  it('is not offered a key, because a key is data-plane access', () => {
    const harness = asOversight();
    harness.component.selectTab('keys');
    harness.fixture.detectChanges();
    const html = harness.fixture.nativeElement as HTMLElement;

    expect(html.textContent).not.toContain('+ Issue key');
    expect(html.querySelector('[data-testid="keys-readonly"]')).not.toBeNull();
  });

  it('is offered a view of the pipeline, not an edit of it', () => {
    const harness = asOversight();
    expect((harness.fixture.nativeElement as HTMLElement).textContent).toContain('View pipeline');
  });

  it('is told storage is off rather than shown a switch it cannot flip', () => {
    // The *other* branch of the read-only statement: with storage off there is no period to
    // report, and reporting "kept for — day(s)" would be worse than saying nothing.
    const harness = asOversight();
    harness.component.selectTab('overview');
    harness.fixture.detectChanges();
    const readonly = (harness.fixture.nativeElement as HTMLElement).querySelector(
      '[data-testid="retention-readonly"]',
    );

    expect(readonly?.textContent).toContain('not stored');
    expect(readonly?.textContent).not.toContain('day(s)');
  });
});

describe('UseCaseDetail — the rules tab', () => {
  it('is deep-linkable, like every other tab', async () => {
    // A tab that cannot be linked to is a tab nobody sends a colleague to — and the security
    // console now links straight at this one.
    const harness = setup({}, true, 'rules');
    await harness.fixture.whenStable();
    harness.fixture.detectChanges();

    expect(harness.component.tab()).toBe('rules');
    expect(harness.html().querySelector('#tab-rules')?.getAttribute('aria-selected')).toBe('true');
    expect(harness.html().querySelector('app-rules-tab')).not.toBeNull();
  });
});

describe('UseCaseDetail — every tab has a panel behind it', () => {
  // A tab that responds with nothing is the `FRD-206` defect in its plainest form, and it is
  // exactly what `FRD-502` shipped: two tabs whose panels failed to construct and rendered
  // nothing, while every unit test passed. This walks all eight.
  const PANELS: [string, string][] = [
    ['overview', '[aria-labelledby="tab-overview"]'],
    ['members', 'app-access-panel'],
    ['keys', 'table'],
    ['budgets', 'app-budgets-tab'],
    ['rate-limits', 'app-rate-limits-tab'],
    ['rules', 'app-rules-tab'],
    ['warnings', 'app-warnings-tab'],
    ['traces', 'app-traces-tab'],
  ];

  for (const [tab, selector] of PANELS) {
    it(`renders something under "${tab}"`, async () => {
      const harness = setup({}, true, tab);
      await harness.fixture.whenStable();
      harness.fixture.detectChanges();

      expect(harness.component.tab()).toBe(tab);
      expect(harness.html().querySelector(selector), `${tab} has no panel`).not.toBeNull();
    });
  }

  it('counts warnings only when there are some', () => {
    // A zero beside a tab reads as a figure worth looking at. An absent badge reads as nothing to
    // see, which is the truth.
    const harness = setup();
    expect(harness.html().querySelector('#tab-warnings .tab__count')).toBeNull();

    (
      harness.component as unknown as { warningCount: { set: (v: number) => void } }
    ).warningCount.set(3);
    harness.fixture.detectChanges();
    expect(harness.html().querySelector('#tab-warnings .tab__count')?.textContent).toContain('3');
  });
});

describe('UseCaseDetail — retention', () => {
  it('offers no save until something actually changed', () => {
    // A Save that is always available teaches nobody whether their edit took: the button looks
    // the same before and after.
    const harness = setup();
    expect(harness.component.canSaveRetention()).toBe(false);

    harness.component.retentionDays.set(30);
    expect(harness.component.canSaveRetention()).toBe(true);
  });

  it('counts switching payload storage off as a change', () => {
    // It is the more consequential of the two settings, and forgetting it here would make the
    // switch look inert.
    const harness = setup();
    harness.component.storePayloads.set(false);

    expect(harness.component.canSaveRetention()).toBe(true);
  });

  it('does nothing when asked to save a change that is not there', () => {
    const harness = setup();
    harness.component.saveRetention();

    expect(harness.calls.filter((call) => call.startsWith('update:'))).toEqual([]);
  });

  it('assumes the safe answer when the server reports no permissions at all', () => {
    // An older server, or a serializer that lost the field: the console must not decide it may
    // do everything. "Absence of information is not permission" (`FRD-114`).
    const harness = setup({ get: of({ slug: 'demo-uc', name: 'Demo' } as unknown as UseCase) });

    expect(harness.component.canManage()).toBe(false);
    expect(harness.component.isMember()).toBe(false);
  });

  // ---- the OpenCode configuration (`FRD-132`) ----------------------------------------------
  //
  // Built at issuance because the plaintext exists for exactly that moment. A configuration
  // offered on any later screen could only carry a placeholder, and a config file with a
  // placeholder in it is one somebody pastes and then debugs for twenty minutes.

  it('builds a configuration carrying the key that was just issued', () => {
    const { component } = setup();
    component.issueKey();

    const config = JSON.parse(component.openCodeConfig(component.issued()!));

    expect(config.provider.aira.options.apiKey).toBe('aira_ab_cd');
    expect(config.provider.aira.options.baseURL).toContain('/gw/v1beta');
  });

  it('names only the models that declare tool calling', () => {
    /** An assistant cannot use a model that answers in prose, so offering one in its config would
     *  be an invitation to the failure `FRD-131` exists to prevent. */
    const { component } = setup();
    component.issueKey();

    const config = JSON.parse(component.openCodeConfig(component.issued()!));

    expect(Object.keys(config.provider.aira.models)).toEqual(['qwen2.5:3b']);
    expect(config.model).toBe('aira/qwen2.5:3b');
  });

  it('falls back to a named model rather than an empty provider', () => {
    /** A catalog that has not loaded, or has no tool-capable model, must still produce a file
     *  somebody can edit — an empty `models` block is a config that fails with no clue why. */
    const { component } = setup({ models: of([]) });
    component.issueKey();

    const config = JSON.parse(component.openCodeConfig(component.issued()!));

    expect(config.model).toContain('aira/');
  });

  it('does not offer a model whose catalog entry declares no capabilities', () => {
    /** `FRD-114`'s rule at the console: **undeclared means unsupported**. Absence of information
     *  is not permission — an entry that says nothing must not be read as saying "tools". */
    const { component } = setup({
      models: of([{ name: 'silent-model' }, { name: 'qwen2.5:3b', capabilities: ['tools'] }]),
    });
    component.issueKey();

    const config = JSON.parse(component.openCodeConfig(component.issued()!));

    expect(Object.keys(config.provider.aira.models)).toEqual(['qwen2.5:3b']);
  });

  it('still issues a key when the catalog cannot be read', () => {
    /** The catalog is a nicety on this form; the key is the point. A failed read must not take the
     *  screen down with it — deliberately silent, and the config falls back to a named model. */
    const { component } = setup({ models: throwError(() => ({ status: 503 })) });
    component.issueKey();

    expect(component.issued()).not.toBeNull();
    expect(component.openCodeConfig(component.issued()!)).toContain('aira/');
  });

  it('says so when the clipboard will not take the configuration', () => {
    /** Outside a secure context there is no clipboard. The file is still downloadable, so the
     *  button must report why rather than appear to have worked — `FRD-206`: an action that
     *  silently does nothing reads as a broken console. */
    const { component } = setup();
    component.issueKey();
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });

    component.copyOpenCodeConfig(component.issued()!);

    expect(component.copyFailed()).toBe(true);
    expect(component.configCopied()).toBe(false);
  });

  it('reports a rejected write of the configuration', async () => {
    const { component } = setup();
    component.issueKey();
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: () => Promise.reject(new Error('denied')) },
      configurable: true,
    });

    component.copyOpenCodeConfig(component.issued()!);
    await Promise.resolve();
    await Promise.resolve();

    expect(component.copyFailed()).toBe(true);
  });

  it('confirms a configuration that reached the clipboard', async () => {
    const { component } = setup();
    component.issueKey();
    let written = '';
    Object.defineProperty(navigator, 'clipboard', {
      value: {
        writeText: (value: string) => {
          written = value;
          return Promise.resolve();
        },
      },
      configurable: true,
    });

    component.copyOpenCodeConfig(component.issued()!);
    await Promise.resolve();

    expect(component.configCopied()).toBe(true);
    expect(written).toContain('aira_ab_cd');
  });

  // ---- who inside the use case sees whose requests (`FRD-505` FR-4) ------------------------

  it('offers its administrator a switch for who sees whose requests', () => {
    const { component } = setup();
    component.selectTab('overview');

    expect(component.restrictMembers()).toBe(false);
    component.restrictMembers.set(true);

    expect(component.retentionChanged()).toBe(true);
  });

  it('sends the restriction with the data-protection settings', () => {
    /** One form, one save. A second button beside it would be a second thing to forget, and the
     *  two settings answer one question together: what is kept, and who may read it. */
    const { component, calls } = setup();
    component.restrictMembers.set(true);
    component.saveRetention();

    expect(calls.some((call) => call.includes('update'))).toBe(true);
  });

  it('forgets the copied state when the key is dismissed', () => {
    const { component } = setup();
    component.issueKey();
    component.configCopied.set(true);
    component.dismissIssued();

    expect(component.configCopied()).toBe(false);
    expect(component.issued()).toBeNull();
  });
});

describe('UseCaseDetail — consumption (FRD-603)', () => {
  /**
   * Two windows, because they answer two questions: the month is the figure somebody reports,
   * the day is the one that says whether something is running away right now.
   *
   * Asserted as the **requests that were made**, not as what ended up on screen: rendering is the
   * panel's test, and a page that asks for one window and shows two would pass a rendering test
   * and still be wrong.
   */
  it('asks the gateway for this month and for today', () => {
    const harness = setup();

    expect(harness.reportCalls.length).toBe(2);
    expect(harness.reportCalls[0]).toMatch(/^demo-uc:\d{4}-\d{2}-01:/);
    const [, todayFrom, todayTo] = harness.reportCalls[1].split(':');
    expect(new Date(todayTo).getTime() - new Date(todayFrom).getTime()).toBe(24 * 3600 * 1000);
  });

  /**
   * A consumption load that fails must not raise the page's banner.
   *
   * `PageFeedback` is one banner per page (`CLAUDE.md` §3), and it is how a *mutation* reports
   * itself. A figure the reader did not ask for failing to arrive is not a page failure — putting
   * it in the banner would say the use case failed to load, and the next thing doubted is
   * everything else on the screen.
   */
  it('reports an unreachable gateway in the panel, not across the page', () => {
    const harness = setup({ useCaseReport: httpError(503) });

    expect(harness.component.consumption().unavailable).toBe(true);
    expect(harness.component.consumption().month).toBeNull();
    expect(harness.component.feedback.error()).toBeNull();
  });

  /** `in_scope: false` is an empty report the caller was not entitled to fill. Its zeroes are not
   * a measurement, and showing them would state that this use case consumed nothing. */
  it('keeps a report it may not see out of the figures rather than showing its zeroes', () => {
    const harness = setup({ useCaseReport: of(emptyReport({ in_scope: false })) });

    expect(harness.component.consumption().outOfScope).toBe(true);
    expect(harness.component.consumption().month).toBeNull();
    expect(harness.component.consumption().unavailable).toBe(false);
  });

  /**
   * Two loads, one flag — and the second one to answer decides what the reader sees.
   *
   * Written after finding it by reading the code back: the month arrives, `unavailable` is set
   * false, then today fails and sets it true, and the panel hides the month figure that had
   * already been fetched. Whichever request finished last won, which is not a rule anybody chose.
   */
  it('keeps a window that arrived when the other one failed', () => {
    let call = 0;
    const harness = setup({
      useCaseReport: undefined,
      useCaseReportPerCall: () => {
        call += 1;
        return call === 1
          ? of(emptyReport({ totals: reportRow({ requests: 42 }) }))
          : httpError(503);
      },
    });

    expect(harness.component.consumption().month?.requests).toBe(42);
    // Not "unavailable": something *is* available, and hiding it would report a partial failure
    // as a total one.
    expect(harness.component.consumption().unavailable).toBe(false);
    expect(harness.component.consumption().partial).toBe(true);
  });

  it('reports nothing arriving at all as unavailable rather than partial', () => {
    const harness = setup({ useCaseReport: httpError(503) });

    expect(harness.component.consumption().unavailable).toBe(true);
    expect(harness.component.consumption().partial).toBe(false);
  });

  it('passes a real figure through', () => {
    const harness = setup({
      useCaseReport: of(emptyReport({ totals: reportRow({ requests: 59, total_tokens: 10664 }) })),
    });

    expect(harness.component.consumption().month?.total_tokens).toBe(10664);
    expect(harness.component.consumption().today?.requests).toBe(59);
  });
});

/**
 * Who answers for a key (`FRD-604`).
 *
 * The console has always recorded the issuer; what it never did was **tell them**. In an agentic
 * use case that is the whole accountability chain — people issue their own keys, hand them to a
 * coding agent, and when one misbehaves IT Security follows the credential back to a person. That
 * only works if the person knew, at the moment they clicked, that it would.
 *
 * Asserted on the rendered DOM at both moments it must appear: before issuing, and beside the
 * plaintext, which is the point at which the key leaves the screen for good.
 */
describe('UseCaseDetail — accountability for a key', () => {
  it('says whose name the key will carry before it is issued', () => {
    const harness = setup();
    harness.component.selectTab('keys');
    harness.component.showIssueKey.set(true);
    harness.fixture.detectChanges();

    const notice = harness.html().querySelector('[data-testid="key-responsibility"]');
    expect(notice?.textContent).toContain('your name');
    // True of a shared credential as well: it claims responsibility for the key, never that its
    // owner wrote the request.
    expect(notice?.textContent).toContain('anything you hand it to');
  });

  it('repeats it beside the plaintext, which is the last moment anybody reads it', () => {
    const harness = setup({
      issueApiKey: of({
        api_key: 'aira_ab12_secret',
        prefix: 'ab12',
        label: '',
        use_case: 'demo-uc',
      }),
    });
    harness.component.selectTab('keys');
    harness.component.showIssueKey.set(true);
    harness.fixture.detectChanges();
    harness.component.issueKey();
    harness.fixture.detectChanges();

    const notice = harness.html().querySelector('[data-testid="key-issued-responsibility"]');
    expect(notice?.textContent).toContain('attributed to your name');
  });
});

describe('UseCaseDetail — the two switches that had no way in', () => {
  /**
   * `tools_enabled` (`FRD-131`) and `prompt_caching_enabled` (`FRD-133`) both existed only in the
   * API. That is `FRD-206`'s defect inverted: not a control that refuses when used — which at
   * least produces a complaint — but a capability with **no way in**, which nobody ever notices
   * because nothing fails. An administrator could not turn either on without curl.
   */
  it('offers both switches to an administrator and sends them', () => {
    const harness = setup();
    harness.fixture.detectChanges();

    const dom = harness.html();
    const caching = dom.querySelector<HTMLInputElement>('#prompt-caching');
    const tools = dom.querySelector<HTMLInputElement>('#tools-enabled');
    expect(caching, 'no way to turn prompt caching on').not.toBeNull();
    expect(tools, 'no way to turn tool calling on').not.toBeNull();

    caching!.checked = true;
    caching!.dispatchEvent(new Event('change'));
    harness.fixture.detectChanges();
    harness.component.saveRetention();

    expect(harness.calls.some((c) => c.includes('"prompt_caching_enabled":true'))).toBe(true);
  });

  it('explains what caching changes, and what it does not', () => {
    /** It changes the **price**, never the answer — which is why a model that cannot do it is
     *  served normally rather than skipped, and the one capability in the vocabulary that is not
     *  a dispatch condition. A reader deciding whether to enable it needs that sentence. */
    const harness = setup();
    harness.fixture.detectChanges();

    (harness.html().querySelector('[data-testid="info-prompt-caching"]') as HTMLElement).click();
    harness.fixture.detectChanges();

    const help = harness.html().querySelector('[data-testid="help-prompt-caching"]');
    expect(help?.textContent).toContain('never the answer');
    expect(help?.textContent).toContain('whole organisation');
  });

  it('says that a declared function is carried and never run', () => {
    /** The sentence somebody deciding on this switch actually needs. "Let the model call
     *  functions" reads like the gateway will run them, and an administrator who believes that
     *  is either refusing a harmless capability or expecting a sandbox that does not exist
     *  (`ADR-0013`). */
    const harness = setup();
    harness.fixture.detectChanges();

    (harness.html().querySelector('[data-testid="info-tools-enabled"]') as HTMLElement).click();
    harness.fixture.detectChanges();

    const help = harness.html().querySelector('[data-testid="help-tools-enabled"]');
    expect(help?.textContent).toContain('never executes');
  });
});

describe('UseCaseDetail — tuning the cache (`FRD-133`)', () => {
  /**
   * The lifetime is the one parameter with a genuine trade-off, and only somebody's own traffic
   * settles it: a write costs about a quarter extra for five minutes and about double for an
   * hour, so the long one pays off only where turns are regularly further apart than five
   * minutes. It is therefore a setting, and it is only worth showing once caching is on.
   */
  it('offers the lifetime only when caching is switched on', () => {
    const harness = setup();
    harness.fixture.detectChanges();

    expect(harness.html().querySelector('#cache-ttl'), 'offered before caching is on').toBeNull();

    harness.component.promptCaching.set(true);
    harness.fixture.detectChanges();

    expect(harness.html().querySelector('#cache-ttl')).not.toBeNull();
  });

  it('sends the chosen lifetime', () => {
    const harness = setup();
    harness.fixture.detectChanges();
    harness.component.promptCaching.set(true);
    harness.component.cacheTtl.set('1h');

    harness.component.saveRetention();

    expect(sentUpdate(harness.calls)).toMatchObject({
      prompt_caching_enabled: true,
      prompt_cache_ttl: '1h',
    });
  });

  it('says what the longer lifetime costs, not just that it is longer', () => {
    /** A selector offering "5 minutes" and "1 hour" with no price attached invites the wrong
     *  choice: longer sounds strictly better, and it is not. */
    const harness = setup();
    harness.component.promptCaching.set(true);
    harness.fixture.detectChanges();

    (harness.html().querySelector('[data-testid="info-cache-ttl"]') as HTMLElement).click();
    harness.fixture.detectChanges();

    const help = harness.html().querySelector('[data-testid="help-cache-ttl"]');
    expect(help?.textContent).toContain('about double');
    expect(harness.html().querySelector('#cache-ttl')?.textContent).toContain('costs about double');
  });
});
