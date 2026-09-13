import { Type } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { ActivatedRoute, provideRouter } from '@angular/router';
import { Observable, of, throwError } from 'rxjs';
import {
  ApiKey,
  Budget,
  BudgetUsage,
  IssuedApiKey,
  Membership,
  RateLimit,
  Report,
  ReportRow,
  UseCase,
  UseCaseConsumption,
} from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { ApiKeysPanel } from './api-keys-panel';
import { CapabilitiesPanel } from './capabilities-panel';
import { DataProtectionPanel } from './data-protection-panel';
import { UseCaseDetail } from './use-case-detail';

type Writable<T> = { set: (v: T) => void; (): T };

/** The fields an `update:` call carried — asserted by value, so a new setting breaks nothing. */
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
  // An administrator, for most of these cases; the read-only ones say so themselves.
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
  kiraModels?: Observable<unknown>;
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
  consumption: () => UseCaseConsumption;
  canManage: () => boolean;
  isMember: () => boolean;
  useCase: () => UseCase | null;
}

/** The panels' members these tests reach through the page. */
interface KeysView {
  issueKey: () => void;
  issued: () => IssuedApiKey | null;
  openCodeConfig: (issued: IssuedApiKey) => string;
}
interface CapabilitiesView {
  promptCaching: Writable<boolean>;
  saveCapabilities: () => void;
}
interface DataProtectionView {
  retentionDays: Writable<number | null>;
  storePayloads: Writable<boolean>;
  saveRetention: () => void;
}

function setup(overrides: Overrides = {}, confirmAnswer = true, queryTab: string | null = null) {
  // Several tests build two components (accepted vs. declined confirmation) in one case.
  TestBed.resetTestingModule();
  const calls: string[] = [];
  const reportCalls: string[] = [];
  let keyLoads = 0;
  const service = {
    // Each panel loads its own; stubbed so the walkthrough below can visit all eight tabs.
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
    apiKeys: () => {
      keyLoads += 1;
      return overrides.apiKeys ?? of([]);
    },
    budgets: () => overrides.budgets ?? of([]),
    budgetUsage: () => overrides.budgetUsage ?? of({ usage: [] }),
    // Kept out of `calls`, which records **mutations**: a load there would break every "nothing
    // was sent" assertion.
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
    // The page reads the catalogue and the KIRA listing on init; a harness missing a method the
    // component calls fails every test in the file at once.
    models: () =>
      overrides.models ?? of([{ name: 'qwen2.5:3b', capabilities: ['generate', 'tools'] }]),
    kiraModels: () =>
      overrides.kiraModels ?? of([{ id: 9001, name: 'qwen2.5:3b', capabilities: ['CHAT'] }]),
    issueApiKey: (
      _s: string,
      label: string,
      expiresInDays?: number | null,
      owner?: string | null,
    ) => {
      calls.push(`issueApiKey:${label}:${expiresInDays ?? 'never'}${owner ? `:for:${owner}` : ''}`);
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
    keyLoads: () => keyLoads,
    component: fixture.componentInstance as unknown as Detail,
    text: () => (fixture.nativeElement as HTMLElement).textContent ?? '',
    html: () => fixture.nativeElement as HTMLElement,
  };
}

type Harness = ReturnType<typeof setup>;

/** A panel as the page rendered and wired it. */
function panel<T>(harness: Harness, type: Type<unknown>): T {
  harness.fixture.detectChanges();
  const found = harness.fixture.debugElement.query(By.directive(type));
  expect(found, `${type.name} is not on the page`).not.toBeNull();
  return found.componentInstance as T;
}

function keysPanel(harness: Harness): KeysView {
  harness.component.selectTab('keys');
  return panel<KeysView>(harness, ApiKeysPanel);
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
});

describe('UseCaseDetail — the keys tab', () => {
  it('reloads the key list it counts after the panel issues a key', () => {
    const harness = setup();
    const before = harness.keyLoads();

    keysPanel(harness).issueKey();

    expect(harness.keyLoads()).toBe(before + 1);
  });

  it('keeps a just-issued key on screen across a tab switch', () => {
    // The plaintext is shown once; leaving the tab must not be the end of it.
    const harness = setup();
    keysPanel(harness).issueKey();
    harness.component.selectTab('overview');
    harness.fixture.detectChanges();
    harness.component.selectTab('keys');
    harness.fixture.detectChanges();

    expect(harness.html().querySelector('.secret')?.textContent).toContain('aira_ab_cd');
  });

  it('surfaces the membership rule when issuing is refused', () => {
    const harness = setup({
      issueApiKey: httpError(403, 'Only members of this use case may issue API keys.'),
    });
    const keys = keysPanel(harness);
    keys.issueKey();
    harness.fixture.detectChanges();
    expect(harness.component.feedback.error()).toBe(
      'Only members of this use case may issue API keys.',
    );
    expect(harness.text()).toContain('Only members of this use case may issue API keys.');
    expect(keys.issued()).toBeNull();
  });

  it('still issues a key when the catalog cannot be read', () => {
    // The catalogue is a nicety on this form; the key is the point, and the config falls back to
    // a named model.
    const harness = setup({ models: throwError(() => ({ status: 503 })) });
    const keys = keysPanel(harness);
    keys.issueKey();

    expect(keys.issued()).not.toBeNull();
    expect(keys.openCodeConfig(keys.issued()!)).toContain('aira/');
  });
});

describe('UseCaseDetail rendering', () => {
  it('shows the processing notes on the overview as text, not as a form', async () => {
    // The value is on screen; how it gets there is `about-panel`'s own test.
    const harness = setup({ get: of({ ...USE_CASE, processing_notes: 'No personal data.' }) });
    harness.component.selectTab('overview');
    await Promise.resolve();
    harness.fixture.detectChanges();

    const shown = harness.html().querySelector('[data-testid="about-processing"]');
    expect(shown?.textContent).toContain('No personal data.');
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
});

describe('UseCaseDetail — the overview settings', () => {
  it('shows the period the use case currently keeps payloads for', () => {
    const harness = setup();
    expect(panel<DataProtectionView>(harness, DataProtectionPanel).retentionDays()).toBe(7);
    expect(harness.text()).toContain('Days of payload retention');
    expect(harness.text()).toContain('Deleted automatically once the period has passed');
  });

  it('saves a new period, says what it means, and takes the saved use case', () => {
    const harness = setup();
    const settings = panel<DataProtectionView>(harness, DataProtectionPanel);
    settings.retentionDays.set(1);

    settings.saveRetention();
    expect(sentUpdate(harness.calls)).toMatchObject({ store_payloads: true, retention_days: 1 });
    expect(harness.component.feedback.notice()).toContain('kept for 1 day(s)');
    expect(harness.component.useCase()?.retention_days).toBe(1);
  });

  it('reports a refused change instead of appearing to succeed', () => {
    const harness = setup({
      update: throwError(() => ({
        status: 403,
        error: { error: { message: 'You are not an admin of this use case.' } },
      })),
    });
    const settings = panel<DataProtectionView>(harness, DataProtectionPanel);
    settings.retentionDays.set(30);
    settings.saveRetention();
    expect(harness.component.feedback.error()).toBe('You are not an admin of this use case.');
    expect(harness.component.useCase()?.retention_days).toBe(7);
  });

  it('keeps an unsaved change on the overview across a tab switch', async () => {
    // A look at another tab must not cost a half-made change to either settings form.
    const harness = setup();
    // An ngModel inside a form registers with it on a microtask; typing before that goes nowhere.
    await harness.fixture.whenStable();
    await Promise.resolve();
    const dom = harness.html;
    dom().querySelector<HTMLInputElement>('#prompt-caching')!.click();
    const days = dom().querySelector<HTMLInputElement>('#retention-days')!;
    days.value = '30';
    days.dispatchEvent(new Event('input'));
    harness.fixture.detectChanges();

    harness.component.selectTab('members');
    harness.fixture.detectChanges();
    expect(dom().querySelector('#prompt-caching')).toBeNull();
    expect(dom().querySelector('#retention-days')).toBeNull();

    harness.component.selectTab('overview');
    harness.fixture.detectChanges();
    await harness.fixture.whenStable();
    await Promise.resolve();
    harness.fixture.detectChanges();

    expect(dom().querySelector<HTMLInputElement>('#prompt-caching')?.checked).toBe(true);
    expect(dom().querySelector<HTMLInputElement>('#retention-days')?.value).toBe('30');
    expect(harness.calls.filter((call) => call.startsWith('update:'))).toEqual([]);
  });

  it('does not render the settings before the use case has loaded', () => {
    // An editable form shown while the GET is in flight is silently undone by its response.
    const harness = setup({ get: new Observable<UseCase>(() => undefined) });
    expect(harness.component.loading()).toBe(true);
    expect(harness.html().querySelector('#store-payloads')).toBeNull();
    expect(harness.html().querySelector('#retention-days')).toBeNull();
  });

  it('shows storage as off in the overview tile', () => {
    const harness = setup({ get: of({ ...USE_CASE, store_payloads: false }) });
    harness.fixture.detectChanges();
    expect(harness.text()).toContain('Payload storage');
    expect(harness.text()).toContain('off');
  });

  it('keeps the payload switch and the period it applies to next to each other', () => {
    // Nothing else — in particular no capability switch — may come between the two a reader
    // treats as one setting.
    const harness = setup();
    panel<DataProtectionView>(harness, DataProtectionPanel).storePayloads.set(true);
    harness.fixture.detectChanges();

    const form = harness.html().querySelector('form:has(#store-payloads)')!;
    const controls = [...form.querySelectorAll('input, select, textarea')].map((c) => c.id);
    const between = controls.slice(
      controls.indexOf('store-payloads') + 1,
      controls.indexOf('retention-days'),
    );

    expect(controls).toContain('retention-days');
    expect(between, `${between.join(', ')} sits between the switch and its period`).toEqual([]);
  });

  it('does not claim a saving when caching is switched off', () => {
    // After switching caching off, a message about the prefix it keeps would be about the wrong
    // thing.
    const harness = setup({ get: of({ ...USE_CASE, prompt_caching_enabled: true }) });
    const capabilities = panel<CapabilitiesView>(harness, CapabilitiesPanel);
    capabilities.promptCaching.set(false);

    capabilities.saveCapabilities();
    harness.fixture.detectChanges();

    const banner = harness.html().querySelector('[role="status"]')?.textContent ?? '';
    expect(banner).toContain('charged in full');
    expect(banner).not.toContain('Cached share');
  });

  it('names the lifetime it actually saved, in words', () => {
    // "5m" is a wire value; the two lifetimes differ in price, so the reader is told which.
    const harness = setup();
    const capabilities = panel<CapabilitiesView>(harness, CapabilitiesPanel);
    capabilities.promptCaching.set(true);

    capabilities.saveCapabilities();
    harness.fixture.detectChanges();

    expect(harness.html().querySelector('[role="status"]')?.textContent).toContain('five minutes');
    expect(harness.component.useCase()?.prompt_caching_enabled).toBe(true);
  });

  it('opens the rate-limit tab from the URL', () => {
    expect(setup({}, true, 'rate-limits').component.tab()).toBe('rate-limits');
  });

  it('renders the rate-limit panel and its count without owning either', () => {
    // The parent keeps the counts, because the tab bar shows them before any tab is opened.
    const harness = setup({ rateLimits: of([{ id: 1, scope: 'use_case', limit_rpm: 90 }]) });
    harness.fixture.detectChanges();
    expect(harness.text()).toContain('Rate limits');

    harness.component.selectTab('rate-limits');
    harness.fixture.detectChanges();
    expect(harness.text()).toContain('90');
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
    // An action nobody can carry out reads as a broken system rather than as a boundary.
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
    // Seeing a use case never implies acting in it (ADR-0007), and belonging to one does not
    // require administering it.
    const harness = asReader();
    harness.component.selectTab('keys');
    harness.fixture.detectChanges();

    expect((harness.fixture.nativeElement as HTMLElement).textContent).toContain('+ Issue key');
  });

  it('assumes nothing while the answer has not arrived', () => {
    // Defaulting to "yes" would flash every button and then take them away.
    const harness = setup({ get: of({ ...USE_CASE, permissions: undefined }) });
    harness.component.selectTab('members');
    harness.fixture.detectChanges();

    expect((harness.fixture.nativeElement as HTMLElement).textContent).not.toContain(
      '+ Add member',
    );
  });

  it('assumes the safe answer when the server reports no permissions at all', () => {
    // Absence of information is not permission (`FRD-114`).
    const harness = setup({ get: of({ slug: 'demo-uc', name: 'Demo' } as unknown as UseCase) });

    expect(harness.component.canManage()).toBe(false);
    expect(harness.component.isMember()).toBe(false);
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
    // With storage off there is no period to report; "kept for — day(s)" would be worse than
    // saying nothing.
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
    const harness = setup({}, true, 'rules');
    await harness.fixture.whenStable();
    harness.fixture.detectChanges();

    expect(harness.component.tab()).toBe('rules');
    expect(harness.html().querySelector('#tab-rules')?.getAttribute('aria-selected')).toBe('true');
    expect(harness.html().querySelector('app-rules-tab')).not.toBeNull();
  });
});

describe('UseCaseDetail — every tab has a panel behind it', () => {
  // A tab that renders nothing is the `FRD-206` defect in its plainest form, and unit tests of
  // the panels alone do not see it. This walks all eight.
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
    // A zero beside a tab reads as a figure worth looking at; an absent badge reads as nothing.
    const harness = setup();
    expect(harness.html().querySelector('#tab-warnings .tab__count')).toBeNull();

    (
      harness.component as unknown as { warningCount: { set: (v: number) => void } }
    ).warningCount.set(3);
    harness.fixture.detectChanges();
    expect(harness.html().querySelector('#tab-warnings .tab__count')?.textContent).toContain('3');
  });
});

describe('UseCaseDetail — consumption (FRD-603)', () => {
  // Asserted as the requests that were made: rendering is the panel's test, and a page that asks
  // for one window and shows two would pass it.
  it('asks the gateway for this month and for today', () => {
    const harness = setup();

    expect(harness.reportCalls.length).toBe(2);
    expect(harness.reportCalls[0]).toMatch(/^demo-uc:\d{4}-\d{2}-01:/);
    const [, todayFrom, todayTo] = harness.reportCalls[1].split(':');
    expect(new Date(todayTo).getTime() - new Date(todayFrom).getTime()).toBe(24 * 3600 * 1000);
  });

  // A figure the reader did not ask for failing to arrive is not a page failure, so it stays out
  // of the page's one banner (`CLAUDE.md` §3).
  it('reports an unreachable gateway in the panel, not across the page', () => {
    const harness = setup({ useCaseReport: httpError(503) });

    expect(harness.component.consumption().unavailable).toBe(true);
    expect(harness.component.consumption().month).toBeNull();
    expect(harness.component.feedback.error()).toBeNull();
  });

  // `in_scope: false` is an empty report the caller was not entitled to fill, not a measurement.
  it('keeps a report it may not see out of the figures rather than showing its zeroes', () => {
    const harness = setup({ useCaseReport: of(emptyReport({ in_scope: false })) });

    expect(harness.component.consumption().outOfScope).toBe(true);
    expect(harness.component.consumption().month).toBeNull();
    expect(harness.component.consumption().unavailable).toBe(false);
  });

  // Two loads: the later one to answer must not hide a window the earlier one delivered.
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
    // Something *is* available: a partial failure, not a total one.
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
