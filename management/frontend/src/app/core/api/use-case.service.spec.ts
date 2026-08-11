import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { UseCaseService } from './use-case.service';

describe('UseCaseService', () => {
  let service: UseCaseService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting(), UseCaseService],
    });
    service = TestBed.inject(UseCaseService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('lists use cases', () => {
    service.list().subscribe((list) => expect(list.length).toBe(1));
    http
      .expectOne('/api/v1/use-cases/')
      .flush([{ slug: 'a', name: 'A', description: '', processing_notes: '' }]);
  });

  it('creates a use case with POST', () => {
    service.create({ slug: 'x', name: 'X' }).subscribe();
    const req = http.expectOne('/api/v1/use-cases/');
    expect(req.request.method).toBe('POST');
    req.flush({ slug: 'x', name: 'X', description: '', processing_notes: '' });
  });

  it('adds a member to a use case', () => {
    service.addMember('uc', 'bob', 'user').subscribe();
    const req = http.expectOne('/api/v1/use-cases/uc/members/');
    expect(req.request.body).toEqual({ username: 'bob', role: 'user' });
    req.flush({ username: 'bob', role: 'user' });
  });

  it('removes a member', () => {
    service.removeMember('uc', 'bob').subscribe();
    const req = http.expectOne('/api/v1/use-cases/uc/members/bob/');
    expect(req.request.method).toBe('DELETE');
    req.flush(null);
  });

  it('encodes user-supplied path segments', () => {
    // A username or slug must never be able to retarget the request at another endpoint.
    service.removeMember('uc', '../../admin').subscribe();
    const req = http.expectOne('/api/v1/use-cases/uc/members/..%2F..%2Fadmin/');
    expect(req.request.method).toBe('DELETE');
    req.flush(null);
  });

  it('encodes the slug in gateway usage calls', () => {
    service.budgetUsage('a/b').subscribe();
    http.expectOne('/gw/v1beta/usage/a%2Fb').flush({ usage: [] });
  });

  it('fetches a single use case', () => {
    service.get('uc').subscribe();
    http.expectOne('/api/v1/use-cases/uc/').flush({ slug: 'uc', name: 'UC' });
  });

  it('updates a use case with PATCH', () => {
    service.update('uc', { name: 'Renamed' }).subscribe();
    const req = http.expectOne('/api/v1/use-cases/uc/');
    expect(req.request.method).toBe('PATCH');
    expect(req.request.body).toEqual({ name: 'Renamed' });
    req.flush({ slug: 'uc', name: 'Renamed' });
  });

  it('deletes a use case', () => {
    service.remove('uc').subscribe();
    const req = http.expectOne('/api/v1/use-cases/uc/');
    expect(req.request.method).toBe('DELETE');
    req.flush(null);
  });

  it('lists members', () => {
    service.members('uc').subscribe((members) => expect(members.length).toBe(1));
    http.expectOne('/api/v1/use-cases/uc/members/').flush([{ username: 'bob', role: 'user' }]);
  });

  it('dry-runs a pipeline against the gateway', () => {
    service
      .dryRunPipeline({
        use_case: 'uc',
        system: '',
        user: 'hi',
        pipeline: { steps: [], fallback_models: [] },
      })
      .subscribe((result) => expect(result.blocked).toBe(false));
    const req = http.expectOne('/gw/v1beta/pipeline:dryRun');
    expect(req.request.method).toBe('POST');
    req.flush({
      blocked: false,
      block_reason: null,
      effective_model: 'mock-1',
      fallback_models: [],
      trace: [],
    });
  });

  it('reads the model catalog', () => {
    service.models().subscribe((models) => expect(models.length).toBe(1));
    http.expectOne('/api/v1/models/').flush([{ name: 'm-1' }]);
  });

  it('saves a model with its prices as strings', () => {
    service.saveModel({ name: 'm-1', input_price_per_million: '0.075' }).subscribe();
    const req = http.expectOne('/api/v1/models/');
    expect(req.request.method).toBe('POST');
    expect(req.request.body.input_price_per_million).toBe('0.075');
    req.flush({ name: 'm-1' });
  });

  it('asks the gateway which providers it is configured with', () => {
    /** The gateway, not Management: which upstreams exist is a property of the gateway's
     *  configuration and nothing else knows it. A hard-coded list here would be what the *product*
     *  supports rather than what *this installation* has. */
    service.providers().subscribe((providers) => expect(providers.length).toBe(1));
    const req = http.expectOne('/gw/v1beta/providers');
    expect(req.request.method).toBe('GET');
    req.flush({ providers: [{ name: 'generative-language' }] });
  });

  it('reads an empty provider list as empty rather than as undefined', () => {
    service.providers().subscribe((providers) => expect(providers).toEqual([]));
    http.expectOne('/gw/v1beta/providers').flush({});
  });

  it('encodes the provider name when asking what it offers', () => {
    /** A provider name comes from configuration somebody else wrote; an unencoded `/` would
     *  silently retarget the request at a different endpoint (`ADR-0007`). */
    service.providerOfferings('vendor/x').subscribe((models) => expect(models.length).toBe(1));
    const req = http.expectOne('/gw/v1beta/providers/vendor%2Fx/offerings');
    expect(req.request.method).toBe('GET');
    req.flush({ models: [{ name: 'gemini-flash-latest' }] });
  });

  it('encodes the model name when removing it', () => {
    service.removeModel('vendor/model:1').subscribe();
    const req = http.expectOne('/api/v1/models/vendor%2Fmodel%3A1/');
    expect(req.request.method).toBe('DELETE');
    req.flush(null);
  });

  it('lists api keys', () => {
    service.apiKeys('uc').subscribe((keys) => expect(keys.length).toBe(1));
    http
      .expectOne('/api/v1/use-cases/uc/api-keys/')
      .flush([{ prefix: 'ab12', label: 'k', owner: 'bob', is_active: true }]);
  });

  it('issues an api key with a label', () => {
    service
      .issueApiKey('uc', 'laptop')
      .subscribe((issued) => expect(issued.api_key).toContain('aira_'));
    const req = http.expectOne('/api/v1/use-cases/uc/api-keys/');
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({ label: 'laptop' });
    req.flush({ api_key: 'aira_ab12_secret', prefix: 'ab12', label: 'laptop', use_case: 'uc' });
  });

  it('revokes an api key by prefix', () => {
    service.revokeApiKey('uc', 'ab12').subscribe();
    const req = http.expectOne('/api/v1/use-cases/uc/api-keys/ab12/');
    expect(req.request.method).toBe('DELETE');
    req.flush(null);
  });

  it('gets the pipeline config', () => {
    service.getPipeline('uc').subscribe((c) => expect(c.steps.length).toBe(0));
    http.expectOne('/api/v1/use-cases/uc/pipeline/').flush({ steps: [], fallback_models: [] });
  });

  it('saves the pipeline with PUT', () => {
    const config = {
      steps: [{ type: 'model_route' as const, config: {} }],
      fallback_models: ['b'],
    };
    service.savePipeline('uc', config).subscribe();
    const req = http.expectOne('/api/v1/use-cases/uc/pipeline/');
    expect(req.request.method).toBe('PUT');
    expect(req.request.body).toEqual(config);
    req.flush(config);
  });

  it('dry-runs a pipeline against the gateway', () => {
    // The use case travels with it: a dry run calls a real model, and the gateway refuses one
    // this use case may not call (`FRD-308`).
    const payload = {
      use_case: 'uc',
      system: 'sys',
      user: 'hi',
      pipeline: { steps: [], fallback_models: [] },
    };
    service.dryRunPipeline(payload).subscribe((r) => expect(r.blocked).toBe(false));
    const req = http.expectOne('/gw/v1beta/pipeline:dryRun');
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual(payload);
    req.flush({
      blocked: false,
      block_reason: null,
      effective_model: 'mock-1',
      fallback_models: [],
      trace: [],
    });
  });

  it('creates a budget with POST', () => {
    const budget = { scope: 'use_case' as const, period: 'month' as const, limit_tokens: 1000 };
    service.createBudget('uc', budget).subscribe();
    const req = http.expectOne('/api/v1/use-cases/uc/budgets/');
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual(budget);
    req.flush({ id: 1, ...budget });
  });

  it('deletes a budget by id', () => {
    service.deleteBudget('uc', 7).subscribe();
    const req = http.expectOne('/api/v1/use-cases/uc/budgets/7/');
    expect(req.request.method).toBe('DELETE');
    req.flush(null);
  });

  it('lists rate limits', () => {
    service.rateLimits('uc').subscribe((limits) => expect(limits.length).toBe(1));
    const req = http.expectOne('/api/v1/use-cases/uc/rate-limits/');
    expect(req.request.method).toBe('GET');
    req.flush([{ id: 1, scope: 'use_case', limit_rpm: 60, burst: 10 }]);
  });

  it('creates a rate limit with POST', () => {
    const limit = { scope: 'use_case' as const, limit_rpm: 60, burst: 10 };
    service.createRateLimit('uc', limit).subscribe();
    const req = http.expectOne('/api/v1/use-cases/uc/rate-limits/');
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual(limit);
    req.flush({ id: 1, ...limit });
  });

  it('deletes a rate limit by id', () => {
    service.deleteRateLimit('uc', 4).subscribe();
    const req = http.expectOne('/api/v1/use-cases/uc/rate-limits/4/');
    expect(req.request.method).toBe('DELETE');
    req.flush(null);
  });

  it('encodes a slug into the rate-limit URL', () => {
    service.rateLimits('a/b').subscribe();
    http.expectOne('/api/v1/use-cases/a%2Fb/rate-limits/').flush([]);
  });

  it('reads budget usage from the gateway', () => {
    service.budgetUsage('uc').subscribe((r) => expect(r.usage.length).toBe(1));
    http
      .expectOne('/gw/v1beta/usage/uc')
      .flush({ usage: [{ id: 1, used_tokens: 5, used_requests: 2 }] });
  });
  // ---- FRD-502/503: findings, suspensions, traces -----------------------------------------------

  it('reads findings from the gateway, which is where the request log lives', () => {
    service.anomalies(25).subscribe((page) => expect(page.events.length).toBe(0));
    const req = http.expectOne((r) => r.url === '/gw/v1beta/anomalies');
    expect(req.request.params.get('limit')).toBe('25');
    req.flush({ events: [], scope: 'all' });
  });

  it('posts a suspension and deletes it by id', () => {
    service.suspend({ target: 'subject', target_value: 'ada', reason: 'probing' }).subscribe();
    const post = http.expectOne('/gw/v1beta/suspensions');
    expect(post.request.method).toBe('POST');
    expect(post.request.body).toEqual({
      target: 'subject',
      target_value: 'ada',
      reason: 'probing',
    });
    post.flush({ id: 's1' });

    service.liftSuspension('s1').subscribe();
    const del = http.expectOne('/gw/v1beta/suspensions/s1');
    expect(del.request.method).toBe('DELETE');
    del.flush({ id: 's1', lifted_at: 'now' });
  });

  it('encodes a suspension id rather than pasting it into a path', () => {
    // Ids come back from the server, but a path built by concatenation is a path that breaks the
    // first time one contains a slash — and this one restores access.
    service.liftSuspension('a/b').subscribe();
    http.expectOne('/gw/v1beta/suspensions/a%2Fb').flush({});
  });

  it('sends only the trace filters that were actually set', () => {
    // An empty filter sent as an empty string is not the same request: the server would read it as
    // "outcome equals nothing" and answer with nothing.
    service.traces({ useCase: 'uc-a' }).subscribe();
    const bare = http.expectOne((r) => r.url === '/gw/v1beta/traces');
    expect(bare.request.params.get('use_case')).toBe('uc-a');
    expect(bare.request.params.has('outcome')).toBe(false);
    expect(bare.request.params.has('refusals_only')).toBe(false);
    expect(bare.request.params.has('cursor')).toBe(false);
    expect(bare.request.params.get('limit')).toBe('50');
    bare.flush({ traces: [], next_cursor: null, scope: 'use_cases' });

    service
      .traces({
        useCase: 'uc-a',
        outcome: 'rate_limited',
        refusalsOnly: true,
        cursor: 'c1',
        limit: 5,
      })
      .subscribe();
    const full = http.expectOne((r) => r.url === '/gw/v1beta/traces');
    expect(full.request.params.get('outcome')).toBe('rate_limited');
    expect(full.request.params.get('refusals_only')).toBe('true');
    expect(full.request.params.get('cursor')).toBe('c1');
    expect(full.request.params.get('limit')).toBe('5');
    full.flush({ traces: [], next_cursor: null, scope: 'use_cases' });
  });

  it('sends the filters an incident starts with', () => {
    /** Which system, whose identity, which machine — plus "only mine" and "only tool turns"
     *  (`FRD-131` FR-7). Each is asked for **by name**, so the server bounds the result; a browser
     *  filter over one page would answer a busy installation with whatever happened to load. */
    service
      .traces({
        credential: 'ab',
        subject: 'alice',
        sourceIp: '10.0.0.7',
        mine: true,
        toolsOnly: true,
      })
      .subscribe();

    const request = http.expectOne((r) => r.url === '/gw/v1beta/traces').request;
    expect(request.params.get('credential')).toBe('ab');
    expect(request.params.get('subject')).toBe('alice');
    expect(request.params.get('source_ip')).toBe('10.0.0.7');
    expect(request.params.get('mine')).toBe('true');
    expect(request.params.get('tools_only')).toBe('true');
  });

  it('omits a filter that is off rather than sending it as false', () => {
    /** `mine=false` and an absent `mine` are the same intent and must be the same request: a
     *  parameter present with a falsy value is one more thing the server has to agree with us
     *  about. */
    service.traces({ mine: false, toolsOnly: false, credential: '' }).subscribe();

    const request = http.expectOne((r) => r.url === '/gw/v1beta/traces').request;
    expect(request.params.has('mine')).toBe(false);
    expect(request.params.has('tools_only')).toBe(false);
    expect(request.params.has('credential')).toBe(false);
  });

  it('asks the server for a page of use cases, and only searches when there is a term', () => {
    service.listPage('', 2).subscribe();
    const bare = http.expectOne((r) => r.url === '/api/v1/use-cases/').request;
    expect(bare.params.get('page')).toBe('2');
    expect(bare.params.has('q')).toBe(false);

    service.listPage('vertrieb', 1).subscribe();
    expect(http.expectOne((r) => r.url === '/api/v1/use-cases/').request.params.get('q')).toBe(
      'vertrieb',
    );
  });

  it('leaves the expiry out when the caller named none', () => {
    /** NULL means never (`ADR-0015`), and an omitted field is how the server is told to apply its
     *  own default — sending `0` would be a request for a key that expires immediately. */
    service.issueApiKey('uc-a', 'laptop').subscribe();
    expect(http.expectOne((r) => r.method === 'POST').request.body).toEqual({ label: 'laptop' });

    service.issueApiKey('uc-a', 'laptop', 30).subscribe();
    expect(http.expectOne((r) => r.method === 'POST').request.body).toEqual({
      label: 'laptop',
      expires_in_days: 30,
    });
  });

  it('asks for findings by use case rather than sifting the newest hundred', () => {
    service.anomalies(10, 'uc-a', 'c1').subscribe();
    const request = http.expectOne((r) => r.url === '/gw/v1beta/anomalies').request;
    expect(request.params.get('use_case')).toBe('uc-a');
    expect(request.params.get('cursor')).toBe('c1');
    expect(request.params.get('limit')).toBe('10');
  });

  // ---- model smoke tests (`FRD-504`) ---------------------------------------------------------

  it('asks the model through the gateway, attributed to a use case', () => {
    /** `FRD-504` §5: a smoke test travels the ordinary request path, so it is priced, budgeted,
     *  rate-limited and audited like any other traffic. A harness that bypassed the gateway would
     *  measure a path nobody uses. */
    service.askModel('qwen2.5:3b', 'Say OK', 'uc-a').subscribe((answer) => {
      expect(answer).toBe('OK.');
    });

    const request = http.expectOne((r) => r.url.includes(':generateContent'));
    expect(request.request.url).toContain('/gw/uc/uc-a/');
    request.flush({ candidates: [{ content: { parts: [{ text: 'OK.' }] } }] });
  });

  it('omits the selector when the credential already carries a use case', () => {
    service.askModel('m', 'hi', '').subscribe();

    const request = http.expectOne((r) => r.url.includes(':generateContent'));
    expect(request.request.url).not.toContain('/uc/');
    // An answer with no parts is an empty string, not a crash: a model that returns nothing is
    // exactly the kind of behaviour a battery exists to record.
    request.flush({ candidates: [{}] });
  });

  it('asks for the runs of one model when it is given one', () => {
    service.testRuns('m-1').subscribe();
    expect(http.expectOne((r) => r.url === '/api/v1/test-runs/').request.params.get('model')).toBe(
      'm-1',
    );

    service.testRuns().subscribe();
    expect(http.expectOne((r) => r.url === '/api/v1/test-runs/').request.params.has('model')).toBe(
      false,
    );
  });

  it('asks the server for the tool-call turns and the flagged ones separately', () => {
    service.traces({ flaggedOnly: true }).subscribe();

    expect(
      http.expectOne((r) => r.url === '/gw/v1beta/traces').request.params.get('flagged_only'),
    ).toBe('true');
  });

  it('reads anomaly rules from management, not from the gateway', () => {
    // The rule is authored in the control plane; the finding is produced in the data plane. Asking
    // the wrong plane would work in a demo and return a stale copy in production.
    service.globalRules().subscribe((rules) => expect(rules).toEqual([]));
    http.expectOne('/api/v1/anomaly-rules/').flush([]);
  });
});
