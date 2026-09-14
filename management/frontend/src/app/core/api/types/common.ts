/**
 * One page of a list the server paged (`FRD-208`).
 *
 * `count` is the total across every page, not the length of `results` — a list that does not say
 * how much it is not showing reads as complete.
 */
export interface Page<T> {
  count: number;
  page: number;
  page_size: number;
  pages: number;
  results: T[];
}

/** The signed-in caller, as `/v1/me` answers. */
export interface Me {
  subject: string;
  username: string;
  email: string;
  roles: string[];
  /**
   * The caller's installation-wide permissions, which the server enforces (`FRD-614`). The console
   * asks these through `can()` in `core/auth/roles.ts`, never the roles.
   */
  permissions?: string[];
  /**
   * Every role's name, by slug (`FRD-614` FR-10), so no screen restates one. A slug missing here is
   * shown as the slug.
   */
  role_labels?: Record<string, string>;
  use_cases: string[];
  /** The installation's key policy, so a form states what the server enforces (`ADR-0015`). */
  api_key_default_days?: number;
  api_key_max_days?: number;
  /** The unit every money figure is in (`AIRA_CURRENCY`). Read through `MeService.currency`. */
  currency?: string;
  /**
   * Whether to offer the pipeline-tests screen (`ADR-0020`).
   *
   * The server's `MayRunTests`: an object-level answer (administration of a use case) that neither
   * the roles nor `use_cases` can reproduce, so the console does not derive it.
   */
  may_test?: boolean;
}
