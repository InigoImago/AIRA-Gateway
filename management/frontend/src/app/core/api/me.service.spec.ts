import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { MeService } from './me.service';

describe('MeService', () => {
  it('reads the signed-in profile from the management API', () => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting(), MeService],
    });
    const service = TestBed.inject(MeService);
    const http = TestBed.inject(HttpTestingController);

    let roles: string[] = [];
    service.get().subscribe((me) => (roles = me.roles));
    http
      .expectOne('/api/v1/me')
      .flush({ subject: 's', username: 'demo', email: '', roles: ['global-admin'], use_cases: [] });

    expect(roles).toEqual(['global-admin']);
    http.verify();
  });
});
