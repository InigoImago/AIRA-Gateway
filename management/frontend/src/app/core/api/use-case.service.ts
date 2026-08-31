import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, map } from 'rxjs';
import {
  AccessChange,
  AnomalyEvent,
  AnomalyPage,
  AnomalyRule,
  ApiKey,
  Budget,
  BudgetUsage,
  DirectoryResults,
  GatewayConfiguration,
  GatewayProvider,
  GroupGrant,
  OfferedModel,
  RateLimit,
  CatalogModel,
  KiraModel,
  ServedModel,
  DryRunResult,
  IssuedApiKey,
  Membership,
  ModelCheck,
  ThinkingLevelCheck,
  Page,
  PipelineConfig,
  Register,
  Report,
  UseCase,
  Suspension,
  TestAttribution,
  TestCase,
  TestModelStats,
  TestResult,
  TestRun,
  Trace,
  TracePage,
  TracePayload,
} from './models';
import { API, GW } from './prefixes';

/**
 * Every value interpolated into a URL is encoded: slugs, usernames, and key prefixes come from
 * user input, and an unencoded `/` or `..` would silently retarget the request at a different
 * endpoint (ADR-0007).
 */
const seg = (value: string): string => encodeURIComponent(value);

/**
 * Where a model lives, as the **form** currently says — not as the catalogue has it stored.
 *
 * Both checks below take it, because both are buttons inside an editor. Without it they answered
 * about the saved row: somebody correcting a model's provider from `generative-language` to
 * `vertex`, typing a region and pressing Check was told *"Declared, but nothing serves it"* about
 * the declaration they were in the middle of replacing. Right about the wrong thing.
 */
export interface Provenance {
  provider?: string;
  publisher?: string;
  region?: string;
}

/** Only what was actually given: an empty string would override a stored value with nothing. */
function provenanceParams(where: Provenance): Record<string, string> {
  const params: Record<string, string> = {};
  for (const key of ['provider', 'publisher', 'region'] as const) {
    const value = where[key]?.trim();
    if (value) params[key] = value;
  }
  return params;
}

@Injectable({ providedIn: 'root' })
export class UseCaseService {
  private readonly http = inject(HttpClient);
  private readonly base = `${API}/v1/use-cases/`;

  /**
   * Which use cases this caller may put the catalogue to, and why not where they may not
   * (`FRD-504`, `ADR-0020`).
   *
   * **Three comments stood here and one of them was about this method.** The paragraph about
   * server-side paging belongs to `listPage` below and had drifted up here; a second described
   * where a run is booked, which is `startTestRun` two hundred lines down. A doc comment names the
   * thing beneath it, so a block that moves without its subject is a paragraph that now describes
   * something else — the shape `LESSONS.md` §1 records for code (*"a copied block whose subject
   * changed"*), arriving in the prose, where nothing type-checks it.
   */
  testAttribution(): Observable<TestAttribution[]> {
    return this.http.get<TestAttribution[]>(`${API}/v1/test-attribution/`);
  }

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
    return this.http.get<Page<UseCase>>(`${API}/v1/use-cases/`, { params });
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
  revokeGroup(slug: string, groupPath: string): Observable<AccessChange> {
    return this.http.delete<AccessChange>(`${this.base}${seg(slug)}/groups/revoke/`, {
      params: { group_path: groupPath },
    });
  }

  /** Search Keycloak for groups and people a grant could name (`FRD-209` §3). */
  directory(query: string): Observable<DirectoryResults> {
    return this.http.get<DirectoryResults>(`${API}/v1/directory/`, { params: { q: query } });
  }

  addMember(slug: string, username: string, role: string): Observable<Membership> {
    return this.http.post<Membership>(`${this.base}${seg(slug)}/members/`, { username, role });
  }

  /**
   * Take somebody's access away — and, with it, the keys that rested on it.
   *
   * The answer carries `revoked_keys` because the server revokes them (`FRD-613`): a removal that
   * silently deactivated two of somebody's credentials would be a control whose whole effect the
   * screen cannot state, which is the `FRD-206` shape read backwards.
   */
  removeMember(slug: string, username: string): Observable<AccessChange> {
    return this.http.delete<AccessChange>(`${this.base}${seg(slug)}/members/${seg(username)}/`);
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
    owner?: string | null,
  ): Observable<IssuedApiKey> {
    const body: { label: string; expires_in_days?: number; owner?: string } = { label };
    if (expiresInDays) {
      body.expires_in_days = expiresInDays;
    }
    // Omitted when absent, like the lifetime: the ordinary case is that you own what you create,
    // and sending your own name would put a distinction on every key that nobody asked for.
    if (owner) {
      body.owner = owner;
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

  /**
   * Run a (possibly unsaved) pipeline against a sample prompt.
   *
   * `use_case` is **required by the gateway**, not decoration: a dry run runs the real engine, so
   * an LLM-backed step calls a real model and spends real tokens. Until 2026-08-11 it did that for
   * any model named in the body, with no use case, no release check and no audit row — so the
   * endpoint now belongs to a use case exactly as a request does (`FRD-308`).
   */
  dryRunPipeline(payload: {
    use_case: string;
    system: string;
    user: string;
    model?: string;
    pipeline: PipelineConfig;
    /** Keep evaluating after a step refuses. Costs real tokens for steps production never runs. */
    past_blocks?: boolean;
  }): Observable<DryRunResult> {
    return this.http.post<DryRunResult>(`${GW}/v1beta/pipeline:dryRun`, payload);
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

  /**
   * The installation's own budgets (`FRD-610`) — its own route, because this bucket has no slug.
   *
   * `/use-cases/<slug>/budgets/` resolves an object from the path; bending it to accept an absent
   * slug would make *"which use case is this for"* a question with a special answer at every layer
   * that asks it. The server answers an empty list to a caller without an oversight role rather
   * than refusing: what the installation spends on its own diagnostics is not their business, and
   * a 403 would tell them there is something here to want.
   */
  installationBudgets(): Observable<Budget[]> {
    return this.http.get<Budget[]>(`${API}/v1/installation-budgets/`);
  }

  /** Upsert on the period, which is the only thing that distinguishes two of these. */
  saveInstallationBudget(budget: Budget): Observable<Budget> {
    return this.http.post<Budget>(`${API}/v1/installation-budgets/`, budget);
  }

  deleteInstallationBudget(id: number): Observable<void> {
    return this.http.delete<void>(`${API}/v1/installation-budgets/${id}/`);
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
    return this.http.get<CatalogModel[]>(`${API}/v1/models/`);
  }

  saveModel(model: CatalogModel): Observable<CatalogModel> {
    return this.http.post<CatalogModel>(`${API}/v1/models/`, model);
  }

  /**
   * The models as the compatibility surface addresses them, with their integer ids (`FRD-107`).
   *
   * Asked of the gateway for the same reason `servedModels` is: which models are reachable, and
   * under which number, is a property of the gateway. The catalog holds `numeric_id` too and is
   * readable only by the catalog roles — so an administrator of a use case, the person who
   * actually sets a client up, could not get at it there.
   */
  kiraModels(): Observable<KiraModel[]> {
    return this.http.get<KiraModel[]>(`${GW}/kira/api/external/models`);
  }

  /**
   * What the **gateway** actually serves, with the provenance its adapters were configured with
   * (`FRD-507`).
   *
   * Asked of the gateway rather than of Management, because which models are reachable is a
   * property of the gateway's configuration and nothing else knows it. The same `/gw` proxy the
   * dry-run, usage and reporting views already use, so the browser's token carries through.
   */
  servedModels(): Observable<ServedModel[]> {
    return this.http.get<{ models: ServedModel[] }>(`${GW}/v1beta/models`).pipe(
      map((body) =>
        (body.models ?? []).map((model) => ({
          ...model,
          // **The `models/` prefix is Google's resource form, not a model name.** The gateway
          // serves that listing in Gemini's shape, and carrying the prefix across this boundary
          // would have catalogued `models/mock-1` — an entry no request can ever match, and one
          // that looks right in the table. Stripped here, at the edge where the wire shape stops
          // and the console's vocabulary starts.
          name: model.name.replace(/^models\//, ''),
        })),
      ),
    );
  }

  /**
   * Which upstreams this gateway is configured with (`FRD-507` stage C).
   *
   * Asked of the gateway for the same reason the served list is: which providers exist is a
   * property of the gateway's configuration and nothing else knows it. Management could hold a
   * hard-coded vocabulary of "supported platforms" instead, and it would be a list of what the
   * *product* supports rather than what *this installation* has — which is the difference between
   * a dropdown that helps and one that offers a provider no credential reaches.
   */
  providers(): Observable<GatewayConfiguration> {
    return this.http.get<GatewayConfiguration>(`${GW}/v1beta/providers`).pipe(
      map((body) => ({
        providers: body.providers ?? [],
        // **Empty is not "anything goes".** An older gateway does not send this field, and a
        // console that read the absence as an empty allow-list would refuse every region a
        // reader typed. Absence means *this gateway did not say*, and the console then does not
        // pretend to know — the gateway still refuses at request time, as it always did.
        allowedRegions: body.allowedRegions ?? [],
      })),
    );
  }

  /**
   * What one provider says it offers this installation's credential.
   *
   * A separate call, made only when a provider is chosen: one key here answered with **50
   * models**, and a page that fetched every provider's catalogue on load would spend a remote
   * round trip per upstream to fill a dropdown nobody had opened.
   */
  providerOfferings(provider: string): Observable<OfferedModel[]> {
    return this.http
      .get<{ models: OfferedModel[] }>(`${GW}/v1beta/providers/${seg(provider)}/offerings`)
      .pipe(map((body) => body.models ?? []));
  }

  removeModel(name: string): Observable<void> {
    return this.http.delete<void>(`${API}/v1/models/${seg(name)}/`);
  }

  /**
   * Spend and usage over a window, from the gateway (FRD-601).
   *
   * The window is half-open — `to` is excluded — so two adjacent periods never both contain the
   * same request. What the caller is shown is decided by their token, not by this call.
   */
  report(from: string, to: string): Observable<Report> {
    return this.http.get<Report>(`${GW}/v1beta/reporting`, { params: { from, to } });
  }

  /**
   * The same report, narrowed to one use case (FRD-603).
   *
   * The **same endpoint** on purpose. What a caller may see is one function on the gateway, and
   * an endpoint of its own would be a second place to decide it — which is how an export comes to
   * return more than the screen (`FRD-602` §1). The parameter can only ever intersect with what
   * the token already allows, so this call cannot ask for somebody else's figures.
   */
  useCaseReport(slug: string, from: string, to: string): Observable<Report> {
    return this.http.get<Report>(`${GW}/v1beta/reporting`, {
      params: { from, to, use_case: slug },
    });
  }

  /**
   * The register of processing activities (`FRD-608`).
   *
   * One row per use case: purpose, processing, released models and where they live, whether
   * prompts are kept and for how long, the controls, who is a member — and where the traffic
   * actually went. Scoped by the caller's token, by the very same function the report uses.
   */
  register(from: string, to: string): Observable<Register> {
    return this.http.get<Register>(`${GW}/v1beta/register`, { params: { from, to } });
  }

  /**
   * The same register as a spreadsheet (`FRD-608` §2.2).
   *
   * A blob for the reason `reportCsv` gives: the endpoint needs the bearer token, and an
   * `<a href>` carries no Authorization header.
   */
  registerCsv(from: string, to: string): Observable<Blob> {
    return this.http.get(`${GW}/v1beta/register`, {
      params: { from, to },
      headers: { Accept: 'text/csv' },
      responseType: 'blob',
    });
  }

  /**
   * The same report as a spreadsheet (FRD-602).
   *
   * A blob rather than a plain link, because the endpoint needs the bearer token and an `<a href>`
   * carries no Authorization header — a download link that 401s is worse than no link, since it
   * looks like the export is broken rather than like the browser cannot authenticate.
   */
  reportCsv(from: string, to: string, breakdown: string): Observable<Blob> {
    return this.http.get(`${GW}/v1beta/reporting`, {
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
    return this.http.get<AnomalyPage>(`${GW}/v1beta/anomalies`, { params });
  }

  /**
   * The prompt and the answer for one request — and, on the server, a record that it was read.
   *
   * Deliberately a second call rather than a field on the row: content is not metadata, the two
   * have different audiences, and a list that carried payloads would disclose to everybody who
   * may see the list.
   */
  tracePayload(id: string): Observable<TracePayload> {
    return this.http.get<TracePayload>(`${GW}/v1beta/traces/${seg(id)}/payload`);
  }

  /**
   * Is this model actually reachable, or only written down (`FRD-506`)?
   *
   * Never a generation: a self-deployed model can be scaled to zero, and "check whether it works"
   * must not be the thing that wakes it, bills for it, and takes minutes to answer.
   */
  checkModel(model: string, where: Provenance = {}): Observable<ModelCheck> {
    return this.http.get<ModelCheck>(`${GW}/v1beta/models/${seg(model)}:check`, {
      params: provenanceParams(where),
    });
  }

  /**
   * Does this model accept these level words (`ADR-0021`)?
   *
   * **This one does generate**, unlike its neighbour above, and the difference is deliberate.
   * There is no free way to ask: `:countTokens` never looks at `generationConfig` and answers 200
   * to an unsupported level as readily as to a supported one — measured. A `generateContent`
   * capped at one output token does judge, and a word the model refuses costs nothing at all
   * because the refusal comes before any generation.
   */
  checkThinkingLevels(
    model: string,
    levels: string[],
    where: Provenance = {},
    modes: string[] = [],
  ): Observable<ThinkingLevelCheck> {
    return this.http.post<ThinkingLevelCheck>(
      `${GW}/v1beta/models/${seg(model)}:checkThinking`,
      // The modes travel with the words because they are the same question asked twice — *can
      // this model be told this?* — and answering them costs nothing: the dialect either has the
      // field or it does not, so no request leaves the gateway for them.
      { levels, modes },
      { params: provenanceParams(where) },
    );
  }

  // ---- model smoke tests (`FRD-504`) --------------------------------------------------------

  /** The whole catalogue. A hundred rows, so it is fetched once and read in the browser. */
  testCases(): Observable<TestCase[]> {
    return this.http.get<TestCase[]>(`${API}/v1/test-cases/`);
  }

  testRuns(useCase?: string): Observable<TestRun[]> {
    const params: Record<string, string> = {};
    if (useCase) params['use_case'] = useCase;
    return this.http.get<TestRun[]>(`${API}/v1/test-runs/`, { params });
  }

  /**
   * Start a run against a use case's pipeline (`ADR-0020`).
   *
   * **No model.** A run enters where the pipeline says it enters, and asking the caller for one
   * would be asking them to predict a decision the pipeline makes — the server fills it in from
   * the model this run is entered at and records it on the run.
   */
  startRun(useCase: string, model: string): Observable<TestRun> {
    // The model the run is **entered at**, picked by the person starting it and bounded by the
    // server to what is released to that use case. It used to come from the pipeline, which took
    // away the point of releasing several models to one use case.
    return this.http.post<TestRun>(`${API}/v1/test-runs/`, { use_case: useCase, model });
  }

  runResults(runId: number): Observable<TestResult[]> {
    return this.http.get<TestResult[]>(`${API}/v1/test-runs/${runId}/results/`);
  }

  finishRun(runId: number): Observable<TestRun> {
    return this.http.post<TestRun>(`${API}/v1/test-runs/${runId}/finish/`, {});
  }

  /**
   * Store an answer, or a verdict, or both.
   *
   * Two different acts through one endpoint, and the server keeps them apart: writing a response
   * does not stamp a rater, because nobody has read it yet.
   */
  updateResult(id: number, changes: Partial<TestResult>): Observable<TestResult> {
    return this.http.patch<TestResult>(`${API}/v1/test-results/${id}/`, changes);
  }

  /**
   * Put one prompt to one model through the **gateway**, the ordinary way.
   *
   * `FRD-504` §5: a smoke test must travel the request path everybody else travels, or it measures
   * a path nobody uses. It is priced, budgeted, rate-limited and audited like any other request —
   * which also means an installation can see what its own testing costs.
   */
  askModel(model: string, prompt: string, useCase: string): Observable<string> {
    const path = useCase ? `${GW}/uc/${seg(useCase)}` : `${GW}`;
    return this.http
      .post<{ candidates?: { content?: { parts?: { text?: string }[] } }[] }>(
        `${path}/v1beta/models/${seg(model)}:generateContent`,
        { contents: [{ parts: [{ text: prompt }] }] },
      )
      .pipe(
        map((body) =>
          (body.candidates?.[0]?.content?.parts ?? []).map((part) => part.text ?? '').join(''),
        ),
      );
  }

  /** The evaluation as CSV. A blob, because a plain link carries no bearer token (`FRD-602`). */
  testRunCsv(runId: number): Observable<Blob> {
    return this.http.get(`${API}/v1/test-runs/${runId}/export/`, { responseType: 'blob' });
  }

  /** Authoring the catalogue. IT Security only on the server; the console offers it to nobody else. */
  createCase(body: Partial<TestCase>): Observable<TestCase> {
    return this.http.post<TestCase>(`${API}/v1/test-cases/`, body);
  }

  updateCase(id: number, body: Partial<TestCase>): Observable<TestCase> {
    return this.http.patch<TestCase>(`${API}/v1/test-cases/${id}/`, body);
  }

  deleteCase(id: number): Observable<void> {
    return this.http.delete<void>(`${API}/v1/test-cases/${id}/`);
  }

  testStats(): Observable<TestModelStats[]> {
    return this.http.get<TestModelStats[]>(`${API}/v1/test-stats/`);
  }

  /** Traffic that is currently stopped, and what was stopped before (`FRD-503`). */
  suspensions(): Observable<{ suspensions: Suspension[] }> {
    return this.http.get<{ suspensions: Suspension[] }>(`${GW}/v1beta/suspensions`);
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
    return this.http.post<Suspension>(`${GW}/v1beta/suspensions`, body);
  }

  /** Lift one. The row is kept and stamped, never deleted. */
  liftSuspension(id: string): Observable<Suspension> {
    return this.http.delete<Suspension>(`${GW}/v1beta/suspensions/${seg(id)}`);
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
    /** Only the requests a pipeline step objected to (`FRD-505` FR-5). */
    flaggedOnly?: boolean;
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
    if (options.flaggedOnly) params['flagged_only'] = true;
    if (options.cursor) params['cursor'] = options.cursor;
    return this.http.get<TracePage>(`${GW}/v1beta/traces`, { params });
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
    return this.http.get<AnomalyRule[]>(`${API}/v1/anomaly-rules/`);
  }

  /**
   * Change a rule. The server decides who may: a global rule needs an incident role, a use-case
   * rule needs to manage that use case (`AnomalyRuleViewSet._guard`).
   *
   * `PATCH`, not `PUT`: a rule has thirteen fields and most edits touch one of them. Sending the
   * whole object back would make every save a chance to overwrite a field the form never showed.
   */
  /**
   * Author a rule that applies to **every** use case (`FRD-500`).
   *
   * The server has accepted this since `FRD-500` and the console never offered it, so the only
   * global rules that existed were the ones a seed had written — `FRD-206`'s defect inverted: not
   * a control that refuses when used, but a capability nobody could reach.
   */
  createGlobalRule(rule: Partial<AnomalyRule>): Observable<AnomalyRule> {
    return this.http.post<AnomalyRule>(`${API}/v1/anomaly-rules/`, rule);
  }

  updateRule(id: number, changes: Partial<AnomalyRule>): Observable<AnomalyRule> {
    return this.http.patch<AnomalyRule>(`${API}/v1/anomaly-rules/${id}/`, changes);
  }

  deleteRule(id: number): Observable<void> {
    return this.http.delete<void>(`${API}/v1/anomaly-rules/${id}/`);
  }

  /** Current-period consumption per budget, from the gateway. */
  budgetUsage(slug: string): Observable<{ usage: BudgetUsage[] }> {
    return this.http.get<{ usage: BudgetUsage[] }>(`${GW}/v1beta/usage/${seg(slug)}`);
  }
}
