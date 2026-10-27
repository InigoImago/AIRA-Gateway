import { PipelineDecision, Trace } from '../../core/api/models';

/**
 * Each outcome as a sentence (`FRD-628` FR-5). Strict data mode leaves the people who run a use
 * case without its prompt and answer, so the outcome has to explain a request on its own.
 */
const OUTCOME_SENTENCES: Record<string, string> = {
  served: 'The request was answered.',
  rate_limited: 'A rate limit refused this request: too many requests in too short a time.',
  budget_exceeded:
    'A budget refused this request: its limit for the period is spent, or this request would not fit in what is left.',
  suspended: 'A suspension covers this caller, key or use case, so the request was refused.',
  blocked_by_pipeline: 'A pipeline step refused this request.',
  no_capable_model: 'No released model can do what this request asks for, so no model was asked.',
  model_not_found: 'The model asked for is not known here, or not released to this use case.',
  invalid_request: 'The request was malformed, and was refused before any model was asked.',
  request_too_large: 'The request was larger than the gateway accepts.',
  upstream_error:
    'The model provider did not deliver an answer: it returned an error or broke the answer off, it could not be reached, or AIRA stopped waiting for it.',
  internal_error:
    'AIRA itself failed while handling this request — a defect in the gateway, not the caller’s doing and not the provider’s.',
  client_gone:
    'The caller’s connection closed before the answer was complete: the caller cancelled — an assistant does when its user stops it — or a timeout in the client, or in a proxy between the caller and AIRA, closed it.',
  diagnostic:
    'A model check, made to see whether the model answers — not a request of the use case.',
};

/** The same outcomes in two or three words, for a list badge that is scanned rather than read. */
const OUTCOME_LABELS: Record<string, string> = {
  served: 'served',
  rate_limited: 'rate limit',
  budget_exceeded: 'budget',
  suspended: 'suspended',
  blocked_by_pipeline: 'refused by pipeline',
  no_capable_model: 'no capable model',
  model_not_found: 'unknown model',
  invalid_request: 'invalid request',
  request_too_large: 'too large',
  upstream_error: 'provider error',
  internal_error: 'AIRA error',
  client_gone: 'caller left',
  diagnostic: 'model check',
};

/**
 * Where a request that was not served ended — the first question a reader has: was it the request,
 * a rule of AIRA's, AIRA itself, the model provider, or the caller's side of the connection? The
 * sentence above it says what happened; this says whose it was.
 */
const OUTCOME_PARTIES: Record<string, string> = {
  rate_limited: 'In AIRA, on purpose: a rule of this use case or installation.',
  budget_exceeded: 'In AIRA, on purpose: a rule of this use case or installation.',
  suspended: 'In AIRA, on purpose: a rule of this use case or installation.',
  blocked_by_pipeline: 'In AIRA, on purpose: a rule of this use case or installation.',
  no_capable_model:
    'In AIRA’s configuration: the models released to the use case, or the catalogue.',
  model_not_found:
    'In the request, or the use case’s configuration: the model named is not served to it.',
  invalid_request: 'In the request: the caller has to change it.',
  request_too_large: 'In the request: the caller has to send less.',
  upstream_error: 'At the model provider — or in AIRA’s wait for it; the lines below say which.',
  internal_error: 'In AIRA.',
  client_gone: 'On the caller’s side of the connection.',
};

/** The pipeline steps by the name a reader knows them under in the builder. */
const STEP_NAMES: Record<string, string> = {
  injection_filter: 'Prompt-injection filter',
  model_route: 'Routing',
  pii_filter: 'Personal data',
  notice: 'Notice',
};

/** Why a classifier was not asked, in words. */
const NOT_ASKED: Record<string, string> = {
  classifier_failed: 'classifier unavailable',
  no_classifier: 'no classifier configured',
};

/** Decisions that are not a pipeline step's: the model chain, and a notice added to the answer. */
const NOT_A_STEP = new Set(['dispatch', 'notice']);

/** How a line reads at a glance: as expected, objected to, or refused. */
export type Tone = 'plain' | 'warning' | 'danger';

export interface StoryLine {
  text: string;
  tone: Tone;
}

/** A word the console does not know yet, readable rather than dropped: `foo_bar` → `foo bar`. */
function words(value: string): string {
  return value.replace(/_/g, ' ');
}

/** The sentence for an outcome; one the console does not know is still said, in its own words. */
export function outcomeSentence(outcome: string | null, status: number): string {
  if (!outcome) {
    return status >= 200 && status < 300
      ? 'The request was answered.'
      : `The request ended with status ${status}; this row predates the recorded outcome.`;
  }
  return OUTCOME_SENTENCES[outcome] ?? `The request ended as “${words(outcome)}”.`;
}

/**
 * Whose a request's ending was, for one that was not served; `null` for an answer, a model check,
 * and an outcome the console does not know — saying nothing rather than a guess.
 */
export function outcomeParty(outcome: string | null): string | null {
  return outcome ? (OUTCOME_PARTIES[outcome] ?? null) : null;
}

/** The short badge for an outcome. */
export function outcomeLabel(outcome: string): string {
  return OUTCOME_LABELS[outcome] ?? words(outcome);
}

/** `fallback:2` → `fallback 2`; `route` → `routed`; `direct` says nothing worth a mark. */
function selectionMark(selection: string | null): string {
  if (!selection || selection === 'direct') return '';
  if (selection === 'route') return ' (routed)';
  return ` (${selection.replace(':', ' ')})`;
}

function because(why: string | undefined): string {
  return why ? ` — ${why}` : '';
}

function injection(decision: PipelineDecision): StoryLine {
  const verdict = decision.why ? ` (verdict: ${decision.why})` : '';
  if (decision.action === 'blocked') return { text: `refused${verdict}`, tone: 'danger' };
  if (decision.flagged) return { text: `flagged and let through${verdict}`, tone: 'warning' };
  return { text: 'passed', tone: 'plain' };
}

function routing(decision: PipelineDecision): StoryLine | null {
  const category = decision.category ? ` (category ${decision.category})` : '';
  if (decision.from && decision.to) {
    return { text: `sent from ${decision.from} to ${decision.to}${category}`, tone: 'plain' };
  }
  if (decision.action === 'unchanged') {
    if (decision.why === 'no_category_matched') {
      return { text: 'kept the model (no category matched)', tone: 'plain' };
    }
    const matched = decision.category
      ? ` (category ${decision.category} routes to the model asked for)`
      : '';
    return { text: `kept the model${matched}`, tone: 'plain' };
  }
  if (decision.action === 'not_asked') {
    const reason = decision.why ? (NOT_ASKED[decision.why] ?? words(decision.why)) : '';
    return { text: `not asked${reason ? ` (${reason})` : ''}`, tone: 'warning' };
  }
  return null;
}

function personalData(decision: PipelineDecision): StoryLine | null {
  switch (decision.action) {
    case 'redacted': {
      // Over an embedding batch the step says how many texts it changed; for one prompt, only that
      // it did.
      const counted =
        typeof decision.changed === 'number' && typeof decision.texts === 'number'
          ? ` in ${decision.changed} of ${decision.texts} texts`
          : '';
      const by = decision.why ? ` (found by ${decision.why})` : '';
      return { text: `replaced${counted}${by}`, tone: 'plain' };
    }
    case 'unchanged':
      return { text: 'nothing to replace', tone: 'plain' };
    case 'blocked':
      return { text: `could not be removed — refused${because(decision.why)}`, tone: 'danger' };
    case 'allowed':
      return {
        text: `could not be removed — sent unchanged (configured to allow)${because(decision.why)}`,
        tone: 'warning',
      };
    default:
      return null;
  }
}

/** A candidate of the model chain the request did not end at. */
function candidate(decision: PipelineDecision): StoryLine | null {
  const model = decision.to ?? 'a model';
  if (decision.action === 'skipped') {
    return { text: `Model ${model}: skipped${because(decision.why)}`, tone: 'warning' };
  }
  if (decision.action === 'failed') {
    return { text: `Model ${model}: failed${because(decision.why)}`, tone: 'warning' };
  }
  return null;
}

/**
 * A decision the console has no words for: every key it carries, so a step added to the gateway
 * after this console still appears in the story instead of vanishing from it.
 */
function generic(decision: PipelineDecision): StoryLine {
  const name = decision.step ? (STEP_NAMES[decision.step] ?? words(decision.step)) : 'A step';
  const rest = Object.entries(decision)
    .filter(([key]) => key !== 'step')
    .map(([key, value]) => `${words(key)} ${String(value)}`);
  return { text: `${name}: ${rest.length ? rest.join(', ') : 'ran'}`, tone: 'plain' };
}

/** One decision as one line of the story. */
export function decisionLine(decision: PipelineDecision): StoryLine {
  const name = decision.step ? STEP_NAMES[decision.step] : undefined;
  let said: StoryLine | null = null;
  switch (decision.step) {
    case 'injection_filter':
      said = injection(decision);
      break;
    case 'model_route':
      said = routing(decision);
      break;
    case 'pii_filter':
      said = personalData(decision);
      break;
    case 'dispatch':
      return candidate(decision) ?? generic(decision);
    case 'notice':
      if (decision.action === 'shown' || decision.action === 'withheld') {
        said = { text: `${decision.action}${because(decision.why)}`, tone: 'plain' };
      }
      break;
  }
  return said && name ? { ...said, text: `${name}: ${said.text}` } : generic(decision);
}

/**
 * The whole story of a request, in order: each decision, and — for an answer — which model gave it,
 * placed after the candidates passed over and before the notice added to the answer.
 */
export function storyOf(trace: Trace): StoryLine[] {
  const decisions = trace.pipeline_decisions ?? [];
  const lines = decisions.map(decisionLine);
  if (trace.outcome === 'served' && trace.model) {
    const answered: StoryLine = {
      text: `Answered by ${trace.model}${selectionMark(trace.model_selection)}`,
      tone: 'plain',
    };
    const lastCandidate = decisions.map((d) => d.step).lastIndexOf('dispatch');
    const firstNotice = decisions.findIndex((d) => d.step === 'notice');
    const at =
      lastCandidate >= 0 ? lastCandidate + 1 : firstNotice >= 0 ? firstNotice : lines.length;
    lines.splice(at, 0, answered);
  }
  return lines;
}

/** Which controls ran on a fallback, as one sentence; `null` when every control ran as configured. */
export function degradedLine(degraded: Record<string, string> | null | undefined): string | null {
  const entries = Object.entries(degraded ?? {});
  if (!entries.length) return null;
  const named = entries.map(([control, fallback]) => `${words(control)} (on ${fallback})`);
  return `Some controls ran on a fallback: ${named.join(', ')}.`;
}

/**
 * Whether a pipeline step — not the model chain, not a notice — decided something about this
 * request: what makes trying the pipeline with one's own text worth offering (`FRD-628` FR-7).
 */
export function pipelineDecided(trace: Trace): boolean {
  return (trace.pipeline_decisions ?? []).some(
    (decision) => !!decision.step && !NOT_A_STEP.has(decision.step),
  );
}
