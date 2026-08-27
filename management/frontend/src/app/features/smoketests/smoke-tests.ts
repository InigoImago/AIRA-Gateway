import { DatePipe } from '@angular/common';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';
import { MeService } from '../../core/api/me.service';
import { maySetStandards } from '../../core/auth/roles';
import {
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
 * Putting the question catalogue to a **use case's pipeline**, and reading what came back
 * (`FRD-504`, `ADR-0020`).
 *
 * Every other control in AIRA governs *access*. None of them says anything about what actually
 * comes back — and "does my pipeline hold?" is a question somebody has to be able to answer with
 * evidence rather than with a vendor's benchmark.
 *
 * **A run is about a use case.** It travels that use case's own pipeline, so the hundred questions
 * exercise the filter, the router and the redactor somebody configured; a blocked question is a
 * *result*, not a broken run. Testing a **model** is then a use case whose pipeline starts there,
 * which is what IT Security's evaluation use case is — nothing about model testing is a special
 * path any more.
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
   * Whether this caller can start a run at all — **the server's answer for the use case they
   * picked**, never a list this screen filters.
   *
   * `may_run` comes from `/test-attribution`, which asks two things per use case: will the gateway
   * accept this caller for it, and does its pipeline declare a start model. Both are answers only
   * the server has, and reading them per use case is what keeps this screen from offering a run
   * that 403s (`FRD-206`).
   *
   * **This docstring said "membership, not a role" until 2026-08-27**, and quoted `FRD-504`'s
   * *whoever may call a model may test one* as the reason. The server retired that sentence on
   * 2026-08-16 — `MayRunTests` says so in as many words — because a run stopped being about a
   * model the installation had approved and became a hundred prompts through somebody's pipeline,
   * spending their budget: a decision **about** the use case rather than work inside it, and so an
   * administrator's. The gate here was already right, because it asks the server; the reason
   * beside it described the rule the server no longer has, which is the more dangerous half —
   * nothing fails, and the next reader reasons from it.
   */
  protected readonly mayRun = computed(() => this.chosen()?.may_run === true);

  /** Which of the three activities the reader is on. */
  protected readonly tab = signal<'results' | 'runs' | 'catalogue'>('results');

  /** The catalogue itself: one flat list of questions, in the order they are asked. */
  protected readonly cases = signal<TestCase[]>([]);
  protected readonly runs = signal<TestRun[]>([]);
  protected readonly stats = signal<TestModelStats[]>([]);
  protected readonly loading = signal(true);

  /**
   * The server refused the screen itself.
   *
   * Distinct from `loading` and from `feedback.error()`: those describe a request that went wrong,
   * and this describes one that went right and said no. Running the catalogue needs administration
   * of a use case (owner's rule, 2026-08-16), which the nav already knows from `me.may_test` — but
   * the nav is not the only way in.
   */
  protected readonly withheld = signal(false);

  // What a new run will be: a use case, and nothing else.
  protected readonly useCase = signal('');

  /**
   * Which use cases this caller may put the catalogue to, answered by the server (`ADR-0020`).
   *
   * **A picker again, and the reasons it was removed are the reasons it is back correct.** It was
   * removed because it listed page one of a paged list, asked a question the person running a
   * *model* test had no opinion about, and resolved membership with Management's rule rather than
   * the gateway's — so a global admin was offered a use case their token had never reached and
   * every question of a run came back `Not a member of use case 'addr-1nn4ss'`.
   *
   * Two of those were the *list* being wrong, not the choice being wrong. This one is the server's
   * answer to "which use cases would the gateway accept from you, and which of those have a
   * pipeline to run", complete and already narrowed. The third — that nobody had an opinion — is
   * no longer true: a run is about a use case's pipeline, so which one is the whole question.
   */
  protected readonly runnableUseCases = signal<TestAttribution[]>([]);

  /**
   * Whether the answer above has actually arrived.
   *
   * Without it the empty list — the state every load starts in — renders as *"there is no use case
   * you may send requests to"*, so every reader is told something false for as long as the request
   * takes, including readers for whom it is false. `LESSONS.md` §6: **unknown is never rendered as
   * zero**. Found by an e2e test that read the sentence and believed it, which is what a person
   * would have done.
   */
  protected readonly attributionKnown = signal(false);

  /** The row for the use case the reader picked, or `null` while they have picked none. */
  protected readonly chosen = computed(
    () => this.runnableUseCases().find((row) => row.use_case === this.useCase()) ?? null,
  );

  /**
   * Choose a use case, and default the entry model with it.
   *
   * One handler rather than two bindings, because the second choice is only meaningful inside the
   * first: leaving a model selected from the previous use case would offer one the new use case
   * may not call, which the server then refuses — `FRD-206` in miniature.
   */
  protected chooseUseCase(slug: string): void {
    this.useCase.set(slug);
    this.startModel.set(this.entryModels()[0] ?? '');
  }

  /**
   * The models this run may be **entered at** — what the chosen use case has been released.
   *
   * The server's list, not one derived here: which models a use case may call is `FRD-308`'s
   * answer and the gateway refuses anything else at dispatch, so a picker offering more would
   * offer a run that fills with 403s.
   */
  protected readonly entryModels = computed(() => this.chosen()?.models ?? []);

  /**
   * Which of them this run enters at. **Chosen, not declared.**
   *
   * It came from a `start_model` on the pipeline until the owner pointed out what that costs: a
   * use case releases several models on purpose, and pinning one on the pipeline reads as *this
   * is the model this use case uses*. Two runs of one use case entering at two different models
   * is the comparison somebody evaluating a model actually wants.
   */
  protected readonly startModel = signal('');

  /**
   * Why this use case cannot be run, in the server's own words — or empty.
   *
   * Shown where Run would be, never as a disabled button with no explanation — the reader has to
   * be told what to go and change, and only the server knows (`FRD-206`).
   */
  protected readonly whyNot = computed(() => this.chosen()?.why_not ?? '');

  protected readonly running = signal(false);
  /** How far a run has got, so a hundred questions do not look frozen. */
  protected readonly progress = signal('');

  // The run being read.
  protected readonly openRun = signal<TestRun | null>(null);
  protected readonly results = signal<TestResult[]>([]);
  /** Index of the answer open in the rating window, or `null` when it is closed. */
  protected readonly rating = signal<number | null>(null);
  protected readonly note = signal('');

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
        if ((response as { status?: number })?.status === 403) {
          // Not an error: an answer. Somebody typed the address, or followed a bookmark from
          // before the rule narrowed. The tab strip comes down and the page says who runs the
          // catalogue — `FRD-206`'s rule that a withheld action names its performer, rather than
          // three tabs of controls over a banner explaining that none of them work.
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
        // Preselect only when there is nothing to choose. Choosing for somebody who has several
        // would be picking which pipeline they meant, and a run costs money.
        if (rows.length === 1) this.chooseUseCase(rows[0].use_case);
      },
      error: (response: unknown) => {
        // Known, and the answer is "none" — a failed question is still not an unasked one, and
        // leaving the panel on its loading state forever would be the opposite defect.
        this.attributionKnown.set(true);
        this.feedback.fail(response, 'Could not work out where the catalogue could be run.');
      },
    });
    this.refreshRuns();
  }

  /**
   * The run history and the per-model figures — **and what happens when they cannot be had**.
   *
   * Both swallowed their failure until 2026-08-27, alone among the loads on this screen: the two
   * beside them in `ngOnInit` each report through `PageFeedback`, one of them with a 403 branch of
   * its own. This is called from `ngOnInit` too, so a failed **first** load left the Runs and
   * Results tabs empty with nothing saying why — and empty is what this screen looks like before
   * anybody has run anything. *An empty state that states the wrong reason is worse than one that
   * states none: the reader concludes the recording is broken, and then distrusts every figure on
   * the page* — written in `reporting.py` about the other plane, and true here.
   *
   * One banner, not two, because `PageFeedback` is the page's single voice: if both calls fail the
   * reader is told once, about the thing they were looking at.
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

  /**
   * Run the catalogue against the selected model, one prompt at a time.
   *
   * Sequential on purpose. Firing a hundred at once would trip the use case's own rate
   * limit — the control this installation configured — and produce a run full of `429`s that says
   * nothing about the model.
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
      await this.open(run);
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

  /** One prompt, through the gateway, with whatever came back written straight to the result. */
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

  /**
   * Take a question out of the catalogue and keep every answer already given to it.
   *
   * This was a **delete**, and the model has said *"retired rather than deleted"* since it was
   * written: a verdict was formed against this wording, so removing the question would take the
   * verdict with it. `TestResult.case` is `PROTECT` and enforced that — by raising an unhandled
   * `ProtectedError`, which is a **500** on a control the console offers, behind a confirm box
   * that promised *"answers already given to it stay."* Every question in an installation that
   * has run the catalogue once was in that state.
   *
   * `retired` is the field that exists for this and had no caller anywhere. The server refuses the
   * delete by name now; this is the verb that works.
   */
  protected retireCase(item: TestCase): void {
    const question =
      `Retire "${item.topic}"? It leaves the catalogue and stops being asked. ` +
      'Answers already given to it are kept, with the wording they were judged against.';
    if (!this.confirmService.ask(question)) return;
    this.service.updateCase(item.id, { retired: true }).subscribe({
      next: () => {
        this.feedback.succeed('Question retired. Its answers are kept.');
        this.reloadCatalogue();
      },
      error: (response: unknown) => this.feedback.fail(response, 'Could not retire this question.'),
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
        link.download = `aira-smoketest-${run.use_case.replace(/[^\w.-]/g, '_')}.csv`;
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
