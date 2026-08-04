export interface Me {
  subject: string;
  username: string;
  email: string;
  roles: string[];
  use_cases: string[];
}

export interface UseCase {
  slug: string;
  name: string;
  description: string;
  processing_notes: string;
  created_at?: string;
  updated_at?: string;
}

export interface Membership {
  username: string;
  role: string;
  created_at?: string;
}

export interface ApiKey {
  prefix: string;
  label: string;
  owner: string;
  is_active: boolean;
  created_at?: string;
  revoked_at?: string | null;
}

/** Issue response — the only time the plaintext key is ever returned. */
export interface IssuedApiKey {
  api_key: string;
  prefix: string;
  label: string;
  use_case: string;
}

export type StepType = 'injection_filter' | 'allow_check' | 'model_route';

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
  // allow_check
  models?: string[];
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
}

export interface DryRunResult {
  blocked: boolean;
  block_reason: string | null;
  effective_model: string;
  fallback_models: string[];
  trace: DryRunTraceEntry[];
}
