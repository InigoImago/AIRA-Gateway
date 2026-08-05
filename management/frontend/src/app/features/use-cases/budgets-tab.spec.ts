import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { Observable, of, throwError } from 'rxjs';
import { Budget, BudgetUsage } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { PageFeedback } from '../../core/ui/page-feedback';
import { BudgetsTab } from './budgets-tab';

interface Overrides {
  createBudget?: Observable<Budget>;
  deleteBudget?: Observable<void>;
}

interface Tab {
  showForm: { set: (v: boolean) => void; (): boolean };
  budgetScope: { set: (v: 'use_case' | 'member') => void; (): string };
  budgetSubject: { set: (v: string) => void; (): string };
  budgetTokens: { set: (v: number | null) => void; (): number | null };
  budgetRequests: { set: (v: number | null) => void; (): number | null };
  budgetCost: { set: (v: string) => void; (): string };
  validationError: () => string | null;
  canAdd: () => boolean;
  add: () => void;
  remove: (id: number | undefined) => void;
  usedFor: (b: Budget) => BudgetUsage;
  costPct: (b: Budget) => number;
  pct: (used: number, limit: number | null | undefined) => number;
  labelFor: (b: Budget) => string;
  unpricedRequests: () => number;
  feedback: PageFeedback;
}

const httpError = (status: number) =>
  throwError(() => ({ status, error: { error: { message: 'refused' } } }));

interface Options {
  budgets?: Budget[];
  usage?: Record<number, BudgetUsage>;
  usageUnavailable?: boolean;
  usageRefused?: boolean;
  overrides?: Overrides;
  confirmAnswer?: boolean;
}

function setup(options: Options = {}) {
  TestBed.resetTestingModule();
  const calls: string[] = [];
  const overrides = options.overrides ?? {};
  const service = {
    createBudget: (_s: string, budget: Budget) => {
      calls.push(`create:${budget.scope}:${budget.subject}:${budget.limit_cost}`);
      return overrides.createBudget ?? of(budget);
    },
    deleteBudget: (_s: string, id: number) => {
      calls.push(`delete:${id}`);
      return overrides.deleteBudget ?? of(undefined as unknown as void);
    },
  };

  TestBed.configureTestingModule({
    imports: [BudgetsTab],
    providers: [
      provideRouter([]),
      PageFeedback,
      { provide: UseCaseService, useValue: service },
      { provide: ConfirmService, useValue: { ask: () => options.confirmAnswer ?? true } },
    ],
  });

  const fixture = TestBed.createComponent(BudgetsTab);
  fixture.componentRef.setInput('slug', 'demo-uc');
  fixture.componentRef.setInput('budgets', options.budgets ?? []);
  fixture.componentRef.setInput('usage', options.usage ?? {});
  fixture.componentRef.setInput('usageUnavailable', options.usageUnavailable ?? false);
  fixture.componentRef.setInput('usageRefused', options.usageRefused ?? false);
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

describe('BudgetsTab', () => {
  it('requires a username for a member budget', () => {
    const { component } = setup();
    component.budgetScope.set('member');
    component.budgetSubject.set('  ');
    expect(component.validationError()).toBe('A member budget needs a username.');
    expect(component.canAdd()).toBe(false);
  });

  it('requires at least one limit', () => {
    expect(setup().component.validationError()).toBe(
      'Set a spend limit, a token limit, or a request limit.',
    );
  });

  it('refuses a spend limit that is not an amount', () => {
    const { component } = setup();
    component.budgetCost.set('viel');
    expect(component.validationError()).toContain('must be an amount');
  });

  it('accepts a comma as the decimal separator', () => {
    // Half of Europe types it that way; refusing it would look like the field is broken.
    const { component, calls } = setup();
    component.budgetCost.set('250,50');
    expect(component.validationError()).toBeNull();

    component.add();
    expect(calls).toContain('create:use_case::250.50');
  });

  it('saves a budget, clears the form and asks the page to reload', () => {
    const { component, calls, changes } = setup();
    component.budgetCost.set('250.00');
    component.add();

    expect(calls).toContain('create:use_case::250.00');
    expect(component.feedback.notice()).toBe('Budget saved.');
    expect(component.budgetCost()).toBe('');
    expect(component.showForm()).toBe(false);
    expect(changes.length).toBe(1);
  });

  it('does not submit an invalid budget', () => {
    const { component, calls } = setup();
    component.add();
    expect(calls).toEqual([]);
  });

  it('surfaces a refused save rather than appearing to have worked', () => {
    const { component, changes } = setup({ overrides: { createBudget: httpError(403) } });
    component.budgetCost.set('10.00');
    component.add();

    expect(component.feedback.error()).toBe('refused');
    expect(component.feedback.notice()).toBeNull();
    expect(changes.length).toBe(0);
  });

  it('asks before removing and does nothing when declined', () => {
    const declined = setup({ confirmAnswer: false });
    declined.component.remove(7);
    expect(declined.calls).toEqual([]);

    const accepted = setup({ confirmAnswer: true });
    accepted.component.remove(7);
    expect(accepted.calls).toContain('delete:7');
    expect(accepted.component.feedback.notice()).toBe('Budget removed.');
  });

  it('ignores a remove with no id', () => {
    const { component, calls } = setup();
    component.remove(undefined);
    expect(calls).toEqual([]);
  });

  it('treats a budget with no consumption as unconsumed rather than undefined', () => {
    const { component } = setup();
    expect(component.usedFor({ scope: 'use_case', period: 'month', id: 9 }).used_cost).toBe('0.00');
  });

  it('divides in nano-units so a spend bar never touches a decimal amount', () => {
    const usage: Record<number, BudgetUsage> = {
      1: {
        id: 1,
        used_tokens: 0,
        used_requests: 0,
        used_cost_nanos: 500_000_000,
        used_cost: '0.50',
        unpriced_requests: 0,
      },
    };
    const { component } = setup({ usage });
    expect(
      component.costPct({ id: 1, scope: 'use_case', period: 'month', limit_cost: '1.00' }),
    ).toBe(50);
  });

  it('shows a bar as full rather than past full', () => {
    const usage: Record<number, BudgetUsage> = {
      1: {
        id: 1,
        used_tokens: 0,
        used_requests: 0,
        used_cost_nanos: 9_000_000_000,
        used_cost: '9.00',
        unpriced_requests: 0,
      },
    };
    const { component } = setup({ usage });
    expect(
      component.costPct({ id: 1, scope: 'use_case', period: 'month', limit_cost: '1.00' }),
    ).toBe(100);
    expect(component.pct(50, 10)).toBe(100);
    expect(component.pct(5, null)).toBe(0);
  });

  it('counts unpriced requests across every budget of the use case', () => {
    const usage: Record<number, BudgetUsage> = {
      1: {
        id: 1,
        used_tokens: 0,
        used_requests: 0,
        used_cost_nanos: 0,
        used_cost: '0.00',
        unpriced_requests: 2,
      },
      2: {
        id: 2,
        used_tokens: 0,
        used_requests: 0,
        used_cost_nanos: 0,
        used_cost: '0.00',
        unpriced_requests: 3,
      },
    };
    expect(setup({ usage }).component.unpricedRequests()).toBe(5);
  });

  it('warns that unpriced traffic is missing from the spend figures', () => {
    // "Unknown is not zero": a spend figure that quietly omits traffic is worse than one that
    // admits what it cannot account for.
    const usage: Record<number, BudgetUsage> = {
      1: {
        id: 1,
        used_tokens: 0,
        used_requests: 0,
        used_cost_nanos: 0,
        used_cost: '0.00',
        unpriced_requests: 4,
      },
    };
    const harness = setup({ usage, budgets: [{ id: 1, scope: 'use_case', period: 'month' }] });
    expect(harness.text()).toContain('no price on file');
    expect(harness.text()).toContain('not included in the spend figures');
  });

  it('names who a budget applies to', () => {
    const { component } = setup();
    expect(component.labelFor({ scope: 'use_case', period: 'month' })).toBe('Whole use case');
    expect(component.labelFor({ scope: 'member', subject: 'alice', period: 'month' })).toBe(
      'alice',
    );
    expect(component.labelFor({ scope: 'member', period: 'month' })).toBe('member');
  });

  it('says plainly when there are no budgets', () => {
    expect(setup().text()).toContain('No budgets yet');
  });

  it('names the actual Keycloak group, not a rendered signal', () => {
    // `slug` is an input signal here, so `{{ slug }}` renders the *function* — the panel would
    // then tell an administrator to add someone to a group whose name is a minified closure.
    // Every unit test passed through that; only the browser showed it.
    const harness = setup({
      budgets: [{ id: 1, scope: 'use_case', period: 'month', limit_tokens: 10 }],
      usageUnavailable: true,
      usageRefused: true,
    });

    expect(harness.text()).toContain('/use-cases/demo-uc');
    expect(harness.text()).not.toContain('function');
  });

  it('distinguishes a gateway that refused from one that could not be reached', () => {
    // Two different problems with two different remedies: join the Keycloak group, or find out
    // why the gateway is down. One message for both would send people the wrong way.
    const budgets: Budget[] = [{ id: 1, scope: 'use_case', period: 'month', limit_tokens: 10 }];
    const refused = setup({ budgets, usageUnavailable: true, usageRefused: true });
    expect(refused.text()).toContain('does not count you as a member');

    const unreachable = setup({ budgets, usageUnavailable: true, usageRefused: false });
    expect(unreachable.text()).toContain('could not be reached');
    expect(unreachable.text()).toContain('still in force');
  });
});

describe('BudgetsTab rendering', () => {
  const usageFor = (over: Partial<BudgetUsage> = {}): Record<number, BudgetUsage> => ({
    1: {
      id: 1,
      used_tokens: 400,
      used_requests: 5,
      used_cost_nanos: 500_000_000,
      used_cost: '0.50',
      unpriced_requests: 0,
      ...over,
    },
  });

  it('renders a bar for every limit the budget sets', () => {
    const harness = setup({
      budgets: [
        {
          id: 1,
          scope: 'use_case',
          period: 'month',
          limit_cost: '1.000000',
          limit_tokens: 1000,
          limit_requests: 10,
        },
      ],
      usage: usageFor(),
    });

    const bars = (harness.fixture.nativeElement as HTMLElement).querySelectorAll(
      '[role="progressbar"]',
    );
    expect(bars.length).toBe(3); // spend, tokens, requests
    const text = harness.text();
    expect(text).toContain('0.50');
    expect(text).toContain('1.000000');
    expect(text).toContain('400');
    expect(text).toContain('Whole use case');
  });

  it('warns before the wall and marks the bar full at it', () => {
    // A bar that looks identical at 40% and 95% tells an administrator nothing until the 429.
    const near = setup({
      budgets: [{ id: 1, scope: 'use_case', period: 'month', limit_cost: '0.600000' }],
      usage: usageFor(),
    });
    expect(
      (near.fixture.nativeElement as HTMLElement).querySelector('.is-warn, .is-full'),
    ).toBeTruthy();

    const full = setup({
      budgets: [{ id: 1, scope: 'use_case', period: 'month', limit_cost: '0.100000' }],
      usage: usageFor(),
    });
    expect((full.fixture.nativeElement as HTMLElement).querySelector('.is-full')).toBeTruthy();
  });

  it('shows the member-username field only for a member-scoped budget', () => {
    const harness = setup();
    harness.component.showForm.set(true);
    harness.fixture.detectChanges();
    const html = harness.fixture.nativeElement as HTMLElement;
    expect(html.querySelector('#budget-subject')).toBeNull();
    expect(html.querySelector('#budget-cost')).toBeTruthy();

    harness.component.budgetScope.set('member');
    harness.fixture.detectChanges();
    expect(html.querySelector('#budget-subject')).toBeTruthy();
  });

  it('shows the validation reason next to the form rather than only disabling the button', () => {
    const harness = setup();
    harness.component.showForm.set(true);
    harness.fixture.detectChanges();

    expect(harness.text()).toContain('Set a spend limit, a token limit, or a request limit.');
    const submit = (harness.fixture.nativeElement as HTMLElement).querySelector(
      'form button[type="submit"]',
    ) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
  });

  it('submits from the form in the DOM, not only from the method', () => {
    const harness = setup();
    harness.component.showForm.set(true);
    harness.fixture.detectChanges();
    harness.component.budgetCost.set('12.00');
    harness.fixture.detectChanges();

    const form = (harness.fixture.nativeElement as HTMLElement).querySelector(
      'form',
    ) as HTMLFormElement;
    form.dispatchEvent(new Event('submit'));

    expect(harness.calls.some((c) => c.startsWith('create:'))).toBe(true);
  });

  it('removes a budget from its own card button', () => {
    const harness = setup({
      budgets: [{ id: 4, scope: 'use_case', period: 'month', limit_requests: 5 }],
      usage: usageFor(),
    });
    const button = (harness.fixture.nativeElement as HTMLElement).querySelector(
      '[aria-label^="Remove "]',
    ) as HTMLButtonElement;
    button.click();

    expect(harness.calls).toContain('delete:4');
  });

  it('names a member budget by its member', () => {
    const harness = setup({
      budgets: [{ id: 1, scope: 'member', subject: 'alice', period: 'day', limit_tokens: 100 }],
      usage: usageFor(),
    });
    expect(harness.text()).toContain('alice');
  });
});
