/**
 * The pipeline steps this console can author.
 *
 * Mirrors `aira_gateway.pipeline.config.StepType` and the serializer's `STEP_TYPES`;
 * `libs/tests/test_capability_vocabulary.py` compares the three in both directions, because a step
 * the console cannot offer looks exactly like a step that does not exist.
 */
export type StepType = 'injection_filter' | 'model_route' | 'pii_filter';

export interface RouteCategory {
  name: string;
  description?: string;
  model: string;
}

export interface StepConfig {
  // injection_filter
  mode?: 'heuristic' | 'llm';
  action?: 'block' | 'flag';
  scope?: 'user' | 'system_user';
  patterns?: string[];
  use_builtins?: boolean;
  instruction?: string;
  /**
   * What a *blocking* LLM filter does when its classifier reaches no verdict (FRD-125). Defaults
   * to refusing: a filter that serves unchecked requests protects nothing while still showing as
   * active in the builder.
   */
  on_undetermined?: 'block' | 'allow';
  // pii_filter (`FRD-309`)
  /** What the caller is told, in the operator's words, when something was replaced. Empty adds
   *  nothing. */
  notice?: string;
  /** No lesser version of this step exists: either the data was removed or it was not. Defaults
   *  to refusing, and `allow` is recorded on the audit row as the choice it is. */
  on_failure?: 'block' | 'allow';
  // model_route
  model?: string; // classifier model
  categories?: RouteCategory[];
  default_model?: string;
}

export interface PipelineStep {
  type: StepType;
  config: StepConfig;
}

export interface PipelineConfig {
  steps: PipelineStep[];
  fallback_models: string[];
  updated_at?: string;
}

export interface DryRunTraceEntry {
  type: string;
  action: string;
  detail: Record<string, unknown>;
  /**
   * This step ran only because the dry run was asked to keep going past a block. Production stops
   * at the refusal, so the entry is a **simulation** and the screen labels it as one.
   */
  after_block?: boolean;
}

export interface DryRunResult {
  blocked: boolean;
  block_reason: string | null;
  effective_model: string;
  fallback_models: string[];
  trace: DryRunTraceEntry[];
}
