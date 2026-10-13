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
import { displayCount, nanosOf, storedAmount } from '../../core/ui/money';
import { PageFeedback } from '../../core/ui/page-feedback';
import { bindsEachMember, scopeLabel } from './limit-scope';

/** A budget the gateway has never counted: nothing has been spent, and that is known. */
const NO_USAGE: BudgetUsage = {
  id: 0,
  used_tokens: 0,
  used_requests: 0,
  used_cost_nanos: 0,
  used_cost: '0.00',
  unpriced_requests: 0,
};

/**
 * The budgets panel of a use case (FRD-400/401/403): the form, its validation, and the arithmetic
 * behind the consumption bars.
 *
 * Limits come from Management and consumption from the gateway, so both arrive as inputs: the
 * parent knows that an unreachable gateway must not blank out the limits as well.
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
  /**
   * Whether budgets refuse at all in this installation (`AIRA_ENFORCE_BUDGETS`, `FRD-610` §3.3).
   *
   * **Three states, and `null` is the one that matters.** `false` says the limits below stop
   * nothing; `null` says the gateway did not answer, which is *not* the same and must not be drawn
   * as either. A boolean defaulting to `true` would have the screen claim enforcement every time
   * the gateway is unreachable — the direction that reassures.
   */
  readonly enforced = input<boolean | null>(null);
  /** Told by the page, which asked the server: an object-level permission is not in the token. */
  readonly canManage = input(false);
  /** The spend limit's unit — an input, because the parent already holds `/v1/me` (`CLAUDE.md` §3). */
  readonly currency = input('');
  readonly changed = output<void>();

  private readonly service = inject(UseCaseService);
  private readonly confirmService = inject(ConfirmService);
  protected readonly feedback = inject(PageFeedback);

  /**
   * The unit in the label, or nothing. Built here as one string: interpolated across a control-flow
   * block, the template would put the block's whitespace between `Spend limit` and the unit.
   */
  protected readonly costUnit = computed(() => {
    const unit = this.currency();
    return unit ? ` (${unit})` : '';
  });

  protected readonly showForm = signal(false);
  protected readonly budgetScope = signal<LimitScope>('use_case');
  protected readonly budgetPeriod = signal<'day' | 'month'>('month');
  protected readonly budgetTokens = signal<number | null>(null);
  protected readonly budgetRequests = signal<number | null>(null);
  /** Kept as text: a spend limit must not round-trip through a JS number. */
  protected readonly budgetCost = signal('');

  /** Total requests in the period whose cost is unknown because the model has no price. */
  protected readonly unpricedRequests = computed(() =>
    Object.values(this.usage()).reduce((sum, entry) => sum + (entry.unpriced_requests ?? 0), 0),
  );

  protected validationError(): string | null {
    const cost = this.budgetCost().trim();
    if (cost && !/^\d+([.,]\d{1,6})?$/.test(cost)) {
      return 'The spend limit must be an amount, e.g. 250 or 250.00.';
    }
    // Checked here because the browser's `min` is inert in an Angular form (`novalidate`), and the
    // server accepts 0: a budget of 0 requests refuses every request, which nobody sets on purpose.
    if (!isWholeAtLeastOne(this.budgetTokens())) {
      return 'A token limit is a whole number of at least 1.';
    }
    if (!isWholeAtLeastOne(this.budgetRequests())) {
      return 'A request limit is a whole number of at least 1.';
    }
    if (!cost && this.budgetTokens() == null && this.budgetRequests() == null) {
      return 'Set a spend limit, a token limit, or a request limit.';
    }
    return null;
  }

  /** Open the window afresh: an earlier failure on the page is not this window's outcome. */
  protected openForm(): void {
    this.feedback.clear();
    this.showForm.set(true);
  }

  /** The budget a new one would replace: the endpoint upserts on scope, subject and period. */
  private replaces(scope: LimitScope, period: string): Budget | undefined {
    return this.budgets().find(
      (row) => row.scope === scope && !row.subject && row.period === period,
    );
  }

  protected canAdd(): boolean {
    return !this.validationError() && !this.feedback.busy();
  }

  protected add(): void {
    if (!this.canAdd()) {
      return;
    }
    // "+ Add" with the scope and period of a budget that exists replaces it, and a limit left
    // empty here is cleared there — asked first, and said afterwards.
    const replaced = this.replaces(this.budgetScope(), this.budgetPeriod());
    if (
      replaced &&
      !this.confirmService.ask(
        `A ${replaced.period}ly budget for ${this.labelFor(replaced).toLowerCase()} already ` +
          `exists (${this.limitsOf(replaced)}). Saving replaces it; a limit left empty here is ` +
          `removed from it.`,
      )
    ) {
      return;
    }
    const cost = this.budgetCost().trim().replace(',', '.');
    const budget: Budget = {
      scope: this.budgetScope(),
      // No scope names a person.
      subject: '',
      period: this.budgetPeriod(),
      limit_cost: cost || null,
      limit_tokens: this.budgetTokens(),
      limit_requests: this.budgetRequests(),
      // Stated, not defaulted: the endpoint upserts on scope+subject+period, and a body silent
      // about `enabled` would re-arm a budget somebody had lifted.
      enabled: true,
    };
    this.feedback.run(this.service.createBudget(this.slug(), budget), {
      failure: 'Could not save the budget.',
      success: () => {
        this.feedback.succeed(replaced ? 'Budget replaced.' : 'Budget saved.');
        this.budgetCost.set('');
        this.budgetTokens.set(null);
        this.budgetRequests.set(null);
        this.showForm.set(false);
        this.changed.emit();
      },
    });
  }

  /**
   * Lift a budget without losing it, or put it back. The gateway obeys only `enabled` budgets.
   *
   * The whole row is sent because the endpoint upserts on scope+subject+period: a body carrying
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
   * The figures this budget's scope consumed that it does **not** limit (`FRD-606` FR-1).
   *
   * Only what is known appears: a null is unknown, and unknown is never shown as zero (`FRD-603`).
   */
  protected alsoUsed(budget: Budget): { label: string; value: string }[] {
    const used = this.usedFor(budget);
    const out: { label: string; value: string }[] = [];
    if (!budget.limit_cost && used.used_cost != null) {
      out.push({ label: `spent${this.costUnit()}`, value: used.used_cost });
    }
    if (budget.limit_tokens == null && used.used_tokens != null) {
      out.push({ label: 'tokens', value: displayCount(used.used_tokens) });
    }
    if (budget.limit_requests == null && used.used_requests != null) {
      out.push({ label: 'request(s)', value: displayCount(used.used_requests) });
    }
    return out;
  }

  /** A limit as it was set: `'250.000000'` reads `250.00`, and `'0.125000'` reads `0.125`. */
  protected limitText(limit: string): string {
    return storedAmount(limit) ?? limit;
  }

  /** A count, grouped like every other count on the page; unknown is a dash, never `0`. */
  protected count(value: number | null | undefined): string {
    return value == null ? '—' : displayCount(value);
  }

  /** What a budget limits, in words — for the question before one replaces it. */
  private limitsOf(budget: Budget): string {
    const parts = [
      budget.limit_cost ? `${this.limitText(budget.limit_cost)}${this.costUnit()}` : null,
      budget.limit_tokens != null ? `${displayCount(budget.limit_tokens)} tokens` : null,
      budget.limit_requests != null ? `${displayCount(budget.limit_requests)} requests` : null,
    ].filter(Boolean);
    return parts.length ? parts.join(', ') : 'no limit';
  }

  protected usedFor(budget: Budget): BudgetUsage {
    return (budget.id != null ? this.usage()[budget.id] : undefined) ?? NO_USAGE;
  }

  /**
   * Whether the figures on this card were measured at all.
   *
   * Not when the gateway did not answer: every budget then has no usage entry, and drawing that as
   * zero is "0.00 / 250.00" and an empty bar over a budget that may be 95 % spent — a measurement
   * nobody made. For a per-person budget the gateway answers with the reader's own counter, and with
   * nothing for somebody the row does not bind; an empty bar would then falsely claim an untouched
   * allowance. A row the gateway answered for and never counted stays at zero, which is known.
   */
  protected measured(budget: Budget): boolean {
    if (this.usageUnavailable()) return false;
    const entry = budget.id != null ? this.usage()[budget.id] : undefined;
    return !entry || entry.used_cost_nanos != null;
  }

  /** Percentage of a spend limit consumed, compared in nano-units read on the digits. */
  protected costPct(budget: Budget): number {
    const limit = budget.limit_cost ? nanosOf(budget.limit_cost) : null;
    return this.pct(this.usedFor(budget).used_cost_nanos, limit);
  }

  /**
   * Percentage of a limit consumed. A limit of zero is drawn **full**: it refuses every request,
   * and an empty bar beside it would read as the most room there could be.
   */
  protected pct(used: number | null, limit: number | null | undefined): number {
    if (limit === 0) return 100;
    return limit ? Math.min(100, Math.round(((used ?? 0) / limit) * 100)) : 0;
  }

  /** True where a per-head row exists, which is what makes the per-person note relevant. */
  protected hasPerHead(): boolean {
    return bindsEachMember(this.budgets());
  }

  protected labelFor(budget: Budget): string {
    return scopeLabel(budget.scope);
  }
}

/** Empty, or a whole number of at least one. */
function isWholeAtLeastOne(value: number | null): boolean {
  return value == null || (Number.isInteger(value) && value >= 1);
}
