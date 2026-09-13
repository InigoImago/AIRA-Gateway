import { Component, computed, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { TestCase } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { InfoHint } from '../../core/ui/info-hint';
import { PageFeedback } from '../../core/ui/page-feedback';

/**
 * The question catalogue: the standing set of questions every run asks (`FRD-504`).
 *
 * One flat, ordered list — grouping would give "how does this use case do" one answer per group.
 * Authoring is IT Security's, like a global anomaly rule, because it states what this installation
 * considers acceptable. The panel stays alive while another tab is open (`shown`), so a search and
 * an open editor survive a tab switch.
 */
@Component({
  selector: 'app-question-catalogue',
  imports: [FormsModule, InfoHint],
  templateUrl: './question-catalogue.html',
  host: { style: 'display: contents' },
})
export class QuestionCatalogue {
  /** The catalogue the page loaded; the page owns it because its tab count reads from it. */
  readonly cases = input<TestCase[]>([]);
  readonly mayAuthor = input(false);
  /** Whether the catalogue tab is the open one. */
  readonly shown = input(false);
  /** Raised after a question is saved or retired, so the page reloads the catalogue. */
  readonly changed = output<void>();

  private readonly service = inject(UseCaseService);
  private readonly confirmService = inject(ConfirmService);
  private readonly feedback = inject(PageFeedback);

  protected readonly search = signal('');
  /**
   * The catalogue in `position` order, filtered by topic and wording.
   *
   * Filtered in the browser: a hundred rows is not a paging problem, and the count the screen
   * states is over the whole catalogue (the same decision as the model catalog, `FRD-208`).
   */
  protected readonly visible = computed(() => {
    const needle = this.search().trim().toLowerCase();
    const rows = [...this.cases()].sort((a, b) => a.position - b.position);
    if (!needle) return rows;
    return rows.filter(
      (row) =>
        row.topic.toLowerCase().includes(needle) || row.prompt.toLowerCase().includes(needle),
    );
  });

  // The question being written.
  protected readonly editing = signal<Partial<TestCase> | null>(null);
  protected readonly caseTopic = signal('');
  protected readonly casePrompt = signal('');
  protected readonly caseExpectation = signal('');

  protected startCase(item?: TestCase): void {
    this.editing.set(item ? { ...item } : {});
    this.caseTopic.set(item?.topic ?? '');
    this.casePrompt.set(item?.prompt ?? '');
    this.caseExpectation.set(item?.expectation ?? '');
  }

  protected cancelCase(): void {
    this.editing.set(null);
  }

  protected saveCase(): void {
    const draft = this.editing();
    if (!draft) return;

    const body = {
      topic: this.caseTopic().trim(),
      prompt: this.casePrompt().trim(),
      expectation: this.caseExpectation().trim(),
      // A new question is appended: a position chosen on every question is a field people get
      // wrong, and a catalogue is added to far more often than it is reordered.
      position: draft.id ? (draft.position ?? 0) : this.cases().length + 1,
    };
    const request = draft.id
      ? this.service.updateCase(draft.id, body)
      : this.service.createCase(body);

    request.subscribe({
      next: () => {
        this.editing.set(null);
        this.feedback.succeed(draft.id ? 'Question saved.' : 'Question added to the catalogue.');
        this.changed.emit();
      },
      error: (response: unknown) => this.feedback.fail(response, 'Could not save this question.'),
    });
  }

  /**
   * Take a question out of the catalogue and keep every answer already given to it.
   *
   * Retired, never deleted: a verdict was formed against this wording, and the server refuses to
   * delete an answered question (`TestResult.case` is `PROTECT`).
   */
  protected retireCase(item: TestCase): void {
    const question =
      `Retire "${item.topic}"? It leaves the catalogue and stops being asked. ` +
      'Answers already given to it are kept, with the wording they were judged against.';
    if (!this.confirmService.ask(question)) return;
    this.service.updateCase(item.id, { retired: true }).subscribe({
      next: () => {
        this.feedback.succeed('Question retired. Its answers are kept.');
        this.changed.emit();
      },
      error: (response: unknown) => this.feedback.fail(response, 'Could not retire this question.'),
    });
  }
}
