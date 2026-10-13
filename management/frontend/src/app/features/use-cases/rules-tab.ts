import { Component, inject, input, output, signal } from '@angular/core';
import { AnomalyRule } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { InfoHint } from '../../core/ui/info-hint';
import { Modal } from '../../core/ui/modal';
import { PageFeedback } from '../../core/ui/page-feedback';
import { describeRule, thresholdText } from '../security/rule-language';
import { NEW_RULE, RuleForm } from '../security/rule-form';

/**
 * The anomaly rules of one use case (`FRD-208`), editable by whoever manages it — the server's
 * rule (`AnomalyRuleViewSet._guard`, `upsert_use_case_rule`).
 *
 * Global rules are deliberately absent: they are not this use case's to change, and listing them
 * would offer an edit the server refuses. They live on the Security console.
 *
 * The page loads the list, because its tab bar counts it before this tab is opened; this tab owns
 * the forms and the mutations and raises `changed` for the page to read the list again.
 */
@Component({
  selector: 'app-rules-tab',
  imports: [InfoHint, RuleForm, Modal],
  templateUrl: './rules-tab.html',
})
export class RulesTab {
  readonly slug = input.required<string>();
  /** Whether this caller may change them — the server's answer, carried on the use case object. */
  readonly canManage = input(false);
  /** The rules the page loaded; `null` until they arrive, or while they could not be loaded. */
  readonly rules = input<AnomalyRule[] | null>(null);
  /** The load failed: the page has said why, and this tab must not say "nothing is watched". */
  readonly failed = input(false);
  /** Raised after a rule is created, changed or deleted, so the page reads the list again. */
  readonly changed = output<void>();

  private readonly service = inject(UseCaseService);
  private readonly confirmService = inject(ConfirmService);
  protected readonly feedback = inject(PageFeedback);

  /** Which rule is open. `0` is the "new rule" form; `null` is nothing open. */
  protected readonly editing = signal<number | null>(null);
  protected readonly draft = signal<AnomalyRule>(NEW_RULE);

  protected explain(rule: AnomalyRule): string {
    return describeRule(rule);
  }

  /** A threshold in the unit a person reads it in — a spike as "3× the previous window", never as
   *  the stored "300" — worded by the one function the security console uses too. */
  protected threshold(rule: AnomalyRule): string {
    return thresholdText(rule.kind, rule.threshold);
  }

  /** Open a form afresh: an earlier failure on the page is not this form's outcome. */
  protected add(): void {
    this.feedback.clear();
    this.draft.set(NEW_RULE);
    this.editing.set(0);
  }

  protected edit(rule: AnomalyRule): void {
    this.feedback.clear();
    this.draft.set(rule);
    this.editing.set(rule.id);
  }

  protected cancel(): void {
    this.editing.set(null);
  }

  /**
   * Create or change a rule. The server upserts **by name**, so a new rule named like an existing
   * one replaces it — kind, threshold, action and all. That is asked first and said afterwards,
   * never done under the word "created".
   */
  protected save(changes: Partial<AnomalyRule>): void {
    const creating = this.editing() === 0;
    const replaced = creating
      ? (this.rules() ?? []).find((rule) => rule.name === changes.name)
      : undefined;
    if (
      replaced &&
      !this.confirmService.ask(
        `A rule named "${replaced.name}" already exists (${replaced.kind}, ${replaced.action}). ` +
          `Saving replaces it with this one — its kind, threshold and action included.`,
      )
    ) {
      return;
    }
    const outcome = replaced ? 'replaced' : creating ? 'created' : 'saved';
    this.feedback.run(this.service.saveUseCaseRule(this.slug(), changes), {
      failure: creating ? 'Could not create this rule.' : 'Could not save this rule.',
      success: () => {
        this.feedback.succeed(
          `"${changes.name}" ${outcome}. It reaches the gateway within a few seconds.`,
        );
        this.editing.set(null);
        this.changed.emit();
      },
    });
  }

  protected remove(rule: AnomalyRule): void {
    const question =
      `Delete "${rule.name}"? Nothing will be watched for it afterwards. Findings it has ` +
      `already produced are kept.`;
    if (!this.confirmService.ask(question)) return;
    this.feedback.run(this.service.deleteUseCaseRule(this.slug(), rule.id), {
      failure: 'Could not delete this rule.',
      success: () => {
        this.feedback.succeed(`"${rule.name}" deleted.`);
        this.editing.set(null);
        this.changed.emit();
      },
    });
  }
}
