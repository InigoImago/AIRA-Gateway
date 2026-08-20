import { TestBed } from '@angular/core/testing';
import { Observable, of, throwError } from 'rxjs';
import { Budget, Me } from '../../core/api/models';
import { MeService } from '../../core/api/me.service';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { PageFeedback } from '../../core/ui/page-feedback';
import { InstallationBudgetCard } from './installation-budget-card';

interface Card {
  canManage: () => boolean;
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
  roles?: string[];
  budgets?: Budget[];
  list?: Observable<Budget[]>;
  save?: Observable<Budget>;
  remove?: Observable<void>;
  confirmAnswer?: boolean;
  me?: Observable<Me>;
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
    get: () =>
      options.me ??
      of({
        subject: 's',
        username: 'u',
        email: 'u@example.test',
        roles: options.roles ?? ['global-admin'],
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
    // The server answers `[]` both to "nothing is configured" and to "not your business", and
    // this component cannot tell them apart — so it must not claim the first. A use-case user
    // reading "no limit is set" would be told something nobody verified.
    const { testid, text } = setup({ roles: ['use-case-user'], budgets: [] });

    expect(testid('installation-budget')).toBeNull();
    expect(text()).toBe('');
  });

  it('shows a governance reader the limit and offers no way to change it', () => {
    // `ADR-0007`: IT Steuerung oversees and acts in nothing.
    const { testid, text } = setup({ roles: ['it-steuerung'], budgets: [MONTHLY] });

    expect(testid('installation-budget')).not.toBeNull();
    expect(text()).toContain('$20.000000');
    expect(testid('add-installation-budget')).toBeNull();
    expect(testid('toggle-installation-budget-7')).toBeNull();
    expect(testid('remove-installation-budget-7')).toBeNull();
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
    // `scope: 'installation'` with an empty use case is what selects the residual bucket in the
    // gateway; and an upsert silent about `enabled` used to re-arm a budget somebody had lifted.
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
    // A card showing a bare `50` says nothing: fifty dollars, fifty tokens and fifty requests are
    // three different limits, and two of them are what binds a model with no price on file.
    const { component } = setup();

    expect(
      component.limitsOf({
        ...MONTHLY,
        limit_cost: '5.000000',
        limit_tokens: 1000,
        limit_requests: 50,
      }),
    ).toEqual(['$5.000000', '1000 tokens', '50 request(s)']);
    expect(component.limitsOf({ ...MONTHLY, limit_cost: null })).toEqual([]);
  });

  it('tells a governance reader that a limit is lifted, since the figure alone would mislead', () => {
    // A budget the gateway skips is a budget that is not in force, and a card showing its numbers
    // without saying so reads as protection that is not there.
    const { text } = setup({
      roles: ['it-steuerung'],
      budgets: [{ ...MONTHLY, enabled: false }],
    });

    expect(text()).toContain('disabled');
  });

  it('does not act for a reader who may not, even if the call is made', () => {
    // The buttons are not rendered for them; this is the second lock, on the method itself. A
    // console that offers nothing but would act if asked is one DOM edit away from acting.
    const { component, calls } = setup({ roles: ['it-steuerung'], budgets: [MONTHLY] });

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

  it('keeps the figure on screen when the roles cannot be read', () => {
    // Losing the reader's roles costs them a form, not a figure — and the page's one banner
    // belongs to the report it is named after, not to this panel's side question.
    const { testid, feedback, text } = setup({ budgets: [MONTHLY], me: httpError(500) });

    expect(testid('installation-budget')).not.toBeNull();
    expect(text()).toContain('$20.000000');
    expect(feedback.error()).toBeNull();
    expect(testid('add-installation-budget')).toBeNull();
  });
});
