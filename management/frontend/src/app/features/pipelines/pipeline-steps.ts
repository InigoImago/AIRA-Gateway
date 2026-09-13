import { DryRunResult, PipelineStep, StepType } from '../../core/api/models';

/**
 * The step types the builder offers, in the order it offers them.
 *
 * The personal-data filter comes before the router: redacting after routing would let the routing
 * classifier read the data. There is no allow-list step — which models a use case may call is
 * released on the use case and enforced at every hop (`FRD-308`), which a step before routing
 * could not do.
 */
export const STEP_TYPES: StepType[] = ['injection_filter', 'pii_filter', 'model_route'];

export const STEP_LABELS: Record<StepType, string> = {
  injection_filter: 'Injection Filter',
  model_route: 'Model Routing (LLM)',
  pii_filter: 'Personal data filter (LLM)',
};

export const STEP_HELP: Record<StepType, string> = {
  injection_filter:
    'Scans the prompt for prompt-injection / jailbreak attempts and blocks or flags them.',
  model_route:
    'An LLM reads system + user text, picks one of your categories, and routes to that category’s model.',
  pii_filter:
    'A model you trust rewrites the prompt with personal data replaced, before it reaches the model that answers. The rewritten prompt is what is sent and what is kept — the original is not stored.',
};

/** Mirrors the gateway's BUILTIN_INJECTION_PATTERNS so the live preview matches the server. */
export const BUILTIN_REGEXES = [
  'ignore\\s+(all\\s+)?(previous|prior|above)\\s+(instructions|prompts)',
  'disregard\\s+(the\\s+)?(previous|above|system|all)',
  'forget\\s+(all|everything|previous|your)',
  'you\\s+are\\s+now\\b',
  'reveal\\s+(your\\s+)?(the\\s+)?(system\\s+)?prompt',
  'developer\\s+mode',
  'jailbreak',
];
export const BUILTIN_LABELS = [
  '“ignore (all) previous/prior/above instructions”',
  '“disregard the previous/above/system”',
  '“forget all/everything/previous/your …”',
  '“you are now …”',
  '“reveal your/the system prompt”',
  '“developer mode”',
  '“jailbreak”',
];

/**
 * Bounds on the live preview, which compiles operator-authored patterns in the browser.
 *
 * A pathological regex would block the UI thread, so the preview caps both the text it scans and
 * the patterns it runs. The saved config is validated server-side as well (ADR-0007).
 */
export const PREVIEW_MAX_CHARS = 4_000;
export const PREVIEW_MAX_PATTERNS = 64;

export interface PreviewRow {
  label: string;
  action: string;
  note: string;
}

/**
 * One step of a dry run, as a card: what it decided, which model was asked and what it replied.
 *
 * `output`, `before` and `after` reach this screen and are **not** stored (`FRD-122` §5.3 keeps a
 * classifier's prose out of the audit row).
 */
export interface TraceCard {
  step: number;
  title: string;
  action: string;
  badge: string;
  /** What the step decided, in the reader's words rather than the wire's. */
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

/** A configured step the dry run never evaluated, because an earlier one stopped the request. */
export interface NotReachedCard {
  step: number;
  title: string;
}

/**
 * The configuration a new step starts with.
 *
 * `pii_filter` starts at `on_failure: 'block'`, the gateway's default too: the step has no lesser
 * version of itself, so "could not redact, sent it anyway" must not be reached by leaving a field
 * alone.
 */
export function defaultConfig(type: StepType): PipelineStep['config'] {
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
      return { model: '', instruction: '', notice: '', on_failure: 'block' };
  }
}

/** The one-line meta under a step's node in the graph. */
export function summarize(step: PipelineStep): string {
  switch (step.type) {
    case 'injection_filter':
      return `${step.config.mode ?? 'heuristic'} · ${step.config.action ?? 'block'}`;
    case 'model_route':
      return `${(step.config.categories ?? []).length} categor(ies)`;
    case 'pii_filter':
      return `${step.config.model || 'no model'} · ${step.config.on_failure ?? 'block'}`;
  }
}

/** Split comma- or newline-separated input into trimmed, non-empty items. */
export function parseList(input: string): string[] {
  return input
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

/**
 * The badge class for an outcome.
 *
 * `rerouted` and `redacted` keep the plain brand badge: the request was changed, which is neither
 * good nor bad news. Muted means nothing happened (`unchanged`, `not_asked`).
 */
export function actionClass(action: string): string {
  if (action === 'passed' || action === 'allowed') return 'badge--success';
  if (action === 'blocked' || action === 'rejected') return 'badge--danger';
  if (action === 'flagged') return 'badge--warning';
  if (action === 'rerouted' || action === 'redacted') return '';
  return 'badge--muted';
}

function matches(pattern: string, text: string): boolean {
  const sample = text.slice(0, PREVIEW_MAX_CHARS);
  try {
    return new RegExp(pattern, 'i').test(sample);
  } catch {
    return sample.toLowerCase().includes(pattern.toLowerCase());
  }
}

function text(detail: Record<string, unknown>, key: string): string | null {
  const value = detail[key];
  return typeof value === 'string' && value.trim() ? value : null;
}

/**
 * The client-side live preview: what each deterministic step would do with the sample prompt.
 *
 * LLM-backed steps are decided at request time and say so; the preview stops at the first block,
 * as production does.
 */
export function previewOf(steps: PipelineStep[], system: string, user: string): PreviewRow[] {
  const rows: PreviewRow[] = [];
  for (const step of steps) {
    const name = STEP_LABELS[step.type];
    if (step.type !== 'injection_filter') {
      rows.push({
        label: name,
        action: 'runtime',
        note: 'LLM picks a category — decided at request time',
      });
      continue;
    }
    if (step.config.mode === 'llm') {
      rows.push({
        label: name,
        action: 'runtime',
        note: 'LLM classifier — decided at request time',
      });
      continue;
    }
    const scanned = step.config.scope === 'system_user' ? `${system}\n${user}` : user;
    const patterns = [
      ...(step.config.use_builtins !== false ? BUILTIN_REGEXES : []),
      ...(step.config.patterns ?? []),
    ].slice(0, PREVIEW_MAX_PATTERNS);
    const hit = patterns.some((p) => matches(p, scanned));
    const action = hit ? (step.config.action === 'flag' ? 'flagged' : 'blocked') : 'passed';
    rows.push({ label: name, action, note: hit ? 'matched a pattern' : 'no pattern matched' });
    if (action === 'blocked') break;
  }
  return rows;
}

/**
 * What a step decided, in one sentence.
 *
 * Written per step type rather than by dumping the detail map: the same key means different things
 * in different steps (`model` is the model in use for a router and the model *asked* for a
 * redactor, hence `classifier`). An unrecognised type falls back to the action word, so a step
 * this build does not know still renders as itself.
 */
function describe(type: string, action: string, detail: Record<string, unknown>): string {
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

/** A dry run's trace as cards. Empty until a run has been made. */
export function traceCardsOf(result: DryRunResult | null): TraceCard[] {
  return (result?.trace ?? []).map((entry, index) => {
    const detail = entry.detail ?? {};
    return {
      step: index + 1,
      title: STEP_LABELS[entry.type as StepType] ?? entry.type,
      action: entry.action,
      badge: actionClass(entry.action),
      summary: describe(entry.type, entry.action, detail),
      classifier: text(detail, 'classifier'),
      output: text(detail, 'output'),
      before: text(detail, 'before'),
      after: text(detail, 'after'),
      simulated: entry.after_block === true,
    };
  });
}

/**
 * The steps a block stopped the dry run from reaching, numbered on from where the trace ended.
 *
 * `steps` is the configuration the run was made with, not the current one: the two differ exactly
 * when the trace is stale. Empty when the run was not blocked, or was told to keep going and so
 * has a card for every step.
 */
export function notReachedOf(result: DryRunResult | null, steps: PipelineStep[]): NotReachedCard[] {
  if (!result?.blocked || result.trace.length >= steps.length) return [];
  return steps.slice(result.trace.length).map((step, index) => ({
    step: result.trace.length + index + 1,
    title: STEP_LABELS[step.type] ?? step.type,
  }));
}
