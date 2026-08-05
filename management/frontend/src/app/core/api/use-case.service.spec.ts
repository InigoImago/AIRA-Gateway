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
      .dryRunPipeline({ system: '', user: 'hi', pipeline: { steps: [], fallback_models: [] } })
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
      steps: [{ type: 'allow_check' as const, config: {} }],
      fallback_models: ['b'],
    };
    service.savePipeline('uc', config).subscribe();
    const req = http.expectOne('/api/v1/use-cases/uc/pipeline/');
    expect(req.request.method).toBe('PUT');
    expect(req.request.body).toEqual(config);
    req.flush(config);
  });

  it('dry-runs a pipeline against the gateway', () => {
    const payload = {
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
});
