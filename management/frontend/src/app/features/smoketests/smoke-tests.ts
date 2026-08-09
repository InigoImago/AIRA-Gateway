import { DatePipe } from '@angular/common';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';
import { MeService } from '../../core/api/me.service';
import {
  CatalogModel,
  TestBattery,
  TestModelStats,
  TestResult,
  TestRun,
  TestVerdict,
} from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { InfoHint } from '../../core/ui/info-hint';
import { PageFeedback } from '../../core/ui/page-feedback';

/**
 * Putting a battery of questions to a model, and reading what came back (`FRD-504`).
 *
 * Every other control in AIRA governs *access*. None of them says anything about what the model
 * actually answers — and "is this model fit for this use case" is a question somebody has to be
 * able to answer with evidence rather than with a vendor's benchmark.
 *
 * **The run travels the ordinary request path.** The console sends each prompt through the gateway
 * with the signed-in person's own credentials, so a smoke test is priced, budgeted, rate-limited
 * and audited exactly like any other traffic. A test harness that bypassed the gateway would be
 * measuring a path nobody uses.
 *
 * **Nothing here decides whether an answer is good.** There is no expected-substring match: whether
 * an answer is acceptable is a judgement, and a regex pretending otherwise produces a number nobody
 * trusts and everybody quotes. A person rates each answer, and the rating carries their name.
 *
 * The list deliberately shows **topic and prompt and not the answer**. Reading fifty answers in a
 * table is how somebody ends up skimming; the rating window shows one at a time, with everything
 * about it, and moves with previous and next.
 */
@Component({
  selector: 'app-smoke-tests',
  imports: [DatePipe, FormsModule, InfoHint],
  templateUrl: './smoke-tests.html',
  providers: [PageFeedback],
})
export class SmokeTests implements OnInit {
  private readonly service = inject(UseCaseService);
  private readonly meService = inject(MeService);
  protected readonly feedback = inject(PageFeedback);

  private readonly me = signal<{ roles: string[] } | null>(null);
  /**
   * Whether this caller can start a run at all — which is **membership**, not a role.
   *
   * The first version asked for an incident role, and the feature was unusable: running needs a
   * use case to attribute the traffic to, and IT Security is deliberately a member of nothing
   * (`ADR-0007`). No user could satisfy both. Running a battery is making requests; whoever may
   * call a model may test one. Authoring a battery stays with IT Security.
   */
  protected readonly mayRun = computed(() => this.mine().length > 0);

  protected readonly batteries = signal<TestBattery[]>([]);
  protected readonly models = signal<CatalogModel[]>([]);
  protected readonly runs = signal<TestRun[]>([]);
  protected readonly stats = signal<TestModelStats[]>([]);
  protected readonly loading = signal(true);

  // What a new run will be.
  protected readonly battery = signal<number | null>(null);
  protected readonly model = signal('');
  protected readonly useCase = signal('');
  /**
   * The use cases this caller may actually attribute traffic to.
   *
   * A **picker, not a free-text box**, and the difference was a real defect: the first version let
   * an incident role type any slug, and IT Security is deliberately a member of nothing
   * (`ADR-0007` — oversight must never imply the right to act inside a use case). So the run went
   * through, the gateway correctly refused every request with "not a member", and the screen
   * collected three failures that looked like the model's fault. `FRD-206` in its usual shape: a
   * control that promises what the server refuses.
   */
  protected readonly mine = signal<{ slug: string; name: string }[]>([]);
  protected readonly running = signal(false);
  /** How far a run has got, so a long battery does not look frozen. */
  protected readonly progress = signal('');

  // The run being read.
  protected readonly openRun = signal<TestRun | null>(null);
  protected readonly results = signal<TestResult[]>([]);
  /** Index of the answer open in the rating window, or `null` when it is closed. */
  protected readonly rating = signal<number | null>(null);
  protected readonly note = signal('');

  /** Only approved models can be called at all (`FRD-307`), so only those are offered. */
  protected readonly runnable = computed(() =>
    this.models().filter((m) => m.approved !== false && m.name),
  );

  protected readonly current = computed(() => {
    const index = this.rating();
    return index === null ? null : (this.results()[index] ?? null);
  });

  ngOnInit(): void {
    this.meService.get().subscribe({ next: (me) => this.me.set(me), error: () => undefined });
    this.load();
  }

  protected load(): void {
    this.loading.set(true);
    this.service.batteries().subscribe({
      next: (rows) => {
        this.batteries.set(rows);
        if (rows.length && this.battery() === null) this.battery.set(rows[0].id);
        this.loading.set(false);
      },
      error: (response: unknown) => {
        this.loading.set(false);
        this.feedback.fail(response, 'Could not load the test batteries.');
      },
    });
    this.service.models().subscribe({
      next: (rows) => this.models.set(rows),
      error: () => undefined,
    });
    // The **paged** endpoint. `list()` still exists and returns the same URL, which since paging
    // landed answers with a `Page` object rather than an array — so `rows.filter` quietly walked
    // over nothing and the picker was empty for everybody. Found by a browser, because a typed
    // `Observable<UseCase[]>` over a body that is not one compiles perfectly.
    this.service.listPage('', 1).subscribe({
      next: (page) => {
        const rows = page.results ?? [];
        const usable = rows
          .filter((row) => row.permissions?.is_member)
          .map((r) => ({ slug: r.slug, name: r.name }));
        this.mine.set(usable);
        if (usable.length && !this.useCase()) this.useCase.set(usable[0].slug);
      },
      error: () => undefined,
    });
    this.refreshRuns();
  }

  protected refreshRuns(): void {
    this.service.testRuns().subscribe({
      next: (rows) => this.runs.set(rows),
      error: () => undefined,
    });
    this.service.testStats().subscribe({
      next: (rows) => this.stats.set(rows),
      error: () => undefined,
    });
  }

  /**
   * Run the selected battery against the selected model, one prompt at a time.
   *
   * Sequential on purpose. Firing a battery of fifty at once would trip the use case's own rate
   * limit — the control this installation configured — and produce a run full of `429`s that says
   * nothing about the model.
   */
  protected async run(): Promise<void> {
    const batteryId = this.battery();
    if (batteryId === null || !this.model() || !this.useCase() || this.running()) return;

    this.running.set(true);
    this.feedback.clear();
    try {
      const run = await firstValueFrom(
        this.service.startRun(batteryId, this.model(), this.useCase()),
      );
      const results = await firstValueFrom(this.service.runResults(run.id));

      for (const [index, result] of results.entries()) {
        this.progress.set(`${index + 1} of ${results.length}`);
        await this.ask(result);
      }

      await firstValueFrom(this.service.finishRun(run.id));
      this.progress.set('');
      this.refreshRuns();
      await this.open(run);
      this.feedback.succeed(
        `${results.length} answer(s) from ${run.model}. Nothing is rated yet — that is the next step.`,
      );
    } catch (error) {
      this.feedback.fail(error, 'The run could not be completed.');
      this.progress.set('');
    } finally {
      this.running.set(false);
    }
  }

  /** One prompt, through the gateway, with whatever came back written straight to the result. */
  private async ask(result: TestResult): Promise<void> {
    const started = Date.now();
    try {
      const answer = await firstValueFrom(
        this.service.askModel(this.model(), result.prompt, this.useCase()),
      );
      await firstValueFrom(
        this.service.updateResult(result.id, {
          response: answer,
          latency_ms: Date.now() - started,
        }),
      );
    } catch (error) {
      // A refused or failed **request** is not a bad **answer**, and the two are stored in
      // different fields so the statistics can keep them apart. Folding them together would make
      // an outage look like a quality problem.
      await firstValueFrom(
        this.service.updateResult(result.id, {
          error: describe(error),
          latency_ms: Date.now() - started,
        }),
      );
    }
  }

  /**
   * Open a run **at the first answer that still needs a verdict**.
   *
   * The first version showed a table of answers and asked for a second click per row to open the
   * window. That is two steps too many for the only thing somebody comes here to do: read one
   * question, judge it, move on. Reported as *"ich will jede Frage einzeln haben und sie dann
   * bewerten, so dass das Window nach der Bewertung zur nächsten geht"* — which is what the window
   * already did, hidden behind a list nobody wanted.
   */
  protected async open(run: TestRun): Promise<void> {
    this.openRun.set(run);
    this.rating.set(null);
    this.service.runResults(run.id).subscribe({
      next: (rows) => {
        this.results.set(rows);
        const first = rows.findIndex((row) => row.verdict === 'unrated' && !row.error);
        // Everything rated already: open the first one anyway rather than nothing, because the
        // reader may have come back to change a judgement.
        this.rate(first >= 0 ? first : 0);
      },
      error: (response: unknown) => this.feedback.fail(response, 'Could not load the answers.'),
    });
  }

  protected closeRun(): void {
    this.openRun.set(null);
    this.results.set([]);
    this.rating.set(null);
  }

  // ---- rating, one answer at a time ---------------------------------------------------------

  protected rate(index: number): void {
    this.rating.set(index);
    this.note.set(this.results()[index]?.note ?? '');
  }

  protected closeRating(): void {
    this.rating.set(null);
    this.openRun.set(null);
    this.results.set([]);
  }

  protected step(by: number): void {
    const index = this.rating();
    if (index === null) return;
    const next = index + by;
    if (next < 0 || next >= this.results().length) return;
    this.rate(next);
  }

  /** Record a verdict and move on, because the next answer is what the reader wants next. */
  protected verdict(value: TestVerdict): void {
    const index = this.rating();
    const result = this.current();
    if (index === null || !result) return;

    this.service.updateResult(result.id, { verdict: value, note: this.note() }).subscribe({
      next: (saved) => {
        this.results.update((rows) => rows.map((row) => (row.id === saved.id ? saved : row)));
        this.refreshRuns();
        if (index + 1 < this.results().length) this.rate(index + 1);
        else this.closeRating();
      },
      error: (response: unknown) => this.feedback.fail(response, 'Could not save this rating.'),
    });
  }

  /**
   * Download the CSV through the API client rather than as a plain link.
   *
   * A link carries no bearer token, so the browser would follow it, receive a 401, and show the
   * reader a broken export — the same reason `FRD-602`'s download is a blob.
   */
  protected download(run: TestRun): void {
    this.service.testRunCsv(run.id).subscribe({
      next: (blob) => {
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `aira-smoketest-${run.model.replace(/[^\w.-]/g, '_')}.csv`;
        link.click();
        URL.revokeObjectURL(url);
      },
      error: (response: unknown) => this.feedback.fail(response, 'Could not export this run.'),
    });
  }

  protected badge(verdict: TestVerdict): string {
    if (verdict === 'pass') return 'badge badge--success';
    if (verdict === 'fail') return 'badge badge--danger';
    if (verdict === 'unclear') return 'badge badge--warning';
    return 'badge';
  }
}

function describe(error: unknown): string {
  const body = (error as { error?: { error?: { message?: string } } })?.error?.error?.message;
  const status = (error as { status?: number })?.status;
  return (body ?? `request failed${status ? ` (${status})` : ''}`).slice(0, 240);
}
