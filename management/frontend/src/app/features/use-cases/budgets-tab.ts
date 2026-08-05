import { Component, computed, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { Budget, BudgetUsage } from '../../core/api/models';
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
  imports: [FormsModule, RouterLink],
  templateUrl: './budgets-tab.html',
})
export class BudgetsTab {
  readonly slug = input.required<string>();
  readonly budgets = input.required<Budget[]>();
  readonly usage = input.required<Record<number, BudgetUsage>>();
  readonly usageUnavailable = input(false);
  readonly usageRefused = input(false);
  readonly changed = output<void>();

  private readonly service = inject(UseCaseService);
  private readonly confirmService = inject(ConfirmService);
  protected readonly feedback = inject(PageFeedback);

  protected readonly showForm = signal(false);
  protected readonly budgetScope = signal<'use_case' | 'member'>('use_case');
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

  /** Percentage of a spend limit consumed. Compares in nano-units, so the division never
   * touches a decimal amount. */
  protected costPct(budget: Budget): number {
    const limit = budget.limit_cost ? Number(budget.limit_cost) * 1_000_000_000 : 0;
    return limit
      ? Math.min(100, Math.round((this.usedFor(budget).used_cost_nanos / limit) * 100))
      : 0;
  }

  /** Total requests in the period whose cost is unknown because the model has no price. */
  protected readonly unpricedRequests = computed(() =>
    Object.values(this.usage()).reduce((sum, entry) => sum + (entry.unpriced_requests ?? 0), 0),
  );

  protected pct(used: number, limit: number | null | undefined): number {
    return limit ? Math.min(100, Math.round((used / limit) * 100)) : 0;
  }

  protected labelFor(budget: Budget): string {
    return budget.scope === 'member' ? budget.subject || 'member' : 'Whole use case';
  }
}
