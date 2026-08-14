import { Component, computed, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { InfoHint } from '../../core/ui/info-hint';
import { Modal } from '../../core/ui/modal';
import { Budget, BudgetUsage, Membership } from '../../core/api/models';
import { LimitScope } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { PageFeedback } from '../../core/ui/page-feedback';

const NO_USAGE: BudgetUsage = {
  id: 0,
  used_tokens: 0,
  used_requests: 0,
  used_cost_nanos: 0,
  used_cost: '0.00',
  unpriced_requests: 0,
};

/**
 * The budgets panel of a use case (FRD-400/401/403).
 *
 * Limits come from Management and consumption from the gateway, which is why both arrive as
 * inputs: they are two different requests to two different services, and the parent is the one
 * that knows a gateway it cannot reach must not blank out the limits as well.
 *
 * What lives here is the form, its validation, and the arithmetic behind the consumption bars.
 */
@Component({
  selector: 'app-budgets-tab',
  imports: [FormsModule, RouterLink, InfoHint, Modal],
  templateUrl: './budgets-tab.html',
})
export class BudgetsTab {
  readonly slug = input.required<string>();
  readonly budgets = input.required<Budget[]>();
  readonly usage = input.required<Record<number, BudgetUsage>>();
  readonly usageUnavailable = input(false);
  readonly usageRefused = input(false);
  /** Whether this caller may change anything here. Told by the page, which was told by the
   * server — an object-level permission is not in the token, and a panel that assumes yes offers
   * buttons that answer 403. */
  readonly canManage = input(false);
  /**
   * The people this use case already has, for the "one named person" field.
   *
   * A **suggestion, never a restriction**, and that distinction is one this project has already
   * paid for: a rule names a *subject*, and access can come through a Keycloak group, so a
   * group-granted service account belongs to **no membership row at all** (`FRD-209`). A picker
   * over this list is therefore narrower than the rule it fills in — `FRD-604` recorded the same
   * conclusion for a key's owner and typed it rather than picking. So the list assists and the
   * field still accepts anything.
   */
  readonly members = input<Membership[]>([]);
  readonly changed = output<void>();

  private readonly service = inject(UseCaseService);
  private readonly confirmService = inject(ConfirmService);
  protected readonly feedback = inject(PageFeedback);

  protected readonly showForm = signal(false);
  protected readonly budgetScope = signal<LimitScope>('use_case');
  protected readonly budgetSubject = signal('');
  protected readonly budgetPeriod = signal<'day' | 'month'>('month');
  protected readonly budgetTokens = signal<number | null>(null);
  protected readonly budgetRequests = signal<number | null>(null);
  /** Kept as text: a spend limit must not round-trip through a JS number. */
  protected readonly budgetCost = signal('');

  protected validationError(): string | null {
    if (this.budgetScope() === 'member' && !this.budgetSubject().trim()) {
      return 'A member budget needs a username.';
    }
    const cost = this.budgetCost().trim();
    if (cost && !/^\d+([.,]\d{1,6})?$/.test(cost)) {
      return 'The spend limit must be an amount, e.g. 250 or 250.00.';
    }
    if (!cost && this.budgetTokens() == null && this.budgetRequests() == null) {
      return 'Set a spend limit, a token limit, or a request limit.';
    }
    return null;
  }

  protected canAdd(): boolean {
    return !this.validationError() && !this.feedback.busy();
  }

  protected add(): void {
    if (!this.canAdd()) {
      return;
    }
    const cost = this.budgetCost().trim().replace(',', '.');
    const budget: Budget = {
      scope: this.budgetScope(),
      subject: this.budgetScope() === 'member' ? this.budgetSubject().trim() : '',
      period: this.budgetPeriod(),
      limit_cost: cost || null,
      limit_tokens: this.budgetTokens(),
      limit_requests: this.budgetRequests(),
    };
    this.feedback.run(this.service.createBudget(this.slug(), budget), {
      failure: 'Could not save the budget.',
      success: () => {
        this.feedback.succeed('Budget saved.');
        this.budgetSubject.set('');
        this.budgetCost.set('');
        this.budgetTokens.set(null);
        this.budgetRequests.set(null);
        this.showForm.set(false);
        this.changed.emit();
      },
    });
  }

  protected remove(id: number | undefined): void {
    if (id == null || !this.confirmService.ask('Remove this budget? Its limits stop applying.')) {
      return;
    }
    this.feedback.run(this.service.deleteBudget(this.slug(), id), {
      failure: 'Could not remove the budget.',
      success: () => {
        this.feedback.succeed('Budget removed.');
        this.changed.emit();
      },
    });
  }

  protected usedFor(budget: Budget): BudgetUsage {
    return (budget.id != null ? this.usage()[budget.id] : undefined) ?? NO_USAGE;
  }

  /**
   * Whether the figures on this card describe the reader.
   *
   * A per-person budget is one configured row and one counter per head, so the gateway answers
   * with the reader's own figure — and with nothing at all for somebody the row does not bind (an
   * oversight role belongs to no use case). Drawing that as an empty bar would say the allowance
   * is untouched, which is a different and confident claim.
   *
   * A row the gateway has never counted at all is a different case and stays at zero: nothing has
   * been spent, and that *is* known.
   */
  protected measured(budget: Budget): boolean {
    const entry = budget.id != null ? this.usage()[budget.id] : undefined;
    return !entry || entry.used_cost_nanos != null;
  }

  /** Percentage of a spend limit consumed. Compares in nano-units, so the division never
   * touches a decimal amount. */
  protected costPct(budget: Budget): number {
    const limit = budget.limit_cost ? Number(budget.limit_cost) * 1_000_000_000 : 0;
    return limit
      ? Math.min(100, Math.round(((this.usedFor(budget).used_cost_nanos ?? 0) / limit) * 100))
      : 0;
  }

  /** Total requests in the period whose cost is unknown because the model has no price. */
  protected readonly unpricedRequests = computed(() =>
    Object.values(this.usage()).reduce((sum, entry) => sum + (entry.unpriced_requests ?? 0), 0),
  );

  protected pct(used: number | null, limit: number | null | undefined): number {
    return limit ? Math.min(100, Math.round(((used ?? 0) / limit) * 100)) : 0;
  }

  protected labelFor(budget: Budget): string {
    if (budget.scope === 'member') {
      return budget.subject || 'member';
    }
    return budget.scope === 'each_member' ? 'Each member, individually' : 'Whole use case';
  }
}
