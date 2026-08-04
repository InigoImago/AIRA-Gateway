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
});
