import { Component, computed, inject, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { TestResult, TestRun, TestVerdict } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { PageFeedback } from '../../core/ui/page-feedback';

/**
 * Rating a run's answers, one at a time (`FRD-504`).
 *
 * One answer per window, with previous and next, rather than a table: reading fifty answers in a
 * table is how somebody ends up skimming. The window is always on the page and empty until
 * `open()`, so opening a run again reloads its answers.
 */
@Component({
  selector: 'app-rating-window',
  imports: [FormsModule],
  templateUrl: './rating-window.html',
  host: { style: 'display: contents' },
})
export class RatingWindow {
  /** Raised after a verdict is saved, so the page can refresh the runs and the figures. */
  readonly rated = output<void>();

  private readonly service = inject(UseCaseService);
  private readonly feedback = inject(PageFeedback);

  protected readonly results = signal<TestResult[]>([]);
  /** Index of the answer on screen, or `null` while the window is closed. */
  protected readonly rating = signal<number | null>(null);
  protected readonly note = signal('');

  protected readonly current = computed(() => {
    const index = this.rating();
    return index === null ? null : (this.results()[index] ?? null);
  });

  /**
   * Open a run at the first answer that still needs a verdict.
   *
   * A failed request has nothing to judge and is skipped. When everything is rated the first
   * answer opens anyway, because the reader may have come back to change a verdict.
   */
  open(run: TestRun): void {
    this.rating.set(null);
    this.service.runResults(run.id).subscribe({
      next: (rows) => {
        this.results.set(rows);
        const first = rows.findIndex((row) => row.verdict === 'unrated' && !row.error);
        this.rate(first >= 0 ? first : 0);
      },
      error: (response: unknown) => this.feedback.fail(response, 'Could not load the answers.'),
    });
  }

  protected rate(index: number): void {
    this.rating.set(index);
    this.note.set(this.results()[index]?.note ?? '');
  }

  protected closeRating(): void {
    this.rating.set(null);
    this.results.set([]);
  }

  protected step(by: number): void {
    const index = this.rating();
    if (index === null) return;
    const next = index + by;
    if (next < 0 || next >= this.results().length) return;
    this.rate(next);
  }

  /** Record a verdict and move to the next answer; after the last one the window closes. */
  protected verdict(value: TestVerdict): void {
    const index = this.rating();
    const result = this.current();
    if (index === null || !result) return;

    this.service.updateResult(result.id, { verdict: value, note: this.note() }).subscribe({
      next: (saved) => {
        this.results.update((rows) => rows.map((row) => (row.id === saved.id ? saved : row)));
        this.rated.emit();
        if (index + 1 < this.results().length) this.rate(index + 1);
        else this.closeRating();
      },
      error: (response: unknown) => this.feedback.fail(response, 'Could not save this rating.'),
    });
  }
}
