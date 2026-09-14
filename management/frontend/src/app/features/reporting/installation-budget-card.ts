import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Budget } from '../../core/api/models';
import { MeService } from '../../core/api/me.service';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { InfoHint } from '../../core/ui/info-hint';
import { Modal } from '../../core/ui/modal';
import { PageFeedback } from '../../core/ui/page-feedback';
import { can } from '../../core/auth/roles';

/** A spend limit as typed: an amount with up to six decimals, either separator. */
const AMOUNT = /^\d+([.,]\d{1,6})?$/;

/**
 * The cap on spend that belongs to no use case (`FRD-610`) — the `(none)` row of the report's
 * `By use case` table, which is why it lives on this page.
 *
 * Read is wider than write: whoever reads every figure sees it (`ADR-0007`), and only
 * `budget.installation.write` is offered the form. The server enforces both — this decides only
 * what to offer.
 */
@Component({
  selector: 'app-installation-budget-card',
  imports: [FormsModule, InfoHint, Modal],
  templateUrl: './installation-budget-card.html',
})
export class InstallationBudgetCard implements OnInit {
  private readonly service = inject(UseCaseService);
  private readonly meService = inject(MeService);
  private readonly confirmService = inject(ConfirmService);
  /** The page's single banner, not one of this panel's own. */
  protected readonly feedback = inject(PageFeedback);
  protected readonly currency = this.meService.currency;

  protected readonly budgets = signal<Budget[]>([]);
  protected readonly loaded = signal(false);
  private readonly me = signal<{ permissions?: string[] } | null>(null);

  protected readonly showForm = signal(false);
  protected readonly period = signal<'day' | 'month'>('month');
  /** Kept as text: a spend limit must not round-trip through a JS number. */
  protected readonly cost = signal('');
  protected readonly tokens = signal<number | null>(null);
  protected readonly requests = signal<number | null>(null);

  /**
   * The unit in the label, or nothing. Built here as one string: interpolated across a control-flow
   * block in the template, it picks up the block's whitespace (`Spend limit  (CHF)`).
   */
  protected readonly costUnit = computed(() => {
    const unit = this.currency();
    return unit ? ` (${unit})` : '';
  });

  protected readonly canManage = computed(() => can(this.me(), 'budget.installation.write'));

  /**
   * Whether to draw the card at all. The server answers an empty list to a reader who may not read
   * every figure, so an empty list may mean *not your business* — never claim "no budget set" to a
   * reader who cannot manage one.
   */
  protected readonly visible = computed(() => this.canManage() || this.budgets().length > 0);

  ngOnInit(): void {
    this.meService.get().subscribe({
      next: (me) => this.me.set(me),
      // Quiet: not knowing the permissions costs a form, not a figure, and the page's banner
      // belongs to the report.
      error: () => this.me.set(null),
    });
    this.load();
  }

  protected load(): void {
    this.service.installationBudgets().subscribe({
      next: (rows) => {
        this.budgets.set(rows);
        this.loaded.set(true);
      },
      error: (response: unknown) =>
        this.feedback.fail(response, 'Could not load the installation budget.'),
    });
  }

  protected validationError(): string | null {
    const cost = this.cost().trim();
    if (cost && !AMOUNT.test(cost)) {
      return 'The spend limit must be an amount, e.g. 250 or 250.00.';
    }
    if (!cost && this.tokens() == null && this.requests() == null) {
      return 'Set a spend limit, a token limit, or a request limit.';
    }
    return null;
  }

  protected canSave(): boolean {
    return !this.validationError() && !this.feedback.busy();
  }

  protected save(): void {
    if (!this.canSave()) return;
    const cost = this.cost().trim().replace(',', '.');
    this.feedback.run(
      this.service.saveInstallationBudget({
        scope: 'installation',
        subject: '',
        period: this.period(),
        limit_cost: cost || null,
        limit_tokens: this.tokens(),
        limit_requests: this.requests(),
        // Stated, not defaulted: this call also edits an existing row, and silence about
        // `enabled` would re-arm a budget somebody deliberately lifted.
        enabled: true,
      }),
      {
        failure: 'Could not save the installation budget.',
        success: () => {
          this.feedback.succeed('Installation budget saved.');
          this.cost.set('');
          this.tokens.set(null);
          this.requests.set(null);
          this.showForm.set(false);
          this.load();
        },
      },
    );
  }

  /**
   * Lift a budget without losing it, or put it back. The whole row is sent because the endpoint
   * upserts on the period: a body carrying only the switch would blank the limits beside it.
   */
  protected setEnabled(budget: Budget, enabled: boolean): void {
    if (!this.canManage() || this.feedback.busy()) return;
    this.feedback.run(this.service.saveInstallationBudget({ ...budget, enabled }), {
      failure: enabled ? 'Could not enable the budget.' : 'Could not disable the budget.',
      success: () => {
        this.feedback.succeed(
          enabled
            ? 'Installation budget enabled. Unattributed spending is capped again.'
            : 'Installation budget disabled. It is kept on record and stops binding.',
        );
        this.load();
      },
    });
  }

  protected remove(id: number | undefined): void {
    // `canManage()` is not the lock — the server refuses anyway — but it makes a call from a reader
    // who was never offered the control a no-op rather than a red banner.
    if (
      id == null ||
      !this.canManage() ||
      this.feedback.busy() ||
      !this.confirmService.ask(
        'Remove this installation budget? Spend that belongs to no use case stops being capped.',
      )
    ) {
      return;
    }
    this.feedback.run(this.service.deleteInstallationBudget(id), {
      failure: 'Could not remove the installation budget.',
      success: () => {
        this.feedback.succeed('Installation budget removed.');
        this.load();
      },
    });
  }

  /** What this row limits, in words, so a card never shows a bare number with no unit. */
  protected limitsOf(budget: Budget): string[] {
    const out: string[] = [];
    if (budget.limit_cost) out.push(`$${budget.limit_cost}`);
    if (budget.limit_tokens != null) out.push(`${budget.limit_tokens} tokens`);
    if (budget.limit_requests != null) out.push(`${budget.limit_requests} request(s)`);
    return out;
  }
}
