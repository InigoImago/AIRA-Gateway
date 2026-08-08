import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  AnomalyEvent,
  AnomalyPage,
  AnomalyRule,
  ApiKey,
  Budget,
  BudgetUsage,
  DirectoryResults,
  GroupGrant,
  RateLimit,
  CatalogModel,
  DryRunResult,
  IssuedApiKey,
  Membership,
  Page,
  PipelineConfig,
  Report,
  UseCase,
  Suspension,
  Trace,
  TracePage,
} from './models';

/**
 * Every value interpolated into a URL is encoded: slugs, usernames, and key prefixes come from
 * user input, and an unencoded `/` or `..` would silently retarget the request at a different
 * endpoint (ADR-0007).
 */
const seg = (value: string): string => encodeURIComponent(value);

@Injectable({ providedIn: 'root' })
export class UseCaseService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/v1/use-cases/';

  /**
   * One page of use cases, searched at the server (`FRD-208`).
   *
   * Paged there rather than in the browser because this endpoint computes object-level permissions
   * per row: fetching all of them and slicing locally leaves every one of those computations
   * happening on every load, which is the part that actually takes seconds.
   */
  listPage(query: string, page: number): Observable<Page<UseCase>> {
    const params: Record<string, string | number> = { page };
    if (query) params['q'] = query;
    return this.http.get<Page<UseCase>>('/api/v1/use-cases/', { params });
  }

  list(): Observable<UseCase[]> {
    return this.http.get<UseCase[]>(this.base);
  }

  get(slug: string): Observable<UseCase> {
    return this.http.get<UseCase>(`${this.base}${seg(slug)}/`);
  }

  create(useCase: Partial<UseCase>): Observable<UseCase> {
    return this.http.post<UseCase>(this.base, useCase);
  }

  update(slug: string, changes: Partial<UseCase>): Observable<UseCase> {
    return this.http.patch<UseCase>(`${this.base}${seg(slug)}/`, changes);
  }

  remove(slug: string): Observable<void> {
    return this.http.delete<void>(`${this.base}${seg(slug)}/`);
  }

  members(slug: string): Observable<Membership[]> {
    return this.http.get<Membership[]>(`${this.base}${seg(slug)}/members/`);
  }

  /** Access granted to Keycloak groups on this use case (`FRD-209`). */
  groupGrants(slug: string): Observable<GroupGrant[]> {
    return this.http.get<GroupGrant[]>(`${this.base}${seg(slug)}/groups/`);
  }

  grantGroup(slug: string, groupPath: string, role: string): Observable<GroupGrant> {
    return this.http.post<GroupGrant>(`${this.base}${seg(slug)}/groups/`, {
      group_path: groupPath,
      role,
    });
  }

  /**
   * Revoke a group grant.
   *
   * The path travels in the **query string**: a Keycloak group path contains slashes, and encoding
   * one into a path segment produces a route that works until somebody has a group two levels
   * deep.
   */
  revokeGroup(slug: string, groupPath: string): Observable<void> {
    return this.http.delete<void>(`${this.base}${seg(slug)}/groups/revoke/`, {
      params: { group_path: groupPath },
    });
  }

  /** Search Keycloak for groups and people a grant could name (`FRD-209` §3). */
  directory(query: string): Observable<DirectoryResults> {
    return this.http.get<DirectoryResults>('/api/v1/directory/', { params: { q: query } });
  }

  addMember(slug: string, username: string, role: string): Observable<Membership> {
    return this.http.post<Membership>(`${this.base}${seg(slug)}/members/`, { username, role });
  }

  removeMember(slug: string, username: string): Observable<void> {
    return this.http.delete<void>(`${this.base}${seg(slug)}/members/${seg(username)}/`);
  }

  apiKeys(slug: string): Observable<ApiKey[]> {
    return this.http.get<ApiKey[]>(`${this.base}${seg(slug)}/api-keys/`);
  }

  /**
   * Issue a key. `expiresInDays` is optional and **omitted when absent** rather than sent as null:
   * a key with no end date is what every key issued before expiry existed carries, and what the
   * break-glass credential needs.
   */
  issueApiKey(
    slug: string,
    label: string,
    expiresInDays?: number | null,
  ): Observable<IssuedApiKey> {
    const body: { label: string; expires_in_days?: number } = { label };
    if (expiresInDays) {
      body.expires_in_days = expiresInDays;
    }
    return this.http.post<IssuedApiKey>(`${this.base}${seg(slug)}/api-keys/`, body);
  }

  revokeApiKey(slug: string, prefix: string): Observable<void> {
    return this.http.delete<void>(`${this.base}${seg(slug)}/api-keys/${seg(prefix)}/`);
  }

  getPipeline(slug: string): Observable<PipelineConfig> {
    return this.http.get<PipelineConfig>(`${this.base}${seg(slug)}/pipeline/`);
  }

  savePipeline(slug: string, config: PipelineConfig): Observable<PipelineConfig> {
    return this.http.put<PipelineConfig>(`${this.base}${seg(slug)}/pipeline/`, config);
  }

  /** Dry-run a (possibly unsaved) pipeline against a sample prompt via the gateway. */
  dryRunPipeline(payload: {
    system: string;
    user: string;
    model?: string;
    pipeline: PipelineConfig;
  }): Observable<DryRunResult> {
    return this.http.post<DryRunResult>('/gw/v1beta/pipeline:dryRun', payload);
  }

  budgets(slug: string): Observable<Budget[]> {
    return this.http.get<Budget[]>(`${this.base}${seg(slug)}/budgets/`);
  }

  createBudget(slug: string, budget: Budget): Observable<Budget> {
    return this.http.post<Budget>(`${this.base}${seg(slug)}/budgets/`, budget);
  }

  deleteBudget(slug: string, id: number): Observable<void> {
    return this.http.delete<void>(`${this.base}${seg(slug)}/budgets/${id}/`);
  }

  rateLimits(slug: string): Observable<RateLimit[]> {
    return this.http.get<RateLimit[]>(`${this.base}${seg(slug)}/rate-limits/`);
  }

  createRateLimit(slug: string, limit: RateLimit): Observable<RateLimit> {
    return this.http.post<RateLimit>(`${this.base}${seg(slug)}/rate-limits/`, limit);
  }

  deleteRateLimit(slug: string, id: number): Observable<void> {
    return this.http.delete<void>(`${this.base}${seg(slug)}/rate-limits/${id}/`);
  }

  /**
   * The **whole** catalog, deliberately unpaged (`FRD-208`).
   *
   * Bounded by how many models an organisation has contracted — tens, not thousands — and the
   * screen's two warnings count over all of it ("N have no price on file"). Paging it at the
   * server would turn those into "N on this page", a figure that means nothing. The console
   * searches and pages this one in the browser, which it can honestly do because it has it all.
   */
  models(): Observable<CatalogModel[]> {
    return this.http.get<CatalogModel[]>('/api/v1/models/');
  }

  saveModel(model: CatalogModel): Observable<CatalogModel> {
    return this.http.post<CatalogModel>('/api/v1/models/', model);
  }

  removeModel(name: string): Observable<void> {
    return this.http.delete<void>(`/api/v1/models/${seg(name)}/`);
  }

  /**
   * Spend and usage over a window, from the gateway (FRD-601).
   *
   * The window is half-open — `to` is excluded — so two adjacent periods never both contain the
   * same request. What the caller is shown is decided by their token, not by this call.
   */
  report(from: string, to: string): Observable<Report> {
    return this.http.get<Report>('/gw/v1beta/reporting', { params: { from, to } });
  }

  /**
   * The same report as a spreadsheet (FRD-602).
   *
   * A blob rather than a plain link, because the endpoint needs the bearer token and an `<a href>`
   * carries no Authorization header — a download link that 401s is worse than no link, since it
   * looks like the export is broken rather than like the browser cannot authenticate.
   */
  reportCsv(from: string, to: string, breakdown: string): Observable<Blob> {
    return this.http.get('/gw/v1beta/reporting', {
      params: { from, to, breakdown },
      headers: { Accept: 'text/csv' },
      responseType: 'blob',
    });
  }

  /**
   * What the detector has found (`FRD-501` FR-8).
   *
   * Scoped by the caller's token: an oversight role sees every use case, a member sees the ones
   * they belong to, and somebody with neither gets an empty list rather than a refusal.
   */
  anomalies(limit = 50, useCase?: string, cursor?: string): Observable<AnomalyPage> {
    const params: Record<string, string | number> = { limit };
    // Cursor, not offset — findings are an append-only log, so a detector firing while somebody
    // reads page two pushes rows across the boundary and they see one twice and miss another.
    if (cursor) params['cursor'] = cursor;
    // Asked for by name rather than filtered in the browser: a console that fetched the newest
    // hundred findings and kept the matching ones would show a quiet use case nothing on a busy
    // installation, because somebody else's findings pushed its own off the end.
    if (useCase) params['use_case'] = useCase;
    return this.http.get<AnomalyPage>('/gw/v1beta/anomalies', { params });
  }

  /** Traffic that is currently stopped, and what was stopped before (`FRD-503`). */
  suspensions(): Observable<{ suspensions: Suspension[] }> {
    return this.http.get<{ suspensions: Suspension[] }>('/gw/v1beta/suspensions');
  }

  /** Stop a subject, a credential or a use case. Needs an incident role; the server decides. */
  suspend(body: {
    target: string;
    target_value: string;
    action?: string;
    throttle_rpm?: number | null;
    minutes?: number | null;
    reason?: string;
    use_case?: string | null;
  }): Observable<Suspension> {
    return this.http.post<Suspension>('/gw/v1beta/suspensions', body);
  }

  /** Lift one. The row is kept and stamped, never deleted. */
  liftSuspension(id: string): Observable<Suspension> {
    return this.http.delete<Suspension>(`/gw/v1beta/suspensions/${seg(id)}`);
  }

  /**
   * What actually happened, request by request (`FRD-502`).
   *
   * Metadata only — never a payload. Paged by cursor rather than offset for the reason the type
   * comment gives.
   */
  traces(options: {
    useCase?: string;
    outcome?: string;
    refusalsOnly?: boolean;
    /** The three an incident starts with: which system, whose identity, which machine. */
    credential?: string;
    subject?: string;
    sourceIp?: string;
    /** Only my own requests — offered to every role, including those that see everything. */
    mine?: boolean;
    /** Only the turns where the model asked for a function. */
    toolsOnly?: boolean;
    cursor?: string;
    limit?: number;
  }): Observable<TracePage> {
    const params: Record<string, string | number | boolean> = { limit: options.limit ?? 50 };
    if (options.useCase) params['use_case'] = options.useCase;
    if (options.outcome) params['outcome'] = options.outcome;
    if (options.refusalsOnly) params['refusals_only'] = true;
    if (options.credential) params['credential'] = options.credential;
    if (options.subject) params['subject'] = options.subject;
    if (options.sourceIp) params['source_ip'] = options.sourceIp;
    if (options.mine) params['mine'] = true;
    if (options.toolsOnly) params['tools_only'] = true;
    if (options.cursor) params['cursor'] = options.cursor;
    return this.http.get<TracePage>('/gw/v1beta/traces', { params });
  }

  /** The anomaly rules of one use case. Members read; whoever manages it writes. */
  useCaseRules(slug: string): Observable<AnomalyRule[]> {
    return this.http.get<AnomalyRule[]>(`${this.base}${seg(slug)}/anomaly-rules/`);
  }

  /**
   * Create or replace a rule on one use case.
   *
   * The server upserts **by name** (`upsert_use_case_rule`), which is why the form keeps a rule's
   * name fixed once it exists: renaming one would silently create a second and leave the first
   * watching.
   */
  saveUseCaseRule(slug: string, rule: Partial<AnomalyRule>): Observable<AnomalyRule> {
    return this.http.post<AnomalyRule>(`${this.base}${seg(slug)}/anomaly-rules/`, rule);
  }

  deleteUseCaseRule(slug: string, id: number): Observable<void> {
    return this.http.delete<void>(`${this.base}${seg(slug)}/anomaly-rules/${id}`);
  }

  /** Anomaly rules that apply everywhere, plus the ones on use cases the caller may see. */
  globalRules(): Observable<AnomalyRule[]> {
    return this.http.get<AnomalyRule[]>('/api/v1/anomaly-rules/');
  }

  /**
   * Change a rule. The server decides who may: a global rule needs an incident role, a use-case
   * rule needs to manage that use case (`AnomalyRuleViewSet._guard`).
   *
   * `PATCH`, not `PUT`: a rule has thirteen fields and most edits touch one of them. Sending the
   * whole object back would make every save a chance to overwrite a field the form never showed.
   */
  updateRule(id: number, changes: Partial<AnomalyRule>): Observable<AnomalyRule> {
    return this.http.patch<AnomalyRule>(`/api/v1/anomaly-rules/${id}/`, changes);
  }

  deleteRule(id: number): Observable<void> {
    return this.http.delete<void>(`/api/v1/anomaly-rules/${id}/`);
  }

  /** Current-period consumption per budget, from the gateway. */
  budgetUsage(slug: string): Observable<{ usage: BudgetUsage[] }> {
    return this.http.get<{ usage: BudgetUsage[] }>(`/gw/v1beta/usage/${seg(slug)}`);
  }
}
