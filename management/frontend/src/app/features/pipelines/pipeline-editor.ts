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
};

const STEP_HELP: Record<StepType, string> = {
  injection_filter:
    'Scans the prompt for prompt-injection / jailbreak attempts and blocks or flags them.',
  model_route:
    'An LLM reads system + user text, picks one of your categories, and routes to that category’s model.',
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

  protected readonly config = signal<PipelineConfig>({ steps: [], fallback_models: [] });
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
  protected readonly stepTypes: StepType[] = ['injection_filter', 'model_route'];
  protected readonly label = (type: StepType): string => STEP_LABELS[type];
  protected readonly help = (type: StepType): string => STEP_HELP[type];
  protected readonly builtinLabels = BUILTIN_LABELS;

  // Sample prompt + results for the test panel.
  protected readonly sampleSystem = signal('');
  protected readonly sampleUser = signal('');
  protected readonly dryRun = signal<DryRunResult | null>(null);
  protected readonly dryRunError = signal<string | null>(null);

  protected actionClass(action: string): string {
    if (action === 'passed' || action === 'allowed') return 'badge--success';
    if (action === 'blocked' || action === 'rejected') return 'badge--danger';
    if (action === 'flagged') return 'badge--warning';
    return 'badge--muted';
  }

  protected summarize(step: PipelineStep): string {
    switch (step.type) {
      case 'injection_filter':
        return `${step.config.mode ?? 'heuristic'} · ${step.config.action ?? 'block'}`;
      case 'model_route':
        return `${(step.config.categories ?? []).length} categor(ies)`;
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
    this.service
      .dryRunPipeline({
        // A dry run spends real tokens, so the gateway wants to know whose they are and refuses a
        // model this use case may not call (`FRD-308`).
        use_case: this.slug,
        system: this.sampleSystem(),
        user: this.sampleUser(),
        pipeline: this.config(),
      })
      .subscribe({
        next: (result) => {
          this.dryRun.set(result);
          this.dryRunning.set(false);
        },
        error: (response: { status?: number }) => {
          this.dryRunning.set(false);
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
