/**
 * What the signed-in caller may do inside one use case, as the server answers it.
 *
 * Object-level (django-guardian) permissions, so not derivable from `/me`: the console is told
 * rather than guessing, and offers no control the server would refuse.
 */
export interface UseCasePermissions {
  /** May rename or delete the use case itself. */
  can_admin: boolean;
  /** May change what happens inside it: members, keys, pipeline, budgets, limits. */
  can_manage: boolean;
  /** Actually belongs to it — which is what issuing an API key requires, and seeing it is not. */
  is_member: boolean;
  /**
   * Whether the **gateway** would accept this person's token for this use case.
   *
   * Not a phrasing of `is_member`: Management counts membership rows and grants a global
   * administrator everything, while the gateway reads only the Keycloak groups in the token.
   */
  may_call?: boolean;
}

export interface UseCase {
  permissions?: UseCasePermissions;
  slug: string;
  name: string;
  description: string;
  processing_notes: string;
  /** Whether prompts and responses are stored at all (FRD-404). */
  store_payloads?: boolean;
  /** Let this use case declare functions for the model to call (FRD-131). */
  tools_enabled?: boolean;
  /** Whether a model's reasoning is returned and stored with the answer (`FRD-135`). */
  include_reasoning?: boolean;
  /** Mark this use case's stable prefix as cacheable at the provider (FRD-133). */
  prompt_caching_enabled?: boolean;
  /** How long the provider keeps it: `5m` or `1h`. */
  prompt_cache_ttl?: string;
  /** Show each use-case *user* only their own requests. An administrator still sees all of them. */
  restrict_members_to_own_requests?: boolean;
  /** How long stored prompts and responses are kept, in days (FRD-404). */
  retention_days?: number;
  /**
   * Which catalogued models this use case may call (`FRD-308`).
   *
   * **Empty means none**: absence of a release is not a release. `undefined` means the server did
   * not send the field, and must not be rendered as an empty release.
   */
  allowed_models?: string[];
  created_at?: string;
  updated_at?: string;
}

/** One thing a grant can name — a Keycloak group, or a person (`FRD-209`). */
export interface DirectoryEntry {
  kind: 'group' | 'user';
  /** What the grant stores: a group path, or a username. */
  id: string;
  label: string;
  detail: string;
}

export interface DirectoryResults {
  results: DirectoryEntry[];
  /**
   * Where the answer came from. `local` means Keycloak could not be asked and this is what
   * Management already knows — a real subset, never a guess — and the console says so, because
   * "no results" from a degraded directory and "no such group" are different answers.
   */
  source: 'keycloak' | 'local' | 'none';
  hint?: string;
}

/** Access granted to a Keycloak group rather than to a person (`FRD-209`). */
export interface GroupGrant {
  group_path: string;
  role: 'admin' | 'user';
  granted_by: string;
  /**
   * How many people **Management has seen sign in** this grant currently reaches — not the
   * group's true size, which only the identity provider knows. It makes a grant that reaches
   * nobody visible rather than silently inert.
   */
  reaches: number;
  created_at?: string;
}

export interface Membership {
  username: string;
  role: string;
  created_at?: string;
}

/**
 * What ending somebody's access actually did (`FRD-613`).
 *
 * Removing a member or revoking a group grant also revokes every API key of this use case whose
 * owner no longer holds a grant on it; the prefixes come back so the panel can name them.
 */
export interface AccessChange {
  revoked_keys: string[];
}

export interface ApiKey {
  prefix: string;
  label: string;
  /** Who **answers for** the credential — the name every audit row carries (`FRD-604`). */
  owner: string;
  /** The human who created it, when that is not the owner (a team's shared credential). */
  issued_by?: string;
  is_active: boolean;
  created_at?: string;
  revoked_at?: string | null;
  /**
   * When it stops working on its own; `null` means never (the default, and what the break-glass
   * credential needs). Kept apart from revocation: "it lapsed as planned" and "we took it away"
   * are different answers to an audit.
   */
  expires_at?: string | null;
}

/** Issue response — the only time the plaintext key is ever returned. */
export interface IssuedApiKey {
  api_key: string;
  prefix: string;
  label: string;
  use_case: string;
  expires_at?: string | null;
  owner?: string;
  issued_by?: string;
}
