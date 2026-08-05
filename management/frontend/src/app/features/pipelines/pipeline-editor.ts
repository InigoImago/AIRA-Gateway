import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { errorMessage } from '../../core/api/error-message';
import { DryRunResult, PipelineConfig, PipelineStep, StepType } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';

const STEP_LABELS: Record<StepType, string> = {
  injection_filter: 'Injection Filter',
  allow_check: 'Allow-Check',
  model_route: 'Model Routing (LLM)',
};

const STEP_HELP: Record<StepType, string> = {
  injection_filter:
    'Scans the prompt for prompt-injection / jailbreak attempts and blocks or flags them.',
  allow_check: 'Rejects the request if the requested model is not in the allow-list.',
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
    case 'allow_check':
      return { models: [] };
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
  imports: [FormsModule, RouterLink],
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

  protected readonly stepTypes: StepType[] = ['injection_filter', 'allow_check', 'model_route'];
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
      case 'allow_check':
        return `${(step.config.models ?? []).length} allowed model(s)`;
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
      } else if (step.type === 'allow_check') {
        rows.push({ label: name, action: 'runtime', note: 'Depends on the requested model' });
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

  ngOnInit(): void {
    this.slug = this.route.snapshot.paramMap.get('slug') ?? '';
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
    this.dryRunError.set(null);
    this.dryRun.set(null);
    this.dryRunning.set(true);
    this.service
      .dryRunPipeline({
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
            response?.status === 401 || response?.status === 403
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
