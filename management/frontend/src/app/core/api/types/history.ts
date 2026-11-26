/** One change to the configuration the gateway enforces, as the history keeps it (`FRD-632`). */
export interface ConfigurationChange {
  id: number;
  changed_at: string;
  /** The person signed in to the console who made it; empty when no person did. */
  actor: string;
  /** Which process made it when no person did: `seed`, `migrate`, … */
  process: string;
  event_type: string;
  kind: ConfigurationKind;
  action: string;
  object_key: string;
  use_case: string;
  /** The person it is about, where it is about one. */
  person: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  fields_changed: string[];
  trace_id: string;
}

export const CONFIGURATION_KINDS = [
  'use_case',
  'membership',
  'group_grant',
  'api_key',
  'pipeline',
  'budget',
  'rate_limit',
  'anomaly_rule',
  'model',
  'role',
] as const;

export type ConfigurationKind = (typeof CONFIGURATION_KINDS)[number];
