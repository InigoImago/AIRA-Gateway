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
  // every model-backed step (`FRD-302` FR-7)
  /**
   * This step's own chain, tried in order where `model` cannot be **reached** — not where it
   * answers something the operator would rather it had not. Distinct from the use case's
   * `fallback_models`, which is the chain that serves the caller.
   */
  fallback_models?: string[];
}

export interface PipelineStep {
  type: StepType;
  config: StepConfig;
}

/** A kind of request the installation keeps a fallback chain for (`FRD-310`). */
export type FallbackKind = 'generate' | 'embed' | 'rerank';

/** Every kind of request, in the order they are shown. */
export const FALLBACK_KINDS: readonly FallbackKind[] = ['generate', 'embed', 'rerank'];

/** Each kind's chain: model names in the order they are tried, `[]` for none. */
export type FallbackChains = Record<FallbackKind, string[]>;

/**
 * What a use case does when a model cannot answer (`FRD-310`): follow the installation's chains,
 * keep its own for text generation, or have none — the model it asked for, or the error.
 */
export type FallbackMode = 'default' | 'own' | 'none';

export interface PipelineConfig {
  steps: PipelineStep[];
  /** The use case's own chain for text generation, used under `fallback_mode: 'own'` only. */
  fallback_models: string[];
  /** Absent from an older Management, which means `own` where a chain is named. */
  fallback_mode?: FallbackMode;
  /** The chain each kind of the use case's requests meets, as the server works it out. */
  fallbacks?: FallbackChains;
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
