import { Component, OnInit, inject, input, output, signal } from '@angular/core';
import { AnomalyRule } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { InfoHint } from '../../core/ui/info-hint';
import { PageFeedback } from '../../core/ui/page-feedback';
import { describeRule, unitOf } from '../security/rule-language';
import { NEW_RULE, RuleForm } from '../security/rule-form';

/**
 * The anomaly rules of one use case (`FRD-208`).
 *
 * This screen is here because the IT Security console pointed at it and it did not exist. A
 * use-case rule is editable by whoever manages that use case — the server has always said so
 * (`AnomalyRuleViewSet._guard`, `upsert_use_case_rule`) — and there was nowhere to do it. The
 * console's "changed on that use case" was, until now, an instruction with no destination: exactly
 * the defect `FRD-206` was written about, one level of indirection further out.
 *
 * **Global rules are deliberately absent.** They are not this use case's to change, and listing
 * them here would offer an edit the server refuses. They are on the Security console, where the
 * people who own them are.
 */
@Component({
  selector: 'app-rules-tab',
  imports: [InfoHint, RuleForm],
  templateUrl: './rules-tab.html',
})
export class RulesTab implements OnInit {
  readonly slug = input.required<string>();
  /** Whether this caller may change them — the server's answer, carried on the use case object. */
  readonly canManage = input(false);
  /** So the parent's tab badge does not become a second source of truth. */
  readonly countChanged = output<number>();

  private readonly service = inject(UseCaseService);
  private readonly confirmService = inject(ConfirmService);
  protected readonly feedback = inject(PageFeedback);

  protected readonly rules = signal<AnomalyRule[]>([]);
  protected readonly loading = signal(true);
  /** Which rule is open. `0` is the "new rule" form; `null` is nothing open. */
  protected readonly editing = signal<number | null>(null);
  protected readonly draft = signal<AnomalyRule>(NEW_RULE);

  ngOnInit(): void {
    this.load();
  }

  protected load(): void {
    this.service.useCaseRules(this.slug()).subscribe({
      next: (rules) => {
        this.rules.set(rules);
        this.countChanged.emit(rules.length);
        this.loading.set(false);
      },
      error: (response: unknown) => {
        this.loading.set(false);
        this.feedback.fail(response, 'Could not load the anomaly rules of this use case.');
      },
    });
  }

  protected explain(rule: AnomalyRule): string {
    return describeRule(rule);
  }

  protected unit(rule: AnomalyRule): string {
    return unitOf(rule.kind);
  }

  protected add(): void {
    this.draft.set(NEW_RULE);
    this.editing.set(0);
  }

  protected edit(rule: AnomalyRule): void {
    this.draft.set(rule);
    this.editing.set(rule.id);
  }

  protected cancel(): void {
    this.editing.set(null);
  }

  protected save(changes: Partial<AnomalyRule>): void {
    const creating = this.editing() === 0;
    this.feedback.run(this.service.saveUseCaseRule(this.slug(), changes), {
      failure: creating ? 'Could not create this rule.' : 'Could not save this rule.',
      success: () => {
        this.feedback.succeed(
          `"${changes.name}" ${creating ? 'created' : 'saved'}. It reaches the gateway within a few seconds.`,
        );
        this.editing.set(null);
        this.load();
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
        this.load();
      },
    });
  }
}
