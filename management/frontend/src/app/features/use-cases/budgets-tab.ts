import { Component, computed, inject, input, output, signal } from '@angular/core';
import { NgTemplateOutlet } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { InfoHint } from '../../core/ui/info-hint';
import { Modal } from '../../core/ui/modal';
import { Budget, BudgetUsage } from '../../core/api/models';
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
  imports: [NgTemplateOutlet, FormsModule, RouterLink, InfoHint, Modal],
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
  readonly changed = output<void>();

  private readonly service = inject(UseCaseService);
  private readonly confirmService = inject(ConfirmService);
  protected readonly feedback = inject(PageFeedback);

  protected readonly showForm = signal(false);
  protected readonly budgetScope = signal<LimitScope>('use_case');
  protected readonly budgetPeriod = signal<'day' | 'month'>('month');
  protected readonly budgetTokens = signal<number | null>(null);
  protected readonly budgetRequests = signal<number | null>(null);
  /** Kept as text: a spend limit must not round-trip through a JS number. */
  protected readonly budgetCost = signal('');

  protected validationError(): string | null {
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
      // No scope names a person any more (2026-08-14).
      subject: '',
      period: this.budgetPeriod(),
      limit_cost: cost || null,
      limit_tokens: this.budgetTokens(),
      limit_requests: this.budgetRequests(),
      // Stated rather than defaulted: this same call edits an existing row (the endpoint upserts
      // on scope+subject+period), and a body silent about `enabled` used to re-arm a budget
      // somebody had lifted.
      enabled: true,
    };
    this.feedback.run(this.service.createBudget(this.slug(), budget), {
      failure: 'Could not save the budget.',
      success: () => {
        this.feedback.succeed('Budget saved.');
        this.budgetCost.set('');
        this.budgetTokens.set(null);
        this.budgetRequests.set(null);
        this.showForm.set(false);
        this.changed.emit();
      },
    });
  }

  /**
   * Lift a budget without losing it, or put it back.
   *
   * `enabled` has been on the model, on the wire and **obeyed by the gateway** — which selects
   * only `enabled` budgets — since budgets existed, and this card neither showed it nor could
   * change it. So a use case could be spending against a budget the console displayed and the
   * data plane ignored, with no way to tell from this screen and no way to lift one for an
   * incident except to delete it and lose what it was.
   *
   * The whole row goes back because the endpoint upserts on scope+subject+period: a body carrying
   * only the switch would blank the limits beside it.
   */
  protected setEnabled(budget: Budget, enabled: boolean): void {
    if (!this.canManage() || this.feedback.busy()) return;
    this.feedback.run(this.service.createBudget(this.slug(), { ...budget, enabled }), {
      failure: enabled ? 'Could not enable the budget.' : 'Could not disable the budget.',
      success: () => {
        this.feedback.succeed(
          enabled
            ? 'Budget enabled. Spending is capped again.'
            : 'Budget disabled. It is kept on record and stops binding.',
        );
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

  /**
   * The figures this budget's scope consumed that it does **not** limit.
   *
   * A budget is a limit on one metric or two; the period's other figures are measured all the
   * same and were simply not rendered, so a request budget answered "how much money" with
   * silence. Only what is actually known appears — a null is unknown, and `FRD-603`'s rule is
   * that unknown is never shown as zero.
   */
  protected alsoUsed(budget: Budget): { label: string; value: string }[] {
    const used = this.usedFor(budget);
    const out: { label: string; value: string }[] = [];
    if (!budget.limit_cost && used.used_cost != null) {
      out.push({ label: 'spent ($)', value: used.used_cost });
    }
    if (budget.limit_tokens == null && used.used_tokens != null) {
      out.push({ label: 'tokens', value: `${used.used_tokens}` });
    }
    if (budget.limit_requests == null && used.used_requests != null) {
      out.push({ label: 'request(s)', value: `${used.used_requests}` });
    }
    return out;
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

  /** True where a per-head row exists, which is what makes the two-allowance warning relevant. */
  protected hasPerHead(): boolean {
    return this.budgets().some((row) => row.scope === 'each_member');
  }

  protected labelFor(budget: Budget): string {
    return budget.scope === 'each_member' ? 'Each member, individually' : 'Whole use case';
  }
}
