import { TechnicalRecord, Trace } from '../../core/api/models';

/**
 * A request's technical course in the words a technician reads it in (`FRD-630`): each phase and
 * milestone named once, the numbers left as the gateway recorded them.
 */
const PHASES: Record<string, string> = {
  completed: 'Completed: the provider finished, and the answer was handed over.',
  before_dispatch: 'Ended in AIRA, before any model was asked.',
  waiting_for_provider: 'Ended while waiting for the provider’s first chunk.',
  mid_answer: 'Ended while the provider was still answering.',
  after_provider_finished:
    'Ended after the provider had finished, while the answer was still being handed over.',
};

/** The milestones, as a timeline row names them. */
const MILESTONES: Record<string, string> = {
  dispatched: 'Controls passed, model chain started',
  provider_first_chunk: 'First chunk from the provider',
  provider_finished: 'Provider finished',
  response_started: 'Status and headers sent to the caller',
  last_byte_to_caller: 'Last byte sent to the caller',
  caller_left: 'Caller’s connection closed',
  recorded: 'Recorded',
};

/** The provider's facts, as a row of the provider table names them, in this order. */
const PROVIDER_FIELDS: [keyof NonNullable<TechnicalRecord['provider']>, string][] = [
  ['status', 'HTTP status'],
  ['reason', 'Error word'],
  ['request_id', 'Provider’s request id'],
  ['retry_after', 'Retry-After'],
  ['host', 'Host'],
  ['where', 'Where'],
];

export interface TimelineRow {
  key: string;
  label: string;
  ms: number;
  /** Milliseconds since the row before, `null` for the first. */
  delta: number | null;
}

export interface Fact {
  label: string;
  value: string;
}

/** The phase in a sentence; one this console does not know yet, in its own word. */
export function phaseSentence(phase: string): string {
  return PHASES[phase] ?? `Ended as “${phase.replace(/_/g, ' ')}”.`;
}

/** Milliseconds as a technician compares them: grouped, and in seconds too where that is shorter. */
export function duration(ms: number): string {
  const grouped = `${ms.toLocaleString('en-US')} ms`;
  return ms >= 1000 ? `${grouped} (${(ms / 1000).toFixed(1)} s)` : grouped;
}

/** The milestones the request reached, in the order they came. */
export function timelineRows(record: TechnicalRecord): TimelineRow[] {
  const reached = Object.entries(record.timeline_ms)
    .filter((entry): entry is [string, number] => typeof entry[1] === 'number')
    .sort((a, b) => a[1] - b[1]);
  return reached.map(([key, ms], index) => ({
    key,
    label: MILESTONES[key] ?? key.replace(/_/g, ' '),
    ms,
    delta: index === 0 ? null : ms - reached[index - 1][1],
  }));
}

/**
 * What the timeline says about a connection that closed — the question a `499` leaves open. The
 * silence before it is what tells an idle timeout in a proxy from a limit on a request's length.
 */
export function connectionFacts(record: TechnicalRecord): Fact[] {
  const left = record.timeline_ms['caller_left'];
  if (typeof left !== 'number') return [];
  const facts: Fact[] = [{ label: 'Open for', value: duration(left) }];
  const last = record.timeline_ms['last_byte_to_caller'];
  facts.push({
    label: 'Silence before it closed',
    value:
      typeof last === 'number'
        ? `${duration(Math.max(0, left - last))} since the last byte`
        : 'nothing had been sent',
  });
  return facts;
}

/** What reached the caller, in one line. */
export function sentLine(record: TechnicalRecord): string | null {
  const sent = record.to_caller;
  if (!sent) return null;
  const keepalives = sent.keepalives === 1 ? '1 keepalive' : `${sent.keepalives} keepalives`;
  return `${sent.bytes.toLocaleString('en-US')} bytes in ${sent.writes} writes, ${keepalives}`;
}

/** The provider's facts that were recorded, labelled. */
export function providerFacts(record: TechnicalRecord): Fact[] {
  const provider = record.provider ?? {};
  return PROVIDER_FIELDS.filter(([key]) => provider[key] !== undefined && provider[key] !== '').map(
    ([key, label]) => ({
      label,
      value:
        key === 'where' && provider.where === 'in_stream'
          ? 'inside the stream'
          : `${provider[key]}`,
    }),
  );
}

/** The exception chain as a traceback reads it, outermost first. */
export function chainLine(record: TechnicalRecord): string | null {
  const chain = record.exception?.chain ?? [];
  return chain.length ? chain.join(' ← ') : null;
}

/**
 * Everything above as plain text, for a ticket or a message to the provider's support: the
 * request, its outcome and its course — never who made it, which strict data mode may withhold
 * from the very reader copying it, and which a ticket has no use for.
 */
export function technicalText(trace: Trace): string {
  const record = trace.technical;
  const lines = [
    `AIRA request ${trace.id}`,
    `Time: ${trace.created_at}`,
    `Outcome: HTTP ${trace.status} ${trace.outcome ?? ''}`.trimEnd(),
    `Model: ${trace.model}${trace.provider ? ` via ${trace.provider}` : ''}${
      trace.region ? ` (${trace.region})` : ''
    }`,
  ];
  if (trace.trace_id) lines.push(`Trace id: ${trace.trace_id}`);
  if (record) {
    lines.push(`Phase: ${record.phase}`, 'Timeline (ms since arrival):');
    for (const row of timelineRows(record)) {
      lines.push(`  ${row.key.padEnd(22)} ${String(row.ms).padStart(9)}`);
    }
    for (const fact of connectionFacts(record)) lines.push(`${fact.label}: ${fact.value}`);
    const sent = sentLine(record);
    if (sent) lines.push(`To the caller: ${sent}`);
    const provider = providerFacts(record);
    if (provider.length) {
      lines.push(`Provider: ${provider.map((fact) => `${fact.label}=${fact.value}`).join('; ')}`);
    }
    const chain = chainLine(record);
    if (chain) lines.push(`Exception: ${chain}`);
    if (record.exception?.errno) lines.push(`Operating system: ${record.exception.errno}`);
    if (record.exception?.at) lines.push(`At: ${record.exception.at}`);
  }
  if (trace.error) lines.push(`Caller was told: ${trace.error}`);
  return lines.join('\n');
}
