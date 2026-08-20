import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Budget } from '../../core/api/models';
import { MeService } from '../../core/api/me.service';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { InfoHint } from '../../core/ui/info-hint';
import { Modal } from '../../core/ui/modal';
import { PageFeedback } from '../../core/ui/page-feedback';

/**
 * The cap on spend that belongs to no use case (`FRD-610`).
 *
 * It lives on the reporting page because that is where the figure it bounds already appears: an
 * oversight reader's `By use case` table carries a `(none)` row, and until this existed there was
 * nothing anywhere that could limit it. Every other budget in this console is reached through the
 * use case it belongs to; this one has none, which is the entire point.
 *
 * **Read is wider than write.** `IT Steuerung` oversees and acts in nothing (`ADR-0007`), so it
 * sees the figure and is offered no form; a Global Administrator sets it. The server enforces
 * both — this decides only what to offer.
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

  protected readonly budgets = signal<Budget[]>([]);
  protected readonly loaded = signal(false);
  private readonly roles = signal<string[]>([]);

  // Zoneless: form state is signals, or setting one from code renders nothing.
  protected readonly showForm = signal(false);
  protected readonly period = signal<'day' | 'month'>('month');
  /** Kept as text: a spend limit must not round-trip through a JS number. */
  protected readonly cost = signal('');
  protected readonly tokens = signal<number | null>(null);
  protected readonly requests = signal<number | null>(null);

  protected readonly canManage = computed(() => this.roles().includes('global-admin'));

  /**
   * Whether to draw the card at all.
   *
   * The server answers an empty list to a reader with no oversight role, so an empty list is not
   * proof that nothing is configured — it can also mean *not your business*. Both look the same
   * from here, and a card reading "no installation budget set" shown to a use-case user would be a
   * claim this component cannot make.
   */
  protected readonly visible = computed(() => this.canManage() || this.budgets().length > 0);

  ngOnInit(): void {
    this.meService.get().subscribe({
      next: (me) => this.roles.set(me.roles ?? []),
      // Deliberately quiet: failing to learn the reader's roles costs them a form, not a figure,
      // and the page's one banner belongs to the report it is named after.
      error: () => this.roles.set([]),
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
    if (cost && !/^\d+([.,]\d{1,6})?$/.test(cost)) {
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
        // Stated rather than defaulted: this same call edits an existing row, and a body silent
        // about `enabled` used to re-arm a budget somebody had deliberately lifted.
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
   * Lift a budget without losing it, or put it back.
   *
   * The whole row goes back because the endpoint upserts on the period: a body carrying only the
   * switch would blank the limits beside it.
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
    // `canManage()` here as well as on the button, and as `setEnabled` already had it. The server
    // refuses either way, so this is not the lock — it is the difference between a no-op and a red
    // banner for a reader who was never offered the control.
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
