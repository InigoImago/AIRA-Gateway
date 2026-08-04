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
});
