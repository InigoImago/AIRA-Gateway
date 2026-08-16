import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { errorMessage } from '../../core/api/error-message';
import { DryRunResult, PipelineConfig, PipelineStep, StepType } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { MultiSelect, MultiSelectOption } from '../../core/ui/multi-select';

const STEP_LABELS: Record<StepType, string> = {
  injection_filter: 'Injection Filter',
  model_route: 'Model Routing (LLM)',
  pii_filter: 'Personal data filter (LLM)',
};

const STEP_HELP: Record<StepType, string> = {
  injection_filter:
    'Scans the prompt for prompt-injection / jailbreak attempts and blocks or flags them.',
  model_route:
    'An LLM reads system + user text, picks one of your categories, and routes to that category’s model.',
  pii_filter:
    'A model you trust rewrites the prompt with personal data replaced, before it reaches the model that answers. The rewritten prompt is what is sent and what is kept — the original is not stored.',
};

// Mirrors the gateway BUILTIN_INJECTION_PATTERNS so the live preview matches server behaviour.
const BUILTIN_REGEXES = [
  'ignore\\s+(all\\s+)?(previous|prior|above)\\s+(instructions|prompts)',
  'disregard\\s+(the\\s+)?(previous|above|system|all)',
  'forget\\s+(all|everything|previous|your)',
  'you\\s+are\\s+now\\b',
  'reveal\\s+(your\\s+)?(the\\s+)?(system\\s+)?prompt',
  'developer\\s+mode',
  'jailbreak',
];
const BUILTIN_LABELS = [
  '“ignore (all) previous/prior/above instructions”',
  '“disregard the previous/above/system”',
  '“forget all/everything/previous/your …”',
  '“you are now …”',
  '“reveal your/the system prompt”',
  '“developer mode”',
  '“jailbreak”',
];

function defaultConfig(type: StepType): PipelineStep['config'] {
  switch (type) {
    case 'injection_filter':
      return {
        mode: 'heuristic',
        action: 'block',
        scope: 'user',
        use_builtins: true,
        patterns: [],
      };
    case 'model_route':
      return { categories: [{ name: '', description: '', model: '' }], default_model: '' };
    case 'pii_filter':
      // `on_failure: 'block'` is the default in the gateway too, and the form starts there rather
      // than empty: this step has no lesser version of itself, so "could not redact" and "sent it
      // anyway" is the one combination nobody should reach by leaving a field alone.
      return { model: '', instruction: '', notice: '', on_failure: 'block' };
  }
}

/**
 * The live preview compiles operator-authored patterns in the browser. A pathological regex
 * would block the UI thread outright, so the preview bounds both how much text it scans and
 * how many patterns it runs — the saved config is validated server-side as well (ADR-0007).
 */
const PREVIEW_MAX_CHARS = 4_000;
const PREVIEW_MAX_PATTERNS = 64;

function matches(pattern: string, text: string): boolean {
  const sample = text.slice(0, PREVIEW_MAX_CHARS);
  try {
    return new RegExp(pattern, 'i').test(sample);
  } catch {
    return sample.toLowerCase().includes(pattern.toLowerCase());
  }
}

interface PreviewRow {
  label: string;
  action: string;
  note: string;
}

/**
 * One step of a dry run, as a card rather than a badge and a word.
 *
 * The trace used to render as `[blocked] injection_filter` and nothing else, which says what
 * happened and not *why* — and for the three LLM-backed steps the why is a model's own answer.
 * Somebody tuning a redaction instruction or a category list is reading exactly that: the sentence
 * they typed, what the model made of it, and which model was asked.
 *
 * `output`, `before` and `after` reach this screen and are **not** stored (`FRD-122` §5.3 keeps a
 * classifier's prose out of the audit row on purpose). They are the sample text on this page and
 * the reply to it.
 */
interface TraceCard {
  step: number;
  title: string;
  action: string;
  badge: string;
  /** One line saying what the step decided, in the reader's words rather than the wire's. */
  summary: string;
  /** The model that was asked — never the model the request was routed *to*. */
  classifier: string | null;
  /** What that model replied, verbatim (capped server-side). */
  output: string | null;
  before: string | null;
  after: string | null;
  /** Ran only because the dry run was asked past a block — production stops before this. */
  simulated: boolean;
}

function text(detail: Record<string, unknown>, key: string): string | null {
  const value = detail[key];
  return typeof value === 'string' && value.trim() ? value : null;
}

@Component({
  selector: 'app-pipeline-editor',
  imports: [FormsModule, RouterLink, MultiSelect],
  templateUrl: './pipeline-editor.html',
  styleUrl: './pipeline-editor.scss',
})
export class PipelineEditor implements OnInit {
  private readonly route = inject(ActivatedRoute);
  private readonly service = inject(UseCaseService);
  private readonly confirmService = inject(ConfirmService);

  protected readonly config = signal<PipelineConfig>({
    steps: [],
    fallback_models: [],
    start_model: '',
  });
  protected readonly selected = signal<number | 'fallback' | null>(null);
  protected readonly saved = signal(false);
  protected readonly saving = signal(false);
  protected readonly loading = signal(true);
  protected readonly dirty = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly dryRunning = signal(false);
  protected slug = '';

  // `allow_check` left on 2026-08-11. Which models a use case may call is a property of the use
  // case, released on its own screen and enforced at every hop (`FRD-308`) — the step ran once,
  // before routing, so a route or a fallback went straight past it.
  protected readonly stepTypes: StepType[] = ['injection_filter', 'pii_filter', 'model_route'];
  protected readonly label = (type: StepType): string => STEP_LABELS[type];
  protected readonly help = (type: StepType): string => STEP_HELP[type];
  protected readonly builtinLabels = BUILTIN_LABELS;

  // Sample prompt + results for the test panel.
  protected readonly sampleSystem = signal('');
  protected readonly sampleUser = signal('');
  protected readonly dryRun = signal<DryRunResult | null>(null);
  protected readonly dryRunError = signal<string | null>(null);
  /**
   * Keep evaluating after a step refuses.
   *
   * Asked for from the console: a filter that blocks the sample leaves every step behind it
   * untested, and the only way to see them was to delete the filter and put it back. Off by
   * default and deliberately so — the default answer this screen gives has to be the one
   * production would give, and each step run past a block spends real tokens on a call the served
   * path would never make.
   */
  protected readonly pastBlocks = signal(false);
  /**
   * The pipeline as it was when the last dry run was made.
   *
   * A trace stays on screen while somebody keeps editing, and after the first change it describes
   * a configuration that no longer exists — which is the failure this whole screen is against: a
   * confident statement about the wrong thing. Cheaper to compare than to invalidate, and
   * comparing the *sample text* too, because a run against a different prompt is just as stale.
   */
  private readonly dryRunOf = signal('');
  protected readonly dryRunStale = computed(
    () => this.dryRun() !== null && this.dryRunOf() !== this.dryRunSubject(),
  );

  /**
   * The pipeline the last **failed** attempt was about.
   *
   * Reported from the console: *"when I start a dry run and it was rejected, the warning or error
   * doesn't go away."* It did not — the message stayed until the next run, so a reader who read it,
   * changed the step it was about and looked again was still being told about an attempt that no
   * longer matched anything on the screen.
   *
   * Held apart from `dryRunOf` because a failed attempt and a displayed trace are about different
   * things, and one signal for both got the pairing wrong in the case that matters: a failed run
   * would have stamped the *new* configuration onto the *old* trace, marking a stale result fresh.
   */
  private readonly dryRunErrorOf = signal('');
  /** The error, while it is still about what is on screen. */
  protected readonly currentError = computed(() =>
    this.dryRunErrorOf() === this.dryRunSubject() ? this.dryRunError() : null,
  );

  /**
   * The steps that were configured but never evaluated, because an earlier one stopped the request.
   *
   * `dry_run` stops where production stops, which is the truthful thing for it to do — but it left
   * a reader who blocks at step 1 with no way to find out whether steps 2 and 3 work at all, which
   * is what somebody checking a use case for compatibility is actually asking. They are shown as
   * what they are: configured, in order, **not reached**. Inventing outcomes for them would be a
   * claim about model calls that were never made.
   *
   * Taken from the configuration **as it was when the run was made**, not the current one: the two
   * differ exactly when the trace is stale, and pairing this run's trace with a step list edited
   * afterwards is how a card comes to be labelled with the wrong step's name.
   */
  private readonly dryRunSteps = signal<PipelineStep[]>([]);
  protected readonly notReached = computed(() => {
    const result = this.dryRun();
    // Nothing is unreached when the run was told to keep going: every configured step has a card
    // of its own, and a second list of the same steps would contradict it.
    if (!result?.blocked || result.trace.length >= this.dryRunSteps().length) return [];
    return this.dryRunSteps()
      .slice(result.trace.length)
      .map((step, index) => ({
        step: result.trace.length + index + 1,
        title: STEP_LABELS[step.type] ?? step.type,
      }));
  });

  private dryRunSubject(): string {
    return JSON.stringify([
      this.config(),
      this.sampleSystem(),
      this.sampleUser(),
      this.pastBlocks(),
    ]);
  }

  protected actionClass(action: string): string {
    if (action === 'passed' || action === 'allowed') return 'badge--success';
    if (action === 'blocked' || action === 'rejected') return 'badge--danger';
    if (action === 'flagged') return 'badge--warning';
    // `rerouted` and `redacted` keep the plain brand badge: something happened to the request, and
    // that is neither good news nor bad. Muted would read as "nothing to see", which is what
    // `unchanged` and `not_asked` are.
    if (action === 'rerouted' || action === 'redacted') return '';
    return 'badge--muted';
  }

  /** The dry run's trace as cards. Empty until a run has been made. */
  protected readonly traceCards = computed<TraceCard[]>(() =>
    (this.dryRun()?.trace ?? []).map((entry, index) => {
      const detail = entry.detail ?? {};
      return {
        step: index + 1,
        title: STEP_LABELS[entry.type as StepType] ?? entry.type,
        action: entry.action,
        badge: this.actionClass(entry.action),
        summary: this.describe(entry.type, entry.action, detail),
        classifier: text(detail, 'classifier'),
        output: text(detail, 'output'),
        before: text(detail, 'before'),
        after: text(detail, 'after'),
        simulated: entry.after_block === true,
      };
    }),
  );

  /**
   * What a step decided, in one sentence.
   *
   * Written per step type rather than by dumping the detail map: the same key means different
   * things in different steps (`model` is the model in use for a router and the model *asked* for
   * a redactor — which is why the gateway now sends `classifier` for the latter), and a reader of
   * a JSON blob has to know that. An unrecognised type falls back to the action word, so a step
   * this build does not know about still renders as itself instead of vanishing.
   */
  private describe(type: string, action: string, detail: Record<string, unknown>): string {
    const why = text(detail, 'why');
    if (type === 'injection_filter') {
      const verdict = text(detail, 'verdict') ?? 'no verdict';
      const mode = text(detail, 'mode') ?? 'heuristic';
      return action === 'blocked'
        ? `Verdict ${verdict} — the request stops here (${mode}).`
        : `Verdict ${verdict} (${mode}).`;
    }
    if (type === 'model_route') {
      if (action === 'rerouted') {
        return `Classified as “${text(detail, 'category')}” → ${text(detail, 'to')}.`;
      }
      if (action === 'not_asked') return `Not asked: ${why ?? 'the classifier did not answer'}.`;
      const category = text(detail, 'category');
      return category
        ? `Classified as “${category}”, which is the model already in use.`
        : 'No category matched — the request keeps the model it named.';
    }
    if (type === 'pii_filter') {
      if (action === 'redacted') return 'Personal data replaced in the user’s text.';
      if (action === 'unchanged') return 'Nothing to replace.';
      return action === 'allowed'
        ? `Could not redact (${why ?? 'unknown'}) — configured to serve anyway.`
        : `Could not redact (${why ?? 'unknown'}) — the request stops here.`;
    }
    return action;
  }

  protected summarize(step: PipelineStep): string {
    switch (step.type) {
      case 'injection_filter':
        return `${step.config.mode ?? 'heuristic'} · ${step.config.action ?? 'block'}`;
      case 'model_route':
        return `${(step.config.categories ?? []).length} categor(ies)`;
      case 'pii_filter':
        return `${step.config.model || 'no model'} · ${step.config.on_failure ?? 'block'}`;
    }
  }

  protected readonly selectedStep = computed(() => {
    const index = this.selected();
    return typeof index === 'number' ? (this.config().steps[index] ?? null) : null;
  });

  protected readonly selectedIndex = computed(() =>
    typeof this.selected() === 'number' ? (this.selected() as number) : -1,
  );

  // Client-side live preview: evaluates deterministic steps against the sample prompt.
  protected readonly preview = computed<PreviewRow[]>(() => {
    const system = this.sampleSystem();
    const user = this.sampleUser();
    const rows: PreviewRow[] = [];
    let blocked = false;
    for (const step of this.config().steps) {
      if (blocked) break;
      const name = STEP_LABELS[step.type];
      if (step.type === 'injection_filter') {
        if (step.config.mode === 'llm') {
          rows.push({
            label: name,
            action: 'runtime',
            note: 'LLM classifier — decided at request time',
          });
          continue;
        }
        const text = step.config.scope === 'system_user' ? `${system}\n${user}` : user;
        const patterns = [
          ...(step.config.use_builtins !== false ? BUILTIN_REGEXES : []),
          ...(step.config.patterns ?? []),
        ].slice(0, PREVIEW_MAX_PATTERNS);
        const hit = patterns.some((p) => matches(p, text));
        const action = hit ? (step.config.action === 'flag' ? 'flagged' : 'blocked') : 'passed';
        if (action === 'blocked') blocked = true;
        rows.push({ label: name, action, note: hit ? 'matched a pattern' : 'no pattern matched' });
      } else {
        rows.push({
          label: name,
          action: 'runtime',
          note: 'LLM picks a category — decided at request time',
        });
      }
    }
    return rows;
  });

  /**
   * Whether this caller may change the pipeline, as the server answers it on the use case.
   *
   * The builder is reachable by anyone who may see the use case, and reading a pipeline is
   * genuinely useful — it is the configuration governing every request they make. Editing it is
   * not: a save would come back 403 with the graph already rearranged on screen. So the graph
   * becomes read-only and the test panel stays live, because a dry run changes nothing.
   */
  protected readonly canManage = signal(false);
  /**
   * Whether the gateway would accept this reader's token for this use case — which is what a dry
   * run needs, and is **not** what the console's own membership answer says.
   *
   * `true` until told otherwise: an older control plane does not send the field, and a builder
   * that greys out its own test panel because a server did not mention something would be worse
   * than the defect it is here to prevent. Undefined means "no opinion", not "no".
   */
  protected readonly mayCall = signal(true);

  /**
   * The models this use case has been released (`FRD-308`).
   *
   * Every model field in the builder chooses from this rather than taking free text. Two reasons,
   * and the second is the one that matters: a typo used to be discoverable only as refused traffic
   * later, and a name the use case may **not** call could be saved, which the server now refuses —
   * a control that invites a click and then answers 400 is `FRD-206`'s complaint exactly.
   */
  protected readonly released = signal<string[]>([]);

  /** The released models as picker options. */
  protected readonly modelChoices = computed<MultiSelectOption[]>(() =>
    this.released().map((name) => ({ value: name, label: name })),
  );

  /** True when nothing is released, which is the state every new use case starts in. The builder
   *  says so once, at the top, rather than showing five empty dropdowns with no explanation. */
  protected readonly nothingReleased = computed(() => this.released().length === 0);

  ngOnInit(): void {
    this.slug = this.route.snapshot.paramMap.get('slug') ?? '';
    this.service.get(this.slug).subscribe({
      next: (useCase) => {
        this.canManage.set(useCase.permissions?.can_manage ?? false);
        this.mayCall.set(useCase.permissions?.may_call ?? true);
        // What this use case may call (`FRD-308`). Every model field below chooses from it: the
        // fields were free text, which offered exactly what the server refuses — `FRD-206`'s
        // complaint, and here it also invites naming a model the use case has no right to.
        this.released.set([...(useCase.allowed_models ?? [])].sort());
      },
      // A use case we cannot read leaves the safe answer in place rather than a second error
      // banner about a request the reader did not make.
      error: () => this.canManage.set(false),
    });
    this.service.getPipeline(this.slug).subscribe({
      next: (config) => {
        this.config.set(config);
        this.loading.set(false);
      },
      error: (response: unknown) => {
        this.error.set(errorMessage(response, 'Could not load the pipeline.'));
        this.loading.set(false);
      },
    });
  }

  private update(mutator: (config: PipelineConfig) => void): void {
    const next = structuredClone(this.config());
    mutator(next);
    this.config.set(next);
    this.saved.set(false);
    // The graph is edited locally and only reaches the gateway on save; say so, so nobody
    // navigates away believing a change is live.
    this.dirty.set(true);
  }

  protected select(index: number | 'fallback'): void {
    this.selected.set(index);
  }

  protected addStep(type: StepType): void {
    this.update((c) => c.steps.push({ type, config: defaultConfig(type) }));
    this.selected.set(this.config().steps.length - 1);
  }

  protected removeStep(index: number): void {
    const step = this.config().steps[index];
    if (!this.confirmService.ask(`Remove the "${this.label(step.type)}" step?`)) {
      return;
    }
    this.update((c) => c.steps.splice(index, 1));
    this.selected.set(null);
  }

  protected canMove(index: number, delta: number): boolean {
    const target = index + delta;
    return target >= 0 && target < this.config().steps.length;
  }

  protected moveStep(index: number, delta: number): void {
    const target = index + delta;
    if (target < 0 || target >= this.config().steps.length) return;
    this.update((c) => {
      const [step] = c.steps.splice(index, 1);
      c.steps.splice(target, 0, step);
    });
    this.selected.set(target);
  }

  protected setStepField(index: number, key: string, value: unknown): void {
    this.update((c) => {
      (c.steps[index].config as unknown as Record<string, unknown>)[key] = value;
    });
  }

  protected setListField(index: number, key: string, csvOrLines: string): void {
    this.setStepField(index, key, this.parseList(csvOrLines));
  }

  protected addCategory(index: number): void {
    this.update((c) => (c.steps[index].config.categories ??= []).push({ name: '', model: '' }));
  }

  protected removeCategory(index: number, catIndex: number): void {
    this.update((c) => c.steps[index].config.categories?.splice(catIndex, 1));
  }

  protected setCategoryField(index: number, catIndex: number, key: string, value: string): void {
    this.update((c) => {
      const category = c.steps[index].config.categories?.[catIndex];
      if (category) (category as unknown as Record<string, unknown>)[key] = value;
    });
  }

  /**
   * Where a request **enters** this pipeline when the caller names none (`ADR-0020`).
   *
   * Offered from the released models rather than typed, like the fallback chain and every category
   * target: a name that is not released is refused when the pipeline is saved, and a picker that
   * offers only what will be accepted is `FRD-206`'s rule applied to a text box.
   *
   * Blank is a real choice and stays available — most pipelines are only ever entered by a caller
   * who names their own model, which is every ordinary API request. What it costs is the question
   * catalogue: a use case with no start model cannot be run, and the smoke-test screen says so.
   */
  protected setStartModel(model: string): void {
    this.update((c) => (c.start_model = model));
  }

  protected setFallback(csv: string): void {
    this.update((c) => (c.fallback_models = this.parseList(csv)));
  }

  /** The fallback chain, chosen rather than typed (`FRD-308`). **Order is preserved** — a chain is
   *  tried in the order it is written, so the picker appends rather than sorting. */
  protected setFallbackModels(models: string[]): void {
    this.update((c) => (c.fallback_models = models));
  }

  protected save(): void {
    this.saving.set(true);
    this.service.savePipeline(this.slug, this.config()).subscribe({
      next: (config) => {
        this.config.set(config);
        this.saved.set(true);
        this.dirty.set(false);
        this.saving.set(false);
        this.error.set(null);
      },
      error: (response: unknown) => {
        this.saving.set(false);
        this.error.set(errorMessage(response, 'Could not save the pipeline.'));
      },
    });
  }

  protected runDryRun(): void {
    // The previous result is **kept on screen** while the next one runs. Clearing it first
    // collapsed the whole panel and the page jumped, which reads as a reload — and a control panel
    // that appears to reload makes somebody wonder what else it just did.
    this.dryRunError.set(null);
    this.dryRunning.set(true);
    // What this attempt is about, resolved **now**: the reader can keep editing while it runs, and
    // the answer that comes back is about the pipeline that was sent, not the one on screen.
    const subject = this.dryRunSubject();
    const steps = this.config().steps;
    this.service
      .dryRunPipeline({
        // A dry run spends real tokens, so the gateway wants to know whose they are and refuses a
        // model this use case may not call (`FRD-308`).
        use_case: this.slug,
        system: this.sampleSystem(),
        user: this.sampleUser(),
        pipeline: this.config(),
        past_blocks: this.pastBlocks(),
      })
      .subscribe({
        next: (result) => {
          this.dryRun.set(result);
          this.dryRunSteps.set(steps);
          this.dryRunOf.set(subject);
          this.dryRunning.set(false);
        },
        error: (response: { status?: number }) => {
          this.dryRunning.set(false);
          this.dryRunErrorOf.set(subject);
          this.dryRunError.set(
            // 401 and 403 were one message, and they are two different problems now. A dry run
            // spends real tokens, so the gateway applies the **membership** rule a request gets
            // (`ADR-0007`: oversight visibility never implies the right to act inside a use case)
            // — and a Global Administrator is deliberately a member of nothing. Telling them to
            // check `AIRA_OIDC_ENABLED` would send them to configuration that is working.
            response?.status === 403
              ? 'Dry-run refused — a dry run calls a real model and is charged to this use case, so it follows the same membership rule a request does. Grant yourself access to this use case to test its pipeline.'
              : response?.status === 401
                ? 'Dry-run rejected — the gateway did not accept your login. Enable OIDC on the gateway (AIRA_OIDC_ENABLED) so it can verify the same Keycloak token.'
                : errorMessage(
                    response,
                    'Dry-run failed — is the gateway running and reachable on /gw?',
                  ),
          );
        },
      });
  }

  private parseList(input: string): string[] {
    return input
      .split(/[\n,]/)
      .map((item) => item.trim())
      .filter(Boolean);
  }
}
