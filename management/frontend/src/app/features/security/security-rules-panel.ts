import { Component, inject, input, output, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { AnomalyRule } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { Modal } from '../../core/ui/modal';
import { PageFeedback } from '../../core/ui/page-feedback';
import { TablePager } from '../../core/ui/table-pager';
import { TableView } from '../../core/ui/table-view';
import { NEW_RULE, RuleForm } from './rule-form';
import { describeRule, unitOf } from './rule-language';

/**
 * The anomaly rules this caller can see, and authoring the ones that apply everywhere (`FRD-500`).
 *
 * A global rule is IT Security's to author; a use-case rule is changed on its use case, and this
 * panel links there. The page loads the rules, because its tab count needs them before this tab is
 * opened. This panel owns the open row, the forms and the mutations, reports through the page's
 * `PageFeedback`, and raises `changed` so the page reloads.
 */
@Component({
  selector: 'app-security-rules-panel',
  imports: [RouterLink, Modal, RuleForm, TablePager],
  templateUrl: './security-rules-panel.html',
  host: { style: 'display: contents' },
})
export class SecurityRulesPanel {
  /**
   * Whether this panel's tab is the open one. The panel stays on the page either way, so a
   * half-typed form survives a look at another tab.
   */
  readonly shown = input(false);
  readonly rules = input<AnomalyRule[]>([]);
  /** Whether this caller holds `anomaly.rule.global.write`, which authoring a global rule needs. */
  readonly canWriteGlobalRules = input(false);
  /** Raised after a create, a save or a delete, so the page reloads the rules it owns. */
  readonly changed = output<void>();

  private readonly service = inject(UseCaseService);
  private readonly confirmService = inject(ConfirmService);
  protected readonly feedback = inject(PageFeedback);

  /** The rule whose detail row is open: its sentence and fields are more than a table row holds. */
  protected readonly openRule = signal<number | null>(null);
  /** Which rule's form is open. The form itself owns the field values (`rule-form.ts`). */
  protected readonly editing = signal<number | null>(null);
  /** A global rule being authored, or `null` when nothing is being created. */
  protected readonly draft = signal<AnomalyRule | null>(null);

  /**
   * Paged in the browser (`FRD-505` FR-12), so the tab's count stays a count of every rule rather
   * than of one page (`FRD-208`). A global rule is found by "everywhere", the word its row shows.
   */
  protected readonly ruleView = new TableView<AnomalyRule>(this.rules, (rule) =>
    [rule.name, rule.kind, rule.use_case ?? 'everywhere', rule.action].join(' '),
  );

  /** What a rule does, in a sentence — see `rule-language.ts` for why this is not the raw kind. */
  protected explain(rule: AnomalyRule): string {
    return describeRule(rule);
  }

  /** What a threshold is counted in — "% of requests" reads very differently from "× as much". */
  protected unit(rule: AnomalyRule): string {
    return unitOf(rule.kind);
  }

  /**
   * Whether this caller may change this rule.
   *
   * A global rule needs `anomaly.rule.global.write`, the permission the server enforces. A
   * use-case rule needs to manage that use case, which is object-level and not in `/me`, so rather
   * than guess the panel says where it is edited (`FRD-206`).
   */
  protected mayEdit(rule: AnomalyRule): boolean {
    return rule.is_global && this.canWriteGlobalRules();
  }

  protected toggleRule(id: number): void {
    this.openRule.update((open) => (open === id ? null : id));
    // Closing a rule abandons its edit, so a hidden form never saves fields nobody can see.
    if (this.openRule() !== id) this.editing.set(null);
  }

  protected startCreate(): void {
    this.editing.set(null);
    this.draft.set(NEW_RULE);
  }

  protected cancelCreate(): void {
    this.draft.set(null);
  }

  protected createRule(changes: Partial<AnomalyRule>): void {
    this.feedback.run(this.service.createGlobalRule(changes), {
      failure: 'Could not create this rule.',
      success: () => {
        this.feedback.succeed(
          `"${changes.name}" now applies to every use case. It reaches the gateway within a few ` +
            `seconds.`,
        );
        this.draft.set(null);
        this.changed.emit();
      },
    });
  }

  protected startEdit(rule: AnomalyRule): void {
    this.editing.set(rule.id);
  }

  protected cancelEdit(): void {
    this.editing.set(null);
  }

  protected saveRule(rule: AnomalyRule, changes: Partial<AnomalyRule>): void {
    this.feedback.run(this.service.updateRule(rule.id, changes), {
      failure: 'Could not save this rule.',
      success: () => {
        this.feedback.succeed(`"${rule.name}" saved. It reaches the gateway within a few seconds.`);
        this.editing.set(null);
        this.changed.emit();
      },
    });
  }

  protected removeRule(rule: AnomalyRule): void {
    const question =
      `Delete the rule "${rule.name}"? Nothing will be watched for it afterwards, and ` +
      `findings it already produced are kept.`;
    if (!this.confirmService.ask(question)) return;
    this.feedback.run(this.service.deleteRule(rule.id), {
      failure: 'Could not delete this rule.',
      success: () => {
        this.feedback.succeed(`"${rule.name}" deleted.`);
        this.openRule.set(null);
        this.editing.set(null);
        this.changed.emit();
      },
    });
  }
}
