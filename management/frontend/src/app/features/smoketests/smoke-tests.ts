import { DatePipe } from '@angular/common';
import { Component, OnInit, computed, inject, signal, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';
import { MeService } from '../../core/api/me.service';
import { can } from '../../core/auth/roles';
import {
  TestAttribution,
  TestCase,
  TestModelStats,
  TestResult,
  TestRun,
} from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { InfoHint } from '../../core/ui/info-hint';
import { PageFeedback } from '../../core/ui/page-feedback';
import { QuestionCatalogue } from './question-catalogue';
import { RatingWindow } from './rating-window';
import { csvFileName, describeFailure } from './run-results';

/**
 * The question catalogue put to a use case's pipeline, and the answers rated by a person
 * (`FRD-504`, `ADR-0020`).
 *
 * - A run is about a **use case**: it travels that use case's own pipeline, so a blocked question
 *   is a result, not a broken run. Testing a model is a use case whose pipeline starts there.
 * - It travels the ordinary request path with the reader's own credentials, so it is priced,
 *   budgeted, rate-limited and audited like any other traffic.
 * - Nothing here decides whether an answer is good: a person rates each one, under their name.
 * - Results show the **latest** run per use case, never a sum across runs; earlier runs stay
 *   readable as history.
 *
 * The page loads what the tab bar counts and owns the run launcher; the catalogue and the rating
 * window are panels.
 */
@Component({
  selector: 'app-smoke-tests',
  imports: [DatePipe, FormsModule, InfoHint, QuestionCatalogue, RatingWindow],
  templateUrl: './smoke-tests.html',
  providers: [PageFeedback],
})
export class SmokeTests implements OnInit {
  private readonly service = inject(UseCaseService);
  private readonly meService = inject(MeService);
  protected readonly feedback = inject(PageFeedback);
  private readonly ratingWindow = viewChild.required(RatingWindow);

  private readonly me = signal<{ permissions?: string[] } | null>(null);
  /** Authoring the catalogue needs `smoketest.author`, the permission the server enforces. */
  protected readonly mayAuthor = computed(() => can(this.me(), 'smoketest.author'));

  protected readonly tab = signal<'results' | 'runs' | 'catalogue'>('results');
  protected readonly cases = signal<TestCase[]>([]);
  protected readonly runs = signal<TestRun[]>([]);
  protected readonly stats = signal<TestModelStats[]>([]);
  protected readonly loading = signal(true);
  /**
   * The server refused the screen itself — an answer, not a failure.
   *
   * Running the catalogue needs administration of a use case. The nav hides the entry, but an
   * address can be typed, so the page then names who runs the catalogue instead of showing tabs
   * whose every control would refuse (`FRD-206`).
   */
  protected readonly withheld = signal(false);

  /**
   * The use cases this caller may put the catalogue to, answered by the server (`ADR-0020`):
   * complete, and already narrowed to what the gateway would accept from this caller and what has
   * a pipeline to run. Never a list this screen filters.
   */
  protected readonly runnableUseCases = signal<TestAttribution[]>([]);
  /** Whether that answer has arrived: unknown is never rendered as "none" (`LESSONS.md` §6). */
  protected readonly attributionKnown = signal(false);
  protected readonly useCase = signal('');
  /** The row for the chosen use case, or `null` while none is chosen. */
  protected readonly chosen = computed(
    () => this.runnableUseCases().find((row) => row.use_case === this.useCase()) ?? null,
  );
  /**
   * Whether this caller may run the chosen use case: the server's `may_run`, which asks whether
   * the gateway accepts the caller and whether the pipeline can be entered (`FRD-206`).
   */
  protected readonly mayRun = computed(() => this.chosen()?.may_run === true);
  /**
   * The models a run may enter at: what the chosen use case has been released (`FRD-308`). The
   * gateway refuses anything else at dispatch, so offering more would offer a run of 403s.
   */
  protected readonly entryModels = computed(() => this.chosen()?.models ?? []);
  /**
   * The model this run enters at — chosen per run, not declared on the pipeline: a use case
   * releases several models on purpose, and comparing two runs at two models is the point.
   */
  protected readonly startModel = signal('');
  /** Why the chosen use case cannot be run, in the server's words; shown where Run would be. */
  protected readonly whyNot = computed(() => this.chosen()?.why_not ?? '');
  protected readonly running = signal(false);
  /** How far a run has got, so a hundred questions do not look frozen. */
  protected readonly progress = signal('');

  ngOnInit(): void {
    this.meService.get().subscribe({ next: (me) => this.me.set(me), error: () => undefined });
    this.load();
  }

  protected load(): void {
    this.loading.set(true);
    this.service.testCases().subscribe({
      next: (rows) => {
        this.cases.set(rows);
        this.loading.set(false);
      },
      error: (response: unknown) => {
        this.loading.set(false);
        if ((response as { status?: number })?.status === 403) {
          this.withheld.set(true);
          return;
        }
        this.feedback.fail(response, 'Could not load the question catalogue.');
      },
    });
    this.service.testAttribution().subscribe({
      next: (rows) => {
        this.runnableUseCases.set(rows);
        this.attributionKnown.set(true);
        // Preselect only when there is nothing to choose: a run costs money, so picking one of
        // several pipelines for somebody is not this screen's call.
        if (rows.length === 1) this.chooseUseCase(rows[0].use_case);
      },
      error: (response: unknown) => {
        // A failed answer is still an answer; the panel must not stay "working it out" forever.
        this.attributionKnown.set(true);
        this.feedback.fail(response, 'Could not work out where the catalogue could be run.');
      },
    });
    this.refreshRuns();
  }

  /**
   * The run history and the per-use-case figures.
   *
   * Failures go to the page banner: an empty history that says nothing reads as "nothing has been
   * run", which is a false statement on the tab whose purpose is the history.
   */
  protected refreshRuns(): void {
    this.service.testRuns().subscribe({
      next: (rows) => this.runs.set(rows),
      error: (response: unknown) => this.feedback.fail(response, 'Could not load the runs.'),
    });
    this.service.testStats().subscribe({
      next: (rows) => this.stats.set(rows),
      error: (response: unknown) =>
        this.feedback.fail(response, 'Could not load the figures per model.'),
    });
  }

  protected reloadCatalogue(): void {
    this.service.testCases().subscribe({
      next: (rows) => this.cases.set(rows),
      error: (response: unknown) => this.feedback.fail(response, 'Could not reload the catalogue.'),
    });
  }

  /**
   * Choose a use case and default the entry model with it: a model kept from the previous use case
   * may not be released to the new one, and the server would refuse it.
   */
  protected chooseUseCase(slug: string): void {
    this.useCase.set(slug);
    this.startModel.set(this.entryModels()[0] ?? '');
  }

  /**
   * Run the catalogue through the chosen use case, one prompt at a time.
   *
   * Sequential on purpose: firing a hundred at once would trip the use case's own rate limit and
   * fill the run with 429s that say nothing about the pipeline.
   */
  protected async run(): Promise<void> {
    if (!this.useCase() || !this.mayRun() || this.running()) return;

    this.running.set(true);
    this.feedback.clear();
    try {
      const run = await firstValueFrom(this.service.startRun(this.useCase(), this.startModel()));
      const results = await firstValueFrom(this.service.runResults(run.id));

      for (const [index, result] of results.entries()) {
        this.progress.set(`${index + 1} of ${results.length}`);
        await this.ask(result);
      }

      await firstValueFrom(this.service.finishRun(run.id));
      this.progress.set('');
      this.refreshRuns();
      this.open(run);
      this.feedback.succeed(
        `${results.length} answer(s) through ${run.use_case}, entering at ${run.model}. ` +
          'Nothing is rated yet — that is the next step.',
      );
    } catch (error) {
      this.feedback.fail(error, 'The run could not be completed.');
      this.progress.set('');
    } finally {
      this.running.set(false);
    }
  }

  /**
   * One prompt through the gateway, with whatever came back written to the result.
   *
   * A failed request is stored as `error`, apart from `response`, so the statistics keep an
   * outage apart from a bad answer.
   */
  private async ask(result: TestResult): Promise<void> {
    const started = Date.now();
    try {
      const answer = await firstValueFrom(
        this.service.askModel(this.startModel(), result.prompt, this.useCase()),
      );
      await firstValueFrom(
        this.service.updateResult(result.id, {
          response: answer,
          latency_ms: Date.now() - started,
        }),
      );
    } catch (error) {
      await firstValueFrom(
        this.service.updateResult(result.id, {
          error: describeFailure(error),
          latency_ms: Date.now() - started,
        }),
      );
    }
  }

  protected open(run: TestRun): void {
    this.ratingWindow().open(run);
  }

  /**
   * Whether this run is its use case's current standing — read from the same rows as the results
   * tab, so there is one definition of "latest".
   */
  protected isLatest(run: TestRun): boolean {
    return this.stats().some((row) => row.run === run.id);
  }

  /**
   * Download the CSV through the API client: a plain link carries no bearer token and would get a
   * 401 (the same reason `FRD-602`'s download is a blob).
   */
  protected download(run: TestRun): void {
    this.service.testRunCsv(run.id).subscribe({
      next: (blob) => {
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = csvFileName(run);
        link.click();
        URL.revokeObjectURL(url);
      },
      error: (response: unknown) => this.feedback.fail(response, 'Could not export this run.'),
    });
  }
}
