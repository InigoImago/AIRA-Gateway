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
  budgetScope: { set: (v: 'use_case' | 'each_member') => void; (): string };
  budgetPeriod: { set: (v: 'day' | 'month') => void; (): string };
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
  setEnabled: (b: Budget, enabled: boolean) => void;
  feedback: PageFeedback;
}

const httpError = (status: number) =>
  throwError(() => ({ status, error: { error: { message: 'refused' } } }));

interface Options {
  /** What `/v1/me` says this installation prices in. */
  currency?: string;
  budgets?: Budget[];
  usage?: Record<number, BudgetUsage>;
  usageUnavailable?: boolean;
  usageRefused?: boolean;
  /** What the gateway said about `AIRA_ENFORCE_BUDGETS`; `undefined` means it has not said. */
  enforced?: boolean | null;
  overrides?: Overrides;
  confirmAnswer?: boolean;
  canManage?: boolean;
}

function setup(options: Options = {}) {
  TestBed.resetTestingModule();
  const calls: string[] = [];
  const asked: string[] = [];
  const overrides = options.overrides ?? {};
  const service = {
    createBudget: (_s: string, budget: Budget) => {
      calls.push(
        `create:${budget.scope}:${budget.subject}:${budget.limit_cost}:${budget.limit_tokens}:${budget.enabled}`,
      );
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
      {
        provide: ConfirmService,
        useValue: {
          ask: (question: string) => {
            asked.push(question);
            return options.confirmAnswer ?? true;
          },
        },
      },
    ],
  });

  const fixture = TestBed.createComponent(BudgetsTab);
  fixture.componentRef.setInput('slug', 'demo-uc');
  fixture.componentRef.setInput('budgets', options.budgets ?? []);
  fixture.componentRef.setInput('usage', options.usage ?? {});
  fixture.componentRef.setInput('usageUnavailable', options.usageUnavailable ?? false);
  fixture.componentRef.setInput('usageRefused', options.usageRefused ?? false);
  // `?? null` and not `?? true`: the default here is the same "not yet answered" the component
  // defaults to, so a case that says nothing about enforcement is not quietly asserting it.
  fixture.componentRef.setInput('enforced', options.enforced ?? null);
  // Default to an administrator: most of these cases are about the form. The read-only case is a
  // test of its own, below.
  fixture.componentRef.setInput('canManage', options.canManage ?? true);
  fixture.componentRef.setInput('currency', options.currency ?? '');
  const changes: number[] = [];
  fixture.componentInstance.changed.subscribe(() => changes.push(1));
  fixture.detectChanges();

  return {
    fixture,
    calls,
    asked,
    changes,
    component: fixture.componentInstance as unknown as Tab,
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

describe('BudgetsTab', () => {
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
    expect(calls).toContain('create:use_case::250.50:null:true');
  });

  it('saves a budget, clears the form and asks the page to reload', () => {
    const { component, calls, changes } = setup();
    component.budgetCost.set('250.00');
    component.add();

    expect(calls).toContain('create:use_case::250.00:null:true');
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
    expect(component.labelFor({ scope: 'each_member', period: 'month' })).toBe(
      'Each member, individually',
    );
  });

  it('shows no figure for a per-person budget it cannot attribute to the reader', () => {
    // One row is a counter per head, and the gateway answers nothing for a reader it does not bind.
    // An empty bar would claim an untouched allowance; unknown is never rendered as zero (`FRD-603`).
    const usage: Record<number, BudgetUsage> = {
      1: {
        id: 1,
        measured_for: null,
        used_tokens: null,
        used_requests: null,
        used_cost_nanos: null,
        used_cost: null,
        unpriced_requests: null,
      },
    };
    const harness = setup({
      usage,
      budgets: [{ id: 1, scope: 'each_member', period: 'month', limit_cost: '10.000000' }],
    });

    expect(harness.text()).toContain('no consumption of your own');
    expect(harness.text()).toContain('— / 10.00');
    expect(harness.fixture.nativeElement.querySelector('.progress')).toBeNull();
  });

  it('shows a per-person budget the reader does have a figure for', () => {
    const usage: Record<number, BudgetUsage> = {
      1: {
        id: 1,
        measured_for: 'alice',
        used_tokens: 40,
        used_requests: 2,
        used_cost_nanos: 5_000_000_000,
        used_cost: '5.00',
        unpriced_requests: 0,
      },
    };
    const harness = setup({
      usage,
      budgets: [{ id: 1, scope: 'each_member', period: 'month', limit_cost: '10.000000' }],
    });

    expect(harness.text()).toContain('5.00 / 10.00');
    expect(
      harness.component.costPct({
        id: 1,
        scope: 'each_member',
        period: 'month',
        limit_cost: '10.000000',
      }),
    ).toBe(50);
    expect(harness.fixture.nativeElement.querySelector('.progress')).not.toBeNull();
  });

  it('says plainly when there are no budgets', () => {
    expect(setup().text()).toContain('No budgets yet');
  });

  it('names the actual Keycloak group, not a rendered signal', () => {
    // `slug` is an input signal, so `{{ slug }}` would render the function, not the group name.
    const harness = setup({
      budgets: [{ id: 1, scope: 'use_case', period: 'month', limit_tokens: 10 }],
      usageUnavailable: true,
      usageRefused: true,
    });

    expect(harness.text()).toContain('/use-cases/demo-uc');
    expect(harness.text()).not.toContain('function');
  });

  it('distinguishes a gateway that refused from one that could not be reached', () => {
    // Two problems, two remedies: join the Keycloak group, or find out why the gateway is down.
    const budgets: Budget[] = [{ id: 1, scope: 'use_case', period: 'month', limit_tokens: 10 }];
    const refused = setup({ budgets, usageUnavailable: true, usageRefused: true });
    // The remedy names the Keycloak group, which is not the member list on this page.
    expect(refused.text()).toContain('/use-cases/demo-uc');
    expect(refused.text()).toContain('not');
    expect(refused.text()).toContain('member list on this page');

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
    // The limit as every amount here is written, not as it is stored ("1.000000").
    expect(harness.testid('budget-spend-1')?.textContent).toContain('0.50 / 1.00');
    expect(text).not.toContain('1.000000');
    expect(text).toContain('400');
    expect(text).toContain('Whole use case');
  });

  it('groups counts the way the rest of the page does', () => {
    const harness = setup({
      budgets: [{ id: 1, scope: 'use_case', period: 'month', limit_tokens: 2_000_000 }],
      usage: usageFor({ used_tokens: 1_234_567 }),
    });

    expect(harness.testid('budget-tokens-1')?.textContent).toContain('1,234,567 / 2,000,000');
  });

  it('draws nothing it did not measure when the gateway is unreachable', () => {
    // Every budget has no usage entry then, and drawing that as zero — "0.00 / 250.00", an empty
    // bar — told an administrator nothing was spent of a budget that may be 95 % gone.
    const harness = setup({
      budgets: [
        { id: 1, scope: 'use_case', period: 'month', limit_cost: '250.000000', limit_requests: 50 },
      ],
      usageUnavailable: true,
    });

    expect(harness.testid('budget-spend-1')?.textContent).toContain('— / 250.00');
    expect(harness.testid('budget-requests-1')?.textContent).toContain('— / 50');
    expect(
      (harness.fixture.nativeElement as HTMLElement).querySelector('[role="progressbar"]'),
    ).toBeNull();
    expect(harness.text()).not.toContain('Also used');
    // The callout gives the reason; the per-person sentence would give a wrong one.
    expect(harness.testid('budget-not-yours')).toBeNull();
  });

  it('draws a limit of zero as full, because it refuses everything', () => {
    const harness = setup({
      budgets: [{ id: 1, scope: 'use_case', period: 'month', limit_requests: 0 }],
      usage: usageFor({ used_requests: 0 }),
    });

    const bar = (harness.fixture.nativeElement as HTMLElement).querySelector(
      '[role="progressbar"]',
    );
    expect(bar?.getAttribute('aria-valuenow')).toBe('100');
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

  it('shows the validation reason next to the form rather than only disabling the button', () => {
    const harness = setup();
    harness.component.showForm.set(true);
    harness.fixture.detectChanges();

    expect(harness.text()).toContain('Set a spend limit, a token limit, or a request limit.');
    // The window's action row, not inside the `<form>`: a modal keeps its actions in the foot and
    // ties them back with `form="…"`, which is the same shape the model editor uses.
    const submit = (harness.fixture.nativeElement as HTMLElement).querySelector(
      'button[type="submit"][form="budget-form"]',
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
});

describe('BudgetsTab — a reader', () => {
  it('reads a limit as it was set, never rounded to two places', () => {
    const { text } = setup({
      canManage: false,
      budgets: [{ id: 1, scope: 'use_case', period: 'month', limit_cost: '0.125000' }],
    });

    expect(text()).toContain('0.125');
    expect(text()).not.toContain('0.13');
  });

  it('sees the figures and none of the controls', () => {
    const { fixture, text } = setup({
      canManage: false,
      budgets: [{ id: 1, scope: 'use_case', period: 'month', limit_cost: '250.00' }],
    });
    const html = fixture.nativeElement as HTMLElement;

    expect(text()).toContain('250.00');
    expect(text()).not.toContain('+ Add budget');
    expect(html.querySelector('[aria-label^="Remove budget"]')).toBeNull();
    expect(html.querySelector('[data-testid="budgets-readonly"]')).not.toBeNull();
  });
});

describe('BudgetsTab — a budget per person, and a window to make one in', () => {
  it('offers a limit that applies to everybody separately, without naming anybody', () => {
    // A fair share per head, with no list of heads to maintain — unlike the shared pot of "the whole
    // use case", where the first caller can spend all of it.
    const harness = setup();
    harness.component.showForm.set(true);
    harness.fixture.detectChanges();

    const scope = (harness.fixture.nativeElement as HTMLElement).querySelector<HTMLSelectElement>(
      '#budget-scope',
    )!;
    expect([...scope.options].map((o) => o.value)).toEqual(['use_case', 'each_member']);

    harness.component.budgetScope.set('each_member');
    harness.fixture.detectChanges();
    // No username to fill in — that is the whole point.
    expect(harness.fixture.nativeElement.querySelector('#budget-subject')).toBeNull();
    expect(harness.text()).toContain('including people who join later');
  });

  it('says which currency a spend limit is in, and takes it from the installation', () => {
    // The installation's `AIRA_CURRENCY`. Asserted with neither `USD` nor the `EUR` default, so
    // neither a hard-coded label nor a default can pass.
    const harness = setup({ currency: 'CHF' });
    harness.component.showForm.set(true);
    harness.fixture.detectChanges();

    expect(harness.text()).toContain('Spend limit (CHF)');
  });

  it('says nothing about a currency it has not been told', () => {
    /** Empty until `/v1/me` answers. An unlabelled amount is a reader who asks; a wrongly labelled
     *  one is a reader who does not. */
    const harness = setup({ currency: '' });
    harness.component.showForm.set(true);
    harness.fixture.detectChanges();

    expect(harness.text()).toContain('Spend limit');
    expect(harness.text()).not.toContain('Spend limit (');
  });

  it('creates in a window rather than a form unfolding under the list', () => {
    const harness = setup();
    const html = harness.fixture.nativeElement as HTMLElement;
    expect(html.querySelector('[data-testid="budget-editor"]')).toBeNull();

    html.querySelector<HTMLButtonElement>('[data-testid="add-budget"]')!.click();
    harness.fixture.detectChanges();

    expect(html.querySelector('[data-testid="budget-editor"]')).not.toBeNull();
    // One way out that is not the mouse.
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    harness.fixture.detectChanges();
    expect(html.querySelector('[data-testid="budget-editor"]')).toBeNull();
  });
});

/** How a per-head figure is counted (`ADR-0019`), stated where the reader sizes a limit. */
describe('BudgetsTab — the per-head warning', () => {
  // Also for somebody reading the configuration, not only in the creation window.
  it('shows the warning on the tab wherever such a row already exists', () => {
    const empty = setup();
    expect(empty.fixture.nativeElement.querySelector('[data-testid="budget-two-pots"]')).toBeNull();

    const configured = setup({
      budgets: [{ id: 1, scope: 'each_member', period: 'day', limit_requests: 10 }],
    });

    expect(
      configured.fixture.nativeElement.querySelector('[data-testid="budget-two-pots"]'),
    ).not.toBeNull();
  });

  // A use-case-wide budget or limit binds every credential together.
  it('says that a use-case-wide figure still bounds both allowances', () => {
    const harness = setup({
      budgets: [{ id: 1, scope: 'each_member', period: 'day', limit_requests: 10 }],
    });

    const warning = harness.fixture.nativeElement.querySelector('[data-testid="budget-two-pots"]');

    expect(warning?.textContent).toContain('whole use case');
  });

  it('says a person has one allowance, and says it only for the per-head scope', () => {
    const harness = setup();
    harness.component.showForm.set(true);
    harness.component.budgetScope.set('use_case');
    harness.fixture.detectChanges();
    expect(
      harness.fixture.nativeElement.querySelector('[data-testid="budget-two-pots"]'),
    ).toBeNull();

    harness.component.budgetScope.set('each_member');
    harness.fixture.detectChanges();

    const warning = harness.fixture.nativeElement.querySelector('[data-testid="budget-two-pots"]');
    expect(warning).not.toBeNull();
    // `ADR-0019` keys the counter on the person; a false note is worse than none.
    expect(warning?.textContent).toContain('Counted per person');
    expect(warning?.textContent).toContain('Keycloak');
    expect(warning?.textContent).not.toContain('two separate');
  });
});

describe('BudgetsTab — lifting a budget without losing it', () => {
  const BUDGET: Budget = {
    id: 3,
    scope: 'use_case',
    subject: '',
    period: 'month',
    limit_cost: '10.000000',
    limit_tokens: 5000,
  };

  it('offers a switch for a flag the card never even showed', () => {
    // The gateway obeys only enabled budgets, so the card must show and set the flag.
    const page = setup({ budgets: [BUDGET] });

    // The state and the action apart: a badge says what is, the button says what it does.
    expect(page.testid('status-budget-3')?.textContent?.trim()).toBe('Active');
    expect(page.testid('toggle-budget-3')?.textContent?.trim()).toBe('Disable');
    expect(page.text()).not.toContain('Active — disable');
  });

  it('sends the whole row with the switch flipped', () => {
    const page = setup({ budgets: [BUDGET] });
    page.click('toggle-budget-3');

    expect(page.calls).toEqual(['create:use_case::10.000000:5000:false']);
    expect(page.component.feedback.notice()).toContain('kept on record');
    expect(page.changes).toEqual([1]);
  });

  it('puts a lifted budget back', () => {
    const page = setup({ budgets: [{ ...BUDGET, enabled: false }] });
    expect(page.testid('status-budget-3')?.textContent?.trim()).toBe('Disabled');
    expect(page.testid('toggle-budget-3')?.textContent?.trim()).toBe('Enable');
    page.click('toggle-budget-3');

    expect(page.calls).toEqual(['create:use_case::10.000000:5000:true']);
    expect(page.component.feedback.notice()).toContain('capped again');
  });

  it('tells a reader who may not manage that a budget is lifted', () => {
    /** Otherwise the page shows a limit and the gateway applies none, which is the worst of the
     *  three states to be silent about. */
    const page = setup({ budgets: [{ ...BUDGET, enabled: false }], canManage: false });

    expect(page.testid('toggle-budget-3')).toBeNull();
    expect(page.testid('status-budget-3')?.textContent?.trim()).toBe('Disabled');
  });

  it('states that a new budget is active rather than leaving it to a default', () => {
    const page = setup();
    page.component.budgetTokens.set(1000);
    page.component.add();

    expect(page.calls).toEqual(['create:use_case::null:1000:true']);
  });
});

/**
 * A limit that stops nothing must say so (`FRD-610` §3.3).
 *
 * Open since the concept was written, and half-closed on the other side first: a *caller* has been
 * told since `FRD-139` that the limit beside their figure refuses nothing. The console — where
 * somebody sits down and configures the limit — said nothing at all, which is the shape this
 * project already knows as a badge on an inactive control.
 */
describe('BudgetsTab — limits that are not limits', () => {
  it('refuses a token or request limit that is not a whole number of at least one', () => {
    // `min="1"` is inert in an Angular form, and the server accepts 0 — a budget that refuses
    // every request, created by accident and drawn as an empty, reassuring bar.
    const { component } = setup();
    component.budgetRequests.set(0);
    expect(component.validationError()).toContain('request limit is a whole number');

    component.budgetRequests.set(null);
    component.budgetTokens.set(2.5);
    expect(component.validationError()).toContain('token limit is a whole number');

    component.budgetTokens.set(1);
    expect(component.validationError()).toBeNull();
  });
});

/**
 * "+ Add budget" for a scope and period that already have one replaces it — and a limit left empty
 * in the window is cleared on it. It used to say "Budget saved."
 */
describe('BudgetsTab — a new budget where one exists', () => {
  const existing: Budget = {
    id: 3,
    scope: 'use_case',
    period: 'month',
    limit_cost: '250.000000',
    limit_requests: 500,
  };

  function addMonthly(confirmAnswer: boolean) {
    const harness = setup({ budgets: [existing], confirmAnswer });
    harness.component.budgetScope.set('use_case');
    harness.component.budgetPeriod.set('month');
    harness.component.budgetTokens.set(1000);
    harness.component.add();
    return harness;
  }

  it('asks first, naming the budget it replaces, and sends nothing on no', () => {
    const harness = addMonthly(false);

    expect(harness.asked.at(-1)).toContain('monthly budget for whole use case already exists');
    expect(harness.asked.at(-1)).toContain('250.00');
    expect(harness.asked.at(-1)).toContain('500 requests');
    expect(harness.calls.filter((call) => call.startsWith('create:'))).toEqual([]);
  });

  it('says "replaced" when the answer is yes', () => {
    const harness = addMonthly(true);

    expect(harness.calls.some((call) => call.startsWith('create:'))).toBe(true);
    expect(harness.component.feedback.notice()).toBe('Budget replaced.');
  });

  it('asks nothing for a period that has no budget yet', () => {
    const harness = setup({ budgets: [existing] });
    harness.component.budgetPeriod.set('day');
    harness.component.budgetTokens.set(1000);
    harness.component.add();

    expect(harness.asked).toEqual([]);
    expect(harness.component.feedback.notice()).toBe('Budget saved.');
  });

  it('says why a save was refused inside the window', () => {
    const harness = setup({ overrides: { createBudget: httpError(400) } });
    harness.click('add-budget');
    harness.component.budgetTokens.set(1000);
    harness.component.add();
    harness.fixture.detectChanges();

    const said = harness.testid('budget-editor-error');
    expect(said?.textContent).toContain('refused');
    expect(said?.closest('[data-testid="budget-editor"]')).not.toBeNull();
  });
});

describe('BudgetsTab — enforcement', () => {
  const budgets: Budget[] = [
    { id: 1, scope: 'use_case', subject: '', period: 'month', limit_requests: 100, enabled: true },
  ];

  it('says so, above the figures, when the installation does not enforce budgets', () => {
    const { fixture, text } = setup({ budgets, enforced: false });

    const banner = (fixture.nativeElement as HTMLElement).querySelector(
      '[data-testid="budgets-not-enforced"]',
    );
    expect(banner).not.toBeNull();
    expect(text()).toContain('not enforced in this installation');
    // The variable is named, because the reader's next question is where to change it.
    expect(text()).toContain('AIRA_ENFORCE_BUDGETS');
    // And what switching it on would do, which is the part a reader cannot guess: a limit already
    // exceeded starts refusing immediately.
    expect(text()).toContain('already exceeded');
  });

  it('says nothing when budgets are enforced', () => {
    const { fixture } = setup({ budgets, enforced: true });

    expect(
      (fixture.nativeElement as HTMLElement).querySelector('[data-testid="budgets-not-enforced"]'),
    ).toBeNull();
  });

  it('says nothing when the gateway has not answered, rather than claiming either', () => {
    // The direction that matters: a screen that read silence as "not enforced" would tell a
    // reader their limits are inert because a request failed, and one that read it as "enforced"
    // would reassure them for the same reason. Neither is a fact.
    const { fixture, text } = setup({ budgets, enforced: null });

    expect(
      (fixture.nativeElement as HTMLElement).querySelector('[data-testid="budgets-not-enforced"]'),
    ).toBeNull();
    expect(text()).not.toContain('AIRA_ENFORCE_BUDGETS');
  });

  it('is quiet on a use case with no budget at all', () => {
    // There is nothing to be wrong about: no limit is shown, so no limit can read as working.
    const { fixture } = setup({ budgets: [], enforced: false });

    expect(
      (fixture.nativeElement as HTMLElement).querySelector('[data-testid="budgets-not-enforced"]'),
    ).toBeNull();
  });

  it('is shown beside the figures, not instead of them', () => {
    // The limits and the consumption are still what somebody configured and what was spent. A
    // banner that replaced them would hide the numbers the switch does not change.
    const { fixture, text } = setup({
      budgets,
      enforced: false,
      usage: {
        1: {
          id: 1,
          used_requests: 40,
          used_tokens: 0,
          // `measured()` reads this: a row without it is a counter the gateway could not measure,
          // and the figure then renders as an em dash rather than as a number.
          used_cost_nanos: 0,
          used_cost: '0.00',
          unpriced_requests: 0,
        },
      },
    });

    expect(
      (fixture.nativeElement as HTMLElement).querySelector('[data-testid="budgets-not-enforced"]'),
    ).not.toBeNull();
    expect(text()).toContain('40');
  });
});
