import { NgTemplateOutlet } from '@angular/common';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { errorMessage } from '../../core/api/error-message';
import { PipelineConfig, StepType } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';
import { ConfirmService } from '../../core/ui/confirm.service';
import { MultiSelect, MultiSelectOption } from '../../core/ui/multi-select';
import {
  BUILTIN_LABELS,
  STEP_HELP,
  STEP_LABELS,
  STEP_TYPES,
  defaultConfig,
  parseList,
  summarize,
} from './pipeline-steps';
import { PipelineTestPanel } from './pipeline-test-panel';

/**
 * The pipeline builder: the graph of steps and its inspector, with the test panel beside them.
 *
 * The page loads and saves the pipeline and owns editing the graph; `PipelineTestPanel` owns the
 * sample prompt, the live preview and the dry run.
 */
@Component({
  selector: 'app-pipeline-editor',
  imports: [FormsModule, NgTemplateOutlet, RouterLink, MultiSelect, PipelineTestPanel],
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
  });
  protected readonly selected = signal<number | 'fallback' | null>(null);
  protected readonly saved = signal(false);
  protected readonly saving = signal(false);
  protected readonly loading = signal(true);
  /** The stored pipeline could not be read, so the page holds nothing a save could be about. */
  protected readonly loadFailed = signal(false);
  protected readonly dirty = signal(false);
  protected readonly error = signal<string | null>(null);
  protected slug = '';

  protected readonly stepTypes = STEP_TYPES;
  protected readonly label = (type: StepType): string => STEP_LABELS[type];
  protected readonly help = (type: StepType): string => STEP_HELP[type];
  protected readonly summarize = summarize;
  protected readonly builtinLabels = BUILTIN_LABELS;

  /**
   * Whether this caller may change the pipeline, as the server answers it on the use case.
   *
   * Anyone who may see the use case may read its pipeline and dry-run it. Without this answer the
   * graph is read-only, rather than failing a save with 403 after the work is done.
   */
  protected readonly canManage = signal(false);
  /**
   * Save is offered only over a pipeline that was read. Before the GET answers, or after it failed,
   * `config()` is the empty placeholder, and saving it would replace the stored steps — the
   * injection and personal-data filters among them — with a pipeline nobody had seen.
   */
  protected readonly canSave = computed(
    () => this.canManage() && !this.loading() && !this.loadFailed(),
  );
  /** Whether the gateway would accept this reader for a dry run (see `PipelineTestPanel`). */
  protected readonly mayCall = signal(true);
  /**
   * The models released to this use case (`FRD-308`). Every model field chooses from them: free
   * text would offer names the server refuses (`FRD-206`).
   */
  protected readonly released = signal<string[]>([]);
  protected readonly modelChoices = computed<MultiSelectOption[]>(() =>
    this.released().map((name) => ({ value: name, label: name })),
  );
  /**
   * Where the answer about the use case stands. The release and the reader's rights are facts about
   * the use case: before it answers neither is known, and a failed read says nothing of either —
   * so "nothing is released" and "you may only read this" are said only once it has answered.
   */
  protected readonly useCaseState = signal<'loading' | 'loaded' | 'failed'>('loading');
  /** Why the use case could not be read, in the backend's words. */
  protected readonly useCaseError = signal<string | null>(null);
  protected readonly releaseKnown = computed(() => this.useCaseState() === 'loaded');
  /** Nothing released — every new use case's state; said once, above the builder. */
  protected readonly nothingReleased = computed(
    () => this.releaseKnown() && this.released().length === 0,
  );

  protected readonly selectedStep = computed(() => {
    const index = this.selected();
    return typeof index === 'number' ? (this.config().steps[index] ?? null) : null;
  });

  protected readonly selectedIndex = computed(() =>
    typeof this.selected() === 'number' ? (this.selected() as number) : -1,
  );

  ngOnInit(): void {
    this.slug = this.route.snapshot.paramMap.get('slug') ?? '';
    this.service.get(this.slug).subscribe({
      next: (useCase) => {
        this.canManage.set(useCase.permissions?.can_manage ?? false);
        this.mayCall.set(useCase.permissions?.may_call ?? true);
        this.released.set([...(useCase.allowed_models ?? [])].sort());
        this.useCaseState.set('loaded');
      },
      // A use case we cannot read keeps the safe answer, read-only. Said beside the builder rather
      // than as a second banner, which belongs to the pipeline the reader asked for.
      error: (response: unknown) => {
        this.canManage.set(false);
        this.useCaseError.set(errorMessage(response, 'Could not load the use case.'));
        this.useCaseState.set('failed');
      },
    });
    this.loadPipeline();
  }

  /** Ask for the stored pipeline again, after a load that failed. */
  protected reload(): void {
    this.error.set(null);
    this.loadFailed.set(false);
    this.loading.set(true);
    this.loadPipeline();
  }

  private loadPipeline(): void {
    this.service.getPipeline(this.slug).subscribe({
      next: (config) => {
        this.config.set(config);
        this.loading.set(false);
      },
      error: (response: unknown) => {
        this.error.set(errorMessage(response, 'Could not load the pipeline.'));
        this.loadFailed.set(true);
        this.loading.set(false);
      },
    });
  }

  /**
   * A stored model with no released option, offered as itself: a select with no option for its
   * value renders blank, which reads as "none chosen" while the step still names the model. It is
   * marked "not released" only once the release is known.
   */
  protected strayOption(model: string | null | undefined): { value: string; label: string } | null {
    if (!model || this.released().includes(model)) return null;
    return { value: model, label: this.releaseKnown() ? `${model} (not released)` : model };
  }

  /** A stored model the use case is known not to release, or `null` — said beside its field. */
  protected unreleased(model: string | null | undefined): string | null {
    return this.releaseKnown() && this.strayOption(model) ? (model as string) : null;
  }

  /** Edit the graph locally; it reaches the gateway only on save, and the header says so. */
  private update(mutator: (config: PipelineConfig) => void): void {
    const next = structuredClone(this.config());
    mutator(next);
    this.config.set(next);
    this.saved.set(false);
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
    this.setStepField(index, key, parseList(csvOrLines));
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

  /** The fallback chain, chosen from the released models (`FRD-308`). **Order is preserved**: a
   *  chain is tried in the order it is written, so the picker appends rather than sorting. */
  protected setFallbackModels(models: string[]): void {
    this.update((c) => (c.fallback_models = models));
  }

  protected save(): void {
    if (!this.canSave() || this.saving()) return;
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
}
