import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { PipelineConfig, PipelineStep, StepType } from '../../core/api/models';
import { UseCaseService } from '../../core/api/use-case.service';

const STEP_LABELS: Record<StepType, string> = {
  injection_filter: 'Injection Filter',
  allow_check: 'Allow-Check',
  model_route: 'Model Routing',
};

function defaultConfig(type: StepType): PipelineStep['config'] {
  switch (type) {
    case 'injection_filter':
      return { mode: 'heuristic', action: 'block' };
    case 'allow_check':
      return { models: [] };
    case 'model_route':
      return { rules: [{ model: '' }] };
  }
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

  protected readonly config = signal<PipelineConfig>({ steps: [], fallback_models: [] });
  protected readonly selected = signal<number | 'fallback' | null>(null);
  protected readonly saved = signal(false);
  protected readonly error = signal<string | null>(null);
  protected slug = '';

  protected readonly stepTypes: StepType[] = ['injection_filter', 'allow_check', 'model_route'];
  protected readonly label = (type: StepType): string => STEP_LABELS[type];

  protected summarize(step: PipelineStep): string {
    switch (step.type) {
      case 'injection_filter':
        return `${step.config.mode ?? 'heuristic'} · ${step.config.action ?? 'block'}`;
      case 'allow_check':
        return `${(step.config.models ?? []).length} allowed model(s)`;
      case 'model_route':
        return `${(step.config.rules ?? []).length} rule(s)`;
    }
  }

  protected readonly selectedStep = computed(() => {
    const index = this.selected();
    return typeof index === 'number' ? (this.config().steps[index] ?? null) : null;
  });

  protected readonly selectedIndex = computed(() =>
    typeof this.selected() === 'number' ? (this.selected() as number) : -1,
  );

  ngOnInit(): void {
    this.slug = this.route.snapshot.paramMap.get('slug') ?? '';
    this.service.getPipeline(this.slug).subscribe((config) => this.config.set(config));
  }

  private update(mutator: (config: PipelineConfig) => void): void {
    const next = structuredClone(this.config());
    mutator(next);
    this.config.set(next);
    this.saved.set(false);
  }

  protected select(index: number | 'fallback'): void {
    this.selected.set(index);
  }

  protected addStep(type: StepType): void {
    this.update((c) => c.steps.push({ type, config: defaultConfig(type) }));
    this.selected.set(this.config().steps.length - 1);
  }

  protected removeStep(index: number): void {
    this.update((c) => c.steps.splice(index, 1));
    this.selected.set(null);
  }

  protected moveStep(index: number, delta: number): void {
    const target = index + delta;
    if (target < 0 || target >= this.config().steps.length) {
      return;
    }
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

  protected setAllowModels(index: number, csv: string): void {
    this.setStepField(index, 'models', this.parseList(csv));
  }

  protected addRule(index: number): void {
    this.update((c) => (c.steps[index].config.rules ??= []).push({ model: '' }));
  }

  protected removeRule(index: number, ruleIndex: number): void {
    this.update((c) => c.steps[index].config.rules?.splice(ruleIndex, 1));
  }

  protected setRuleField(index: number, ruleIndex: number, key: string, value: unknown): void {
    this.update((c) => {
      const rule = c.steps[index].config.rules?.[ruleIndex];
      if (rule) {
        (rule as unknown as Record<string, unknown>)[key] = value === '' ? null : value;
      }
    });
  }

  protected setFallback(csv: string): void {
    this.update((c) => (c.fallback_models = this.parseList(csv)));
  }

  protected save(): void {
    this.service.savePipeline(this.slug, this.config()).subscribe({
      next: (config) => {
        this.config.set(config);
        this.saved.set(true);
        this.error.set(null);
      },
      error: () => this.error.set('Could not save the pipeline.'),
    });
  }

  private parseList(csv: string): string[] {
    return csv
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean);
  }
}
