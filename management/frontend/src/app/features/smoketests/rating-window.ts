import { Component, computed, inject, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Subscription } from 'rxjs';
import { TestResult, TestRun, TestVerdict } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { Modal } from '../../core/ui/modal';
import { PageFeedback } from '../../core/ui/page-feedback';
import { neverAsked } from './run-results';

/**
 * Rating a run's answers, one at a time (`FRD-504`).
 *
 * One answer per window, with previous and next, rather than a table: reading fifty answers in a
 * table is how somebody ends up skimming. The window is always on the page and empty until
 * `open()`, so opening a run again reloads its answers.
 */
@Component({
  selector: 'app-rating-window',
  imports: [FormsModule, Modal],
  templateUrl: './rating-window.html',
  host: { style: 'display: contents' },
})
export class RatingWindow {
  /** Raised after a verdict is saved, so the page can refresh the runs and the figures. */
  readonly rated = output<void>();

  private readonly service = inject(UseCaseService);
  protected readonly feedback = inject(PageFeedback);

  protected readonly results = signal<TestResult[]>([]);
  /** Index of the answer on screen, or `null` while the window is closed. */
  protected readonly rating = signal<number | null>(null);
  protected readonly note = signal('');
  /**
   * What has been typed per answer and not yet saved, by result id.
   *
   * Only a verdict saves a note, and stepping to the next answer to compare is ordinary — so a note
   * is kept per answer for as long as the window is open, rather than living in the one box and
   * being replaced by the next answer's.
   */
  private readonly drafts = signal<Record<number, string>>({});
  /** Set while a verdict is being saved: the window moves on when it lands, so a second click
   *  would otherwise rate the next answer, one its rater has not read. */
  protected readonly saving = signal(false);
  /**
   * Whether a verdict has been tried since the window opened. The page's banner is behind the
   * backdrop, so a refusal is repeated in the window — and only one that happened here, or an
   * older page error would read as a statement about this answer.
   */
  protected readonly tried = signal(false);

  protected readonly current = computed(() => {
    const index = this.rating();
    return index === null ? null : (this.results()[index] ?? null);
  });
  /** Whether the answer on screen is a question the run never put to the model. */
  protected readonly unasked = computed(() => {
    const row = this.current();
    return row !== null && neverAsked(row);
  });

  /** The answers being loaded, so opening a second run drops the first one's late reply. */
  private inFlight: Subscription | null = null;

  /**
   * Open a run at the first answer that still needs a verdict.
   *
   * A failed request has nothing to judge and is skipped, and so is a question the run never
   * asked. When nothing is left to rate the first answer opens anyway, because the reader may have
   * come back to change a verdict.
   */
  open(run: TestRun): void {
    this.inFlight?.unsubscribe();
    this.rating.set(null);
    this.drafts.set({});
    this.tried.set(false);
    this.inFlight = this.service.runResults(run.id).subscribe({
      next: (rows) => {
        this.results.set(rows);
        const first = rows.findIndex(
          (row) => row.verdict === 'unrated' && !row.error && !neverAsked(row),
        );
        this.rate(first >= 0 ? first : 0);
      },
      error: (response: unknown) => this.feedback.fail(response, 'Could not load the answers.'),
    });
  }

  protected rate(index: number): void {
    this.rating.set(index);
    const row = this.results()[index];
    this.note.set((row && this.drafts()[row.id]) ?? row?.note ?? '');
  }

  /** The note as typed, kept against the answer it is about. */
  protected writeNote(text: string): void {
    this.note.set(text);
    const row = this.current();
    if (row) this.drafts.update((drafts) => ({ ...drafts, [row.id]: text }));
  }

  protected closeRating(): void {
    this.inFlight?.unsubscribe();
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
    if (index === null || !result || this.saving() || neverAsked(result)) return;

    this.saving.set(true);
    this.tried.set(true);
    this.service.updateResult(result.id, { verdict: value, note: this.note() }).subscribe({
      next: (saved) => {
        this.saving.set(false);
        this.results.update((rows) => rows.map((row) => (row.id === saved.id ? saved : row)));
        this.drafts.update((drafts) => {
          const kept = { ...drafts };
          delete kept[saved.id];
          return kept;
        });
        this.rated.emit();
        // Moved on only from the answer that was rated: the window may have been closed, or another
        // run opened, while the save was on its way.
        if (this.current()?.id !== saved.id) return;
        if (index + 1 < this.results().length) this.rate(index + 1);
        else this.closeRating();
      },
      error: (response: unknown) => {
        this.saving.set(false);
        this.feedback.fail(response, 'Could not save this rating.');
      },
    });
  }
}
