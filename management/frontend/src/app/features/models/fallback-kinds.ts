import { Capability, CatalogModel, FallbackKind, FallbackMode } from '../../core/api/models';

/** How each kind of request is named on this console. */
export const KIND_LABELS: Record<FallbackKind, string> = {
  generate: 'Text generation',
  embed: 'Embedding',
  rerank: 'Ranking',
};

/**
 * What a model must declare to stand in for a request of each kind — the server's rule
 * (`aira_common.fallback.SERVES`), held to it by `test_the_console_reads_the_fallback_rule.py`.
 * Speech counts as generation: a spoken answer is a generation request, and the gateway gives a
 * speech model only the requests that want speech.
 */
export const SERVES: Record<FallbackKind, readonly Capability[]> = {
  generate: ['generate', 'speech'],
  embed: ['embed'],
  rerank: ['rerank'],
};

/** What a model nobody declared may do: generation and embedding, and nothing more. */
const BASELINE: readonly Capability[] = ['generate', 'embed'];

/** Whether a catalogued model may stand in for a request of `kind`, by its declaration. */
export function canServe(model: CatalogModel, kind: FallbackKind): boolean {
  const declared = model.capabilities?.length ? model.capabilities : BASELINE;
  return SERVES[kind].some((capability) => declared.includes(capability));
}

/**
 * The models a request of `kind` falls back to under `mode` — the server's rule
 * (`aira_common.fallback.chain_for`), for a screen showing what a mode will mean before it is
 * saved. After a save the server's own answer (`PipelineConfig.fallbacks`) is what is shown.
 */
export function chainFor(
  kind: FallbackKind,
  mode: FallbackMode,
  own: readonly string[],
  installation: readonly string[],
): string[] {
  if (mode === 'none') return [];
  if (mode === 'own' && kind === 'generate') return [...own];
  return [...installation];
}
