import { DatePipe } from '@angular/common';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';
import { MeService } from '../../core/api/me.service';
import { maySetStandards } from '../../core/auth/roles';
import {
  CatalogModel,
  TestAttribution,
  TestCase,
  TestModelStats,
  TestResult,
  TestRun,
  TestVerdict,
} from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { InfoHint } from '../../core/ui/info-hint';
import { PageFeedback } from '../../core/ui/page-feedback';

/**
 * Putting the question catalogue to a model, and reading what came back (`FRD-504`).
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
 *
 * **Three sub-tabs, because there are three activities and they belong to different moments.**
 * The catalogue is written once and grows slowly — it is the *standard*, a hundred questions that
 * outlive any one model. A run puts that standard to a model. And the first thing anybody wants is
 * the answer to "where does each model stand", which is the **latest** run per model — never a
 * total across every run a model has ever had. That first version summed them, and summing is the
 * wrong shape twice over: an old, since-corrected result drags the current one down forever, and
 * the figure moves when somebody re-runs something unrelated. Earlier runs are **history**, and
 * they stay readable — how a model's behaviour changed between two versions is a question only the
 * history can answer.
 *
 * **One flat catalogue, no grouping.** An earlier version sorted the questions into named
 * batteries; there is nothing to group, and grouping cost the property that makes this a standard:
 * with several batteries, "how does this model do" has as many answers as there are groups.
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
  private readonly confirmService = inject(ConfirmService);
  protected readonly feedback = inject(PageFeedback);

  private readonly me = signal<{ roles: string[] } | null>(null);
  /**
   * Whether this caller can start a run at all — which is **membership**, not a role.
   *
   * The first version asked for an incident role, and the feature was unusable: running needs a
   * use case to attribute the traffic to, and IT Security is deliberately a member of nothing
   * (`ADR-0007`). No user could satisfy both. Running the catalogue is making requests; whoever
   * may call a model may test one. Writing the catalogue stays with IT Security.
   *
   * What decides it is whether the **gateway** will accept this caller for the smoke-test use
   * case, which is the server's answer and not a list this screen filters.
   */
  protected readonly mayRun = computed(() => this.attribution()?.may_call === true);

  /** Which of the three activities the reader is on. */
  protected readonly tab = signal<'results' | 'runs' | 'catalogue'>('results');

  /** The catalogue itself: one flat list of questions, in the order they are asked. */
  protected readonly cases = signal<TestCase[]>([]);
  protected readonly models = signal<CatalogModel[]>([]);
  protected readonly runs = signal<TestRun[]>([]);
  protected readonly stats = signal<TestModelStats[]>([]);
  protected readonly loading = signal(true);

  // What a new run will be.
  protected readonly model = signal('');
  protected readonly useCase = computed(() => this.attribution()?.use_case ?? '');
  /**
   * Where a run is booked, answered by the server.
   *
   * **Not a control, and no longer a choice.** It was a picker, and the picker was wrong three
   * times over: it listed page one of a paged list (an endless dropdown that frequently did not
   * hold the use case somebody works in); it asked a question the person running a model test has
   * no opinion about; and it resolved membership with Management's rule rather than the gateway's,
   * so a global admin was offered a use case their token has never reached and every question of a
   * run came back `Not a member of use case 'addr-1nn4ss'`.
   *
   * There is **one use case for all model testing** (owner's decision, 2026-08-09). Test traffic is
   * real traffic and has to be priced somewhere; booking it against whichever use case the tester
   * belongs to charges somebody else's budget for work that is not theirs and mixes evaluation
   * spend into their production figures. Reporting now separates the two by construction.
   */
  protected readonly attribution = signal<TestAttribution | null>(null);

  protected readonly running = signal(false);
  /** How far a run has got, so a hundred questions do not look frozen. */
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

  /**
   * Authoring the catalogue is IT Security's, matching the server's `IsITSecurity`.
   *
   * The catalogue states what this installation considers an acceptable answer — the same kind of
   * statement as a global anomaly rule, and owned by the same role. Everyone who may call a model
   * may **run** it and rate what comes back.
   */
  protected readonly mayAuthor = computed(() => maySetStandards(this.me()?.roles));

  /** What the reader typed into the catalogue's search box. */
  protected readonly search = signal('');

  /**
   * The catalogue as the reader sees it.
   *
   * Filtered in the browser on purpose. A hundred rows is not a paging problem, and the count the
   * screen states is a count over the **whole** catalogue — server-side paging would turn "100
   * questions" into "100 on this page", which is the decision `FRD-208` already made for the model
   * catalog for the same reason.
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

  // The question being written, and its fields. Signals because Angular is zoneless (FRD-203 §4).
  protected readonly editing = signal<Partial<TestCase> | null>(null);
  protected readonly caseTopic = signal('');
  protected readonly casePrompt = signal('');
  protected readonly caseExpectation = signal('');

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
    this.service.testCases().subscribe({
      next: (rows) => {
        this.cases.set(rows);
        this.loading.set(false);
      },
      error: (response: unknown) => {
        this.loading.set(false);
        this.feedback.fail(response, 'Could not load the question catalogue.');
      },
    });
    this.service.models().subscribe({
      next: (rows) => this.models.set(rows),
      error: () => undefined,
    });
    this.service.testAttribution().subscribe({
      next: (where) => this.attribution.set(where),
      error: (response: unknown) =>
        this.feedback.fail(response, 'Could not work out where a run would be booked.'),
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
   * Run the catalogue against the selected model, one prompt at a time.
   *
   * Sequential on purpose. Firing a hundred at once would trip the use case's own rate
   * limit — the control this installation configured — and produce a run full of `429`s that says
   * nothing about the model.
   */
  protected async run(): Promise<void> {
    if (!this.model() || !this.mayRun() || this.running()) return;

    this.running.set(true);
    this.feedback.clear();
    try {
      const run = await firstValueFrom(this.service.startRun(this.model(), this.useCase()));
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
   * Is this the run that counts as the model's current standing?
   *
   * Answered from the same rows the results tab is built from, rather than recomputed here — a
   * second definition of "latest" would eventually disagree with the first, which is the defect
   * this project has now recorded under several names.
   */
  protected isLatest(run: TestRun): boolean {
    return this.stats().some((row) => row.run === run.id);
  }

  // ---- the catalogue -------------------------------------------------------------------------

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
      // Appended at the end. A catalogue is added to far more often than it is reordered, and a
      // position somebody has to choose on every question is a field they will get wrong.
      position: draft.id ? (draft.position ?? 0) : this.cases().length + 1,
    };
    const request = draft.id
      ? this.service.updateCase(draft.id, body)
      : this.service.createCase(body);

    request.subscribe({
      next: () => {
        this.editing.set(null);
        this.feedback.succeed(draft.id ? 'Question saved.' : 'Question added to the catalogue.');
        this.reloadCatalogue();
      },
      error: (response: unknown) => this.feedback.fail(response, 'Could not save this question.'),
    });
  }

  protected removeCase(item: TestCase): void {
    const question = `Remove "${item.topic}" from the catalogue? Answers already given to it stay.`;
    if (!this.confirmService.ask(question)) return;
    this.service.deleteCase(item.id).subscribe({
      next: () => {
        this.feedback.succeed('Question removed.');
        this.reloadCatalogue();
      },
      error: (response: unknown) => this.feedback.fail(response, 'Could not remove this question.'),
    });
  }

  private reloadCatalogue(): void {
    this.service.testCases().subscribe({
      next: (rows) => this.cases.set(rows),
      error: (response: unknown) => this.feedback.fail(response, 'Could not reload the catalogue.'),
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
