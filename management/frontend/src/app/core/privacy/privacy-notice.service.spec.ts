import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { PrivacyNoticeService } from './privacy-notice.service';

describe('PrivacyNoticeService', () => {
  function setup() {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    return {
      service: TestBed.inject(PrivacyNoticeService),
      http: TestBed.inject(HttpTestingController),
    };
  }

  it('lets the browser choose the language unless the reader did', () => {
    const { service, http } = setup();

    service.get().subscribe();
    const negotiated = http.expectOne('/api/v1/privacy-notice');
    expect(negotiated.request.params.has('language')).toBe(false);
    negotiated.flush({});

    service.get('en').subscribe();
    http.expectOne('/api/v1/privacy-notice?language=en').flush({});
    http.verify();
  });

  it('acknowledges a version in a language', () => {
    const { service, http } = setup();
    service.acknowledge('v1', 'de').subscribe();

    const request = http.expectOne('/api/v1/privacy-notice/acknowledgements');
    expect(request.request.method).toBe('POST');
    expect(request.request.body).toEqual({ version: 'v1', language: 'de' });
    request.flush({});
    http.verify();
  });
});
