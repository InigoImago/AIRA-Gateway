import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Observable, defer, of, throwError } from 'rxjs';
import { Budget, Me } from '../../core/api/models';
import { MeService } from '../../core/api/me.service';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { PageFeedback } from '../../core/ui/page-feedback';
import { InstallationBudgetCard } from './installation-budget-card';

interface Card {
  canManage: () => boolean;
  retryIfFailed: () => void;
  limitsOf: (b: Budget) => string[];
  setEnabled: (b: Budget, enabled: boolean) => void;
  remove: (id: number | undefined) => void;
  canSave: () => boolean;
  period: { set: (v: 'day' | 'month') => void };
  cost: { set: (v: string) => void; (): string };
  tokens: { set: (v: number | null) => void; (): number | null };
  requests: { set: (v: number | null) => void; (): number | null };
  showForm: { set: (v: boolean) => void; (): boolean };
  validationError: () => string | null;
  save: () => void;
  feedback: PageFeedback;
}

const httpError = (status: number) =>
  throwError(() => ({ status, error: { error: { message: 'refused' } } }));

interface Options {
  /** What `/me` lists. Absent means the permission to set the budget. */
  permissions?: string[];
  roles?: string[];
  budgets?: Budget[];
  list?: Observable<Budget[]>;
  save?: Observable<Budget>;
  remove?: Observable<void>;
  confirmAnswer?: boolean;
  me?: Observable<Me>;
  /** What the page's report said about enforcement; `undefined` means the gateway has not said. */
  enforced?: boolean | null;
  /** `AIRA_CURRENCY` as `/v1/me` reported it; empty until it has. */
  currency?: string;
}

function setup(options: Options = {}) {
  TestBed.resetTestingModule();
  const calls: string[] = [];
  const rows = options.budgets ?? [];
  const service = {
    installationBudgets: () => {
      calls.push('list');
      return options.list ?? of(rows);
    },
    saveInstallationBudget: (budget: Budget) => {
      calls.push(
        `save:${budget.scope}:${budget.period}:${budget.limit_cost}:${budget.limit_tokens}:${budget.limit_requests}:${budget.enabled}`,
      );
      return options.save ?? of(budget);
    },
    deleteInstallationBudget: (id: number) => {
      calls.push(`delete:${id}`);
      return options.remove ?? of(undefined as unknown as void);
    },
  };
  const me = {
    // The double carries every member the real service has; a less capable one fails as a
    // template error in every test at once.
    currency: signal(options.currency ?? ''),
    get: () =>
      options.me ??
      of({
        subject: 's',
        username: 'u',
        email: 'u@example.test',
        roles: options.roles ?? ['global-admin'],
        permissions: options.permissions ?? ['budget.installation.write'],
        use_cases: [],
      } as Me),
  };

  TestBed.configureTestingModule({
    imports: [InstallationBudgetCard],
    providers: [
      PageFeedback,
      { provide: UseCaseService, useValue: service },
      { provide: MeService, useValue: me },
      { provide: ConfirmService, useValue: { ask: () => options.confirmAnswer ?? true } },
    ],
  });

  const fixture = TestBed.createComponent(InstallationBudgetCard);
  // The page passes what its report carried. `null` is the honest default: not answered.
  fixture.componentRef.setInput('enforced', options.enforced ?? null);
  fixture.detectChanges();

  return {
    fixture,
    calls,
    component: fixture.componentInstance as unknown as Card,
    feedback: TestBed.inject(PageFeedback),
    text: () => (fixture.nativeElement as HTMLElement).textContent ?? '',
    click: (testid: string) => {
      (fixture.nativeElement as HTMLElement)
        .querySelector<HTMLButtonElement>(`[data-testid="${testid}"]`)
        ?.click();
      fixture.detectChanges();
    },
    testid: (id: string) =>
      (fixture.nativeElement as HTMLElement).querySelector(`[data-testid="${id}"]`),
  };
}

const MONTHLY: Budget = {
  id: 7,
  scope: 'installation',
  subject: '',
  period: 'month',
  limit_cost: '20.000000',
  limit_tokens: null,
  limit_requests: null,
  enabled: true,
};

describe('InstallationBudgetCard', () => {
  it('shows an administrator that nothing is capped, which is not the same as zero spend', () => {
    const { testid, text } = setup({ budgets: [] });

    expect(testid('no-installation-budget')).not.toBeNull();
    expect(text()).toContain('nothing stops it');
    expect(testid('add-installation-budget')).not.toBeNull();
  });

  it('draws nothing at all for a reader the server answered with an empty list', () => {
    // The server answers `[]` both to "nothing is configured" and to "not your business", so the
    // card must not claim the first.
    const { testid, text } = setup({ roles: [], permissions: [], budgets: [] });

    expect(testid('installation-budget')).toBeNull();
    expect(text()).toBe('');
  });

  it('shows a governance reader the limit and offers no way to change it', () => {
    // `ADR-0007`: reading every figure is not setting one.
    const { testid, text } = setup({
      permissions: ['report.read_all'],
      budgets: [MONTHLY],
      currency: 'CHF',
    });

    expect(testid('installation-budget')).not.toBeNull();
    expect(text()).toContain('20.00 CHF');
    expect(testid('add-installation-budget')).toBeNull();
    expect(testid('toggle-installation-budget-7')).toBeNull();
    expect(testid('remove-installation-budget-7')).toBeNull();
  });

  it('asks the permission, never the role', () => {
    // The server decides what a role holds, so the role alone offers nothing…
    const roleOnly = setup({ roles: ['global-admin'], permissions: [], budgets: [MONTHLY] });
    expect(roleOnly.testid('add-installation-budget')).toBeNull();
    expect(roleOnly.testid('toggle-installation-budget-7')).toBeNull();
    expect(roleOnly.testid('remove-installation-budget-7')).toBeNull();

    // …and the permission alone is enough.
    const permissionOnly = setup({
      roles: [],
      permissions: ['budget.installation.write'],
      budgets: [MONTHLY],
    });
    expect(permissionOnly.testid('add-installation-budget')).not.toBeNull();
    expect(permissionOnly.testid('toggle-installation-budget-7')).not.toBeNull();
    expect(permissionOnly.testid('remove-installation-budget-7')).not.toBeNull();
  });

  it('requires at least one limit', () => {
    expect(setup().component.validationError()).toBe(
      'Set a spend limit, a token limit, or a request limit.',
    );
  });

  it('refuses a spend limit that is not an amount', () => {
    const { component } = setup();
    component.cost.set('viel');
    expect(component.validationError()).toContain('must be an amount');
  });

  it('saves the scope the gateway binds on, and says enabled out loud', () => {
    // `scope: 'installation'` with an empty use case selects the residual bucket in the gateway;
    // an upsert silent about `enabled` would re-arm a budget somebody had lifted.
    const { component, calls } = setup();
    component.cost.set('20,00');
    component.save();

    expect(calls).toContain('save:installation:month:20.00:null:null:true');
  });

  it('clears the form and reloads after a save', () => {
    const { component, calls, feedback } = setup();
    component.requests.set(50);
    component.showForm.set(true);
    component.save();

    expect(feedback.notice()).toBe('Installation budget saved.');
    expect(component.cost()).toBe('');
    expect(component.requests()).toBeNull();
    expect(component.showForm()).toBe(false);
    expect(calls.filter((c) => c === 'list').length).toBe(2);
  });

  it('reports a refused save in the backend’s own words', () => {
    const { component, feedback } = setup({ save: httpError(403) });
    component.tokens.set(1000);
    component.save();

    expect(feedback.error()).toContain('refused');
  });

  it('reports a failed load rather than showing an empty card', () => {
    const { feedback, testid } = setup({ list: httpError(502) });

    expect(feedback.error()).toContain('refused');
    // Not "no limit is set": the load never answered, so nothing is known.
    expect(testid('no-installation-budget')).toBeNull();
  });

  it('asks again when the page asks, only after a load that failed', () => {
    // The page clears its one banner for each new period; the card's failed load was said only
    // there, so the card is asked again rather than left blank — and a good answer is not re-asked.
    let asked = 0;
    const { component, calls, fixture, text } = setup({
      list: defer(() => (++asked === 1 ? httpError(502) : of([MONTHLY]))),
      currency: 'EUR',
    });
    expect(text()).not.toContain('20.00');

    component.retryIfFailed();
    fixture.detectChanges();
    expect(text()).toContain('20.00 EUR');

    component.retryIfFailed();
    expect(calls.filter((c) => c === 'list').length).toBe(2);
  });

  it('lifts a limit by sending the whole row back', () => {
    // The endpoint upserts on the period, so a body carrying only the switch would blank the
    // limits beside it.
    const { click, calls } = setup({ budgets: [MONTHLY] });

    click('toggle-installation-budget-7');

    expect(calls).toContain('save:installation:month:20.000000:null:null:false');
  });

  it('removes a limit only after the question is answered', () => {
    const { click, calls } = setup({ budgets: [MONTHLY], confirmAnswer: false });

    click('remove-installation-budget-7');
    expect(calls).not.toContain('delete:7');
  });

  it('removes a limit and reloads', () => {
    const { click, calls, feedback } = setup({ budgets: [MONTHLY] });

    click('remove-installation-budget-7');

    expect(calls).toContain('delete:7');
    expect(feedback.notice()).toBe('Installation budget removed.');
  });

  it('names every limit it shows with its unit', () => {
    // A card showing a bare `50` says nothing: fifty in the currency, fifty tokens and fifty
    // requests are three different limits, and two of them are what binds a model with no price
    // on file.
    const { component } = setup({ currency: 'EUR' });

    expect(
      component.limitsOf({
        ...MONTHLY,
        limit_cost: '5.000000',
        limit_tokens: 1000,
        limit_requests: 50,
      }),
    ).toEqual(['5.00 EUR', '1,000 tokens', '50 request(s)']);
    expect(component.limitsOf({ ...MONTHLY, limit_cost: null })).toEqual([]);
  });

  it('states a spend limit in the installation’s currency, never in a symbol of its own', () => {
    // `$` beside a figure in `AIRA_CURRENCY` is a figure in the wrong currency; the stored
    // `20.000000` is trimmed for a reader on the digits, never through a float.
    const { text, component } = setup({ budgets: [MONTHLY], currency: 'CHF' });

    expect(text()).toContain('20.00 CHF');
    expect(text()).not.toContain('$');
    expect(component.limitsOf({ ...MONTHLY, limit_cost: '20.500000' })).toEqual(['20.50 CHF']);
    expect(component.limitsOf({ ...MONTHLY, limit_cost: '0.000125' })).toEqual(['0.000125 CHF']);
    expect(component.limitsOf({ ...MONTHLY, limit_cost: '300' })).toEqual(['300.00 CHF']);
  });

  it('names a spend limit as spend while the currency is not known yet', () => {
    // Never a bare number: until `/v1/me` has answered there is no unit to state, and the word
    // says which of the three limits this one is.
    const { component } = setup();

    expect(component.limitsOf(MONTHLY)).toEqual(['spend 20.00']);
  });

  it('tells a governance reader that a limit is lifted, since the figure alone would mislead', () => {
    // A budget the gateway skips is a budget that is not in force, and a card showing its numbers
    // without saying so reads as protection that is not there.
    const { text } = setup({
      permissions: ['report.read_all'],
      budgets: [{ ...MONTHLY, enabled: false }],
    });

    expect(text()).toContain('disabled');
  });

  it('does not act for a reader who may not, even if the call is made', () => {
    // The buttons are not rendered for them; this is the second lock, on the method itself. A
    // console that offers nothing but would act if asked is one DOM edit away from acting.
    const { component, calls } = setup({ permissions: ['report.read_all'], budgets: [MONTHLY] });

    component.setEnabled(MONTHLY, false);
    component.remove(MONTHLY.id);

    expect(calls).not.toContain('save:installation:month:20.000000:null:null:false');
    expect(calls).not.toContain('delete:7');
  });

  it('does nothing for a row that has no id yet', () => {
    const { component, calls } = setup();

    component.remove(undefined);

    expect(calls).not.toContain('delete:undefined');
  });

  it('refuses to save while a save is in flight, and while the form is empty', () => {
    const { component, feedback } = setup();

    expect(component.canSave()).toBe(false); // no limit set yet
    component.cost.set('20.00');
    expect(component.canSave()).toBe(true);
    feedback.busy.set(true);
    expect(component.canSave()).toBe(false);
    component.save(); // and the guard holds when it is called anyway
    expect(component.cost()).toBe('20.00');
  });

  it('keeps the figure on screen when the permissions cannot be read', () => {
    // Losing the reader's permissions costs them a form, not a figure — and the page's one banner
    // belongs to the report it is named after, not to this panel's side question.
    const { testid, feedback, text } = setup({ budgets: [MONTHLY], me: httpError(500) });

    expect(testid('installation-budget')).not.toBeNull();
    expect(text()).toContain('20.00');
    expect(feedback.error()).toBeNull();
    expect(testid('add-installation-budget')).toBeNull();
  });
});

/**
 * A limit set here that refuses nothing (`FRD-610` §3.3).
 *
 * This card is where somebody *sets* the installation's cap, which is why the banner belongs on it
 * even with no budget configured: an empty state here is an invitation, and the person accepting it
 * should know what the limit will do.
 */
describe('InstallationBudgetCard — enforcement', () => {
  it('says so when the installation does not enforce budgets', () => {
    const { fixture, text } = setup({ enforced: false });

    expect(
      (fixture.nativeElement as HTMLElement).querySelector(
        '[data-testid="installation-budget-not-enforced"]',
      ),
    ).not.toBeNull();
    expect(text()).toContain('not enforced in this installation');
    expect(text()).toContain('AIRA_ENFORCE_BUDGETS');
  });

  it('says it even before a budget exists, because this is where one is set', () => {
    const { fixture } = setup({ enforced: false, budgets: [] });

    expect(
      (fixture.nativeElement as HTMLElement).querySelector(
        '[data-testid="installation-budget-not-enforced"]',
      ),
    ).not.toBeNull();
  });

  it('says nothing when budgets are enforced, and nothing when the gateway has not said', () => {
    for (const enforced of [true, null] as const) {
      const { fixture } = setup({ enforced });

      expect(
        (fixture.nativeElement as HTMLElement).querySelector(
          '[data-testid="installation-budget-not-enforced"]',
        ),
      ).toBeNull();
    }
  });
});
