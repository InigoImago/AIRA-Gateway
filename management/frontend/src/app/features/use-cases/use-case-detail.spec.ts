import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, provideRouter } from '@angular/router';
import { Observable, of, throwError } from 'rxjs';
import { ApiKey, Budget, BudgetUsage, Membership, UseCase } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { UseCaseDetail } from './use-case-detail';

const USE_CASE: UseCase = {
  slug: 'demo-uc',
  name: 'Demo',
  description: 'A demo use case',
  processing_notes: '',
};

/** Only the members the tests touch; the rest of the surface is stubbed with empty results. */
interface Overrides {
  get?: Observable<UseCase>;
  members?: Observable<Membership[]>;
  apiKeys?: Observable<ApiKey[]>;
  budgets?: Observable<Budget[]>;
  budgetUsage?: Observable<{ usage: BudgetUsage[] }>;
  addMember?: Observable<Membership>;
  removeMember?: Observable<void>;
  issueApiKey?: Observable<unknown>;
  revokeApiKey?: Observable<void>;
  createBudget?: Observable<Budget>;
  deleteBudget?: Observable<void>;
}

interface Detail {
  slug: string;
  tab: () => string;
  selectTab: (tab: string) => void;
  loading: () => boolean;
  busy: () => boolean;
  error: () => string | null;
  notice: () => string | null;
  usageUnavailable: () => boolean;
  showAddMember: { set: (v: boolean) => void; (): boolean };
  showIssueKey: { set: (v: boolean) => void; (): boolean };
  showAddBudget: { set: (v: boolean) => void; (): boolean };
  memberUsername: { set: (v: string) => void; (): string };
  memberRole: { set: (v: string) => void; (): string };
  keyLabel: { set: (v: string) => void; (): string };
  budgetScope: { set: (v: 'use_case' | 'member') => void; (): string };
  budgetSubject: { set: (v: string) => void; (): string };
  budgetTokens: { set: (v: number | null) => void; (): number | null };
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
  issued: () => unknown;
  dismissIssued: () => void;
  usedFor: (b: Budget) => BudgetUsage;
  pct: (used: number, limit: number | null | undefined) => number;
  budgetLabel: (b: Budget) => string;
}

function setup(overrides: Overrides = {}, confirmAnswer = true, queryTab: string | null = null) {
  // Several tests build two components (accepted vs. declined confirmation) in one case.
  TestBed.resetTestingModule();
  const calls: string[] = [];
  const service = {
    get: () => overrides.get ?? of(USE_CASE),
    members: () => overrides.members ?? of([]),
    apiKeys: () => overrides.apiKeys ?? of([]),
    budgets: () => overrides.budgets ?? of([]),
    budgetUsage: () => overrides.budgetUsage ?? of({ usage: [] }),
    addMember: (_s: string, username: string) => {
      calls.push(`addMember:${username}`);
      return overrides.addMember ?? of({ username, role: 'user' });
    },
    removeMember: (_s: string, username: string) => {
      calls.push(`removeMember:${username}`);
      return overrides.removeMember ?? of(undefined as unknown as void);
    },
    issueApiKey: (_s: string, label: string) => {
      calls.push(`issueApiKey:${label}`);
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
    expect(component.error()).toBe('You may not see this use case.');
    expect(text()).toContain('You may not see this use case.');
  });

  it('reports failures of the secondary loads', () => {
    expect(setup({ members: httpError(500) }).component.error()).toBe(
      'Could not load the members.',
    );
    expect(setup({ apiKeys: httpError(500) }).component.error()).toBe(
      'Could not load the API keys.',
    );
    expect(setup({ budgets: httpError(500) }).component.error()).toBe(
      'Could not load the budgets.',
    );
  });

  // ---- members ---------------------------------------------------------------------

  it('adds a member and reports it', () => {
    const { component, calls } = setup();
    component.memberUsername.set('  bob  ');
    component.addMember();
    expect(calls).toContain('addMember:bob');
    expect(component.notice()).toBe('bob was added.');
  });

  it('refuses to submit an empty member form', () => {
    const { component, calls } = setup();
    component.memberUsername.set('   ');
    expect(component.canAddMember()).toBe(false);
    component.addMember();
    expect(calls).toEqual([]);
  });

  it('keeps the member form open and shows why the server refused', () => {
    const { component } = setup({ addMember: httpError(400, "Unknown user 'bob'.") });
    component.showAddMember.set(true);
    component.memberUsername.set('bob');
    component.addMember();
    expect(component.error()).toBe("Unknown user 'bob'.");
    expect(component.showAddMember()).toBe(true);
    expect(component.memberUsername()).toBe('bob');
  });

  it('asks before removing a member and does nothing when declined', () => {
    const { component, calls } = setup({}, false);
    component.removeMember('bob');
    expect(calls).toEqual([]);
  });

  it('removes a member once confirmed', () => {
    const { component, calls } = setup();
    component.removeMember('bob');
    expect(calls).toContain('removeMember:bob');
    expect(component.notice()).toBe('bob was removed.');
  });

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

  it('surfaces the membership rule when issuing is refused', () => {
    const { component, fixture, text } = setup({
      issueApiKey: httpError(403, 'Only members of this use case may issue API keys.'),
    });
    component.issueKey();
    fixture.detectChanges();
    expect(component.error()).toBe('Only members of this use case may issue API keys.');
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
    expect(accepted.component.notice()).toBe('The key was revoked.');
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

  it('requires a username for a member budget', () => {
    const { component, calls } = setup();
    component.budgetScope.set('member');
    component.budgetSubject.set('  ');
    component.budgetTokens.set(100);
    expect(component.budgetError()).toBe('A member budget needs a username.');
    expect(component.canAddBudget()).toBe(false);
    component.addBudget();
    expect(calls).toEqual([]);
  });

  it('requires at least one limit', () => {
    const { component } = setup();
    component.budgetScope.set('use_case');
    expect(component.budgetError()).toBe('Set a token limit, a request limit, or both.');
  });

  it('creates a valid budget and resets the form', () => {
    const { component, calls } = setup();
    component.budgetScope.set('member');
    component.budgetSubject.set(' bob ');
    component.budgetTokens.set(1000);
    component.addBudget();
    expect(calls).toContain('createBudget:member:bob');
    expect(component.notice()).toBe('Budget saved.');
    expect(component.budgetSubject()).toBe('');
    expect(component.budgetTokens()).toBeNull();
  });

  it('asks before removing a budget and ignores a missing id', () => {
    const declined = setup({}, false);
    declined.component.removeBudget(1);
    expect(declined.calls).toEqual([]);

    const missing = setup();
    missing.component.removeBudget(undefined);
    expect(missing.calls).toEqual([]);

    const accepted = setup();
    accepted.component.removeBudget(7);
    expect(accepted.calls).toContain('deleteBudget:7');
  });

  it('renders consumption against the configured limits', () => {
    const { text, component, fixture } = setup({
      budgets: of([
        { id: 1, scope: 'use_case', period: 'month', limit_tokens: 1000, limit_requests: 10 },
      ]),
      budgetUsage: of({ usage: [{ id: 1, used_tokens: 900, used_requests: 5 }] }),
    });
    component.selectTab('budgets');
    fixture.detectChanges();
    expect(text()).toContain('900 / 1000');
    expect(text()).toContain('5 / 10');
  });

  it('keeps showing the limits when the gateway cannot supply consumption', () => {
    const { component, text, fixture } = setup({
      budgets: of([{ id: 1, scope: 'use_case', period: 'month', limit_tokens: 1000 }]),
      budgetUsage: throwError(() => ({ status: 0 })),
    });
    component.selectTab('budgets');
    fixture.detectChanges();
    expect(component.usageUnavailable()).toBe(true);
    expect(text()).toContain('Consumption is unavailable');
    expect(text()).toContain('0 / 1000');
  });

  it('computes usage percentages defensively', () => {
    const { component } = setup();
    expect(component.pct(5, 10)).toBe(50);
    expect(component.pct(50, 10)).toBe(100); // never overflows the bar
    expect(component.pct(5, null)).toBe(0);
    expect(component.pct(5, 0)).toBe(0);
    expect(component.usedFor({ scope: 'use_case', period: 'day' })).toEqual({
      id: 0,
      used_tokens: 0,
      used_requests: 0,
    });
  });

  it('labels budgets by their scope', () => {
    const { component } = setup();
    expect(component.budgetLabel({ scope: 'use_case', period: 'day' })).toBe('Whole use case');
    expect(component.budgetLabel({ scope: 'member', subject: 'bob', period: 'day' })).toBe('bob');
    expect(component.budgetLabel({ scope: 'member', period: 'day' })).toBe('member');
  });

  it('blocks concurrent mutations while one is in flight', () => {
    const { component } = setup({ addMember: new Observable<Membership>(() => undefined) });
    component.memberUsername.set('bob');
    component.addMember();
    expect(component.busy()).toBe(true);
    expect(component.canAddMember()).toBe(false);
    expect(component.canAddBudget()).toBe(false);
  });
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

  it('renders the members table with roles and remove actions', () => {
    const { text, html } = render({ members: of(MEMBERS) }, 'members');
    expect(text()).toContain('alice');
    expect(text()).toContain('bob');
    expect(html().querySelectorAll('tbody tr').length).toBe(2);
    expect(html().querySelector('[aria-label="Remove alice"]')).not.toBeNull();
    // Wide tables scroll inside their card instead of widening the page.
    expect(html().querySelector('.table-wrap')).not.toBeNull();
  });

  it('renders the add-member form only once opened', () => {
    const harness = render({}, 'members');
    expect(harness.html().querySelector('#member-user')).toBeNull();

    harness.component.showAddMember.set(true);
    harness.fixture.detectChanges();
    const input = harness.html().querySelector('#member-user');
    expect(input).not.toBeNull();
    // Every control is reachable by its label.
    expect(harness.html().querySelector('label[for="member-user"]')).not.toBeNull();
    expect(harness.html().querySelector('label[for="member-role"]')).not.toBeNull();
  });

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

  it('renders both budget bars with their warning states', () => {
    const { html } = render(
      {
        budgets: of([
          { id: 1, scope: 'use_case', period: 'month', limit_tokens: 1000, limit_requests: 10 },
        ]),
        budgetUsage: of({ usage: [{ id: 1, used_tokens: 900, used_requests: 10 }] }),
      },
      'budgets',
    );
    const bars = html().querySelectorAll('.progress__bar');
    expect(bars.length).toBe(2);
    expect(bars[0].classList.contains('is-full')).toBe(true); // requests at 100%
    expect(bars[1].classList.contains('is-warn')).toBe(true); // tokens at 90%
    expect(html().querySelectorAll('[role="progressbar"]').length).toBe(2);
  });

  it('shows the member-username field only for a member-scoped budget', () => {
    const harness = render({}, 'budgets');
    harness.component.showAddBudget.set(true);
    harness.fixture.detectChanges();
    expect(harness.html().querySelector('#budget-subject')).toBeNull();

    harness.component.budgetScope.set('member');
    harness.fixture.detectChanges();
    expect(harness.html().querySelector('#budget-subject')).not.toBeNull();
    expect(harness.text()).toContain('A member budget needs a username.');
  });

  it('says what an empty budget list means', () => {
    expect(render({}, 'budgets').text()).toContain('requests are unlimited');
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

  it('toggles the disclosure forms from their buttons', () => {
    const harness = setup();
    harness.component.selectTab('members');
    harness.fixture.detectChanges();

    click(harness.fixture, '[aria-expanded="false"]');
    expect(harness.component.showAddMember()).toBe(true);
    expect(harness.fixture.nativeElement.querySelector('#member-user')).not.toBeNull();

    click(harness.fixture, '[aria-expanded="true"]');
    expect(harness.component.showAddMember()).toBe(false);
  });

  it('submits the member form on submit', () => {
    const harness = setup();
    harness.component.selectTab('members');
    harness.component.showAddMember.set(true);
    harness.component.memberUsername.set('bob');
    harness.fixture.detectChanges();

    const form = (harness.fixture.nativeElement as HTMLElement).querySelector('form');
    form?.dispatchEvent(new Event('submit'));
    harness.fixture.detectChanges();
    expect(harness.calls).toContain('addMember:bob');
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

  it('removes a budget from its card button', () => {
    const harness = setup({
      budgets: of([{ id: 3, scope: 'use_case', period: 'day', limit_requests: 5 }]),
    });
    harness.component.selectTab('budgets');
    harness.fixture.detectChanges();
    click(harness.fixture, '[aria-label="Remove budget for Whole use case"]');
    expect(harness.calls).toContain('deleteBudget:3');
  });
});
