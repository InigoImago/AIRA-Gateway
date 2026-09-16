import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { AuthService } from '../auth/auth.service';
import { PrivacyGate } from './privacy-gate';
import { privacyNotice } from './privacy-notice.fixture';

const NOTICE = '/api/v1/privacy-notice';
const ACK = '/api/v1/privacy-notice/acknowledgements';

function setup(authenticated = true) {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({
    imports: [PrivacyGate],
    providers: [
      provideHttpClient(),
      provideHttpClientTesting(),
      { provide: AuthService, useValue: { isAuthenticated: () => authenticated } },
    ],
  });
  const fixture = TestBed.createComponent(PrivacyGate);
  const http = TestBed.inject(HttpTestingController);
  fixture.detectChanges();
  const root = () => fixture.nativeElement as HTMLElement;
  return {
    fixture,
    http,
    q: <T extends Element>(selector: string) => root().querySelector<T>(selector),
    text: () => root().textContent ?? '',
    render: () => fixture.detectChanges(),
  };
}

describe('PrivacyGate', () => {
  afterEach(() => TestBed.inject(HttpTestingController).verify());

  it('opens the notice as a window when the server says it is due, and says why', () => {
    const h = setup();
    h.http.expectOne(NOTICE).flush(privacyNotice({ due: 'first' }));
    h.render();

    const dialog = h.q('[data-testid="privacy-notice"]');
    expect(dialog).not.toBeNull();
    expect(h.q('#privacy-notice-title')!.textContent).toContain('Datenschutzhinweise');
    expect(h.q('[data-testid="privacy-due"]')!.textContent).toContain('Bitte zuerst lesen.');
    expect(h.text()).toContain('Protokoll jeder Anfrage');
    expect(h.q('[data-activity="request_record"] dt')!.textContent).toContain('Welche Daten');
    // It must be answered: there is no ✕.
    expect(h.q('[data-testid="privacy-notice-close"]')).toBeNull();
  });

  it('opens nothing when the notice is not due', () => {
    const h = setup();
    h.http.expectOne(NOTICE).flush(privacyNotice({ due: null }));
    h.render();

    expect(h.q('[data-testid="privacy-notice"]')).toBeNull();
    expect(h.q('[data-testid="privacy-load-error"]')).toBeNull();
  });

  it('asks nothing of a console that is not signed in', () => {
    setup(false);
    TestBed.inject(HttpTestingController).expectNone(NOTICE);
  });

  it('closes once the reader acknowledged the version they were shown, in their language', () => {
    const h = setup();
    h.http.expectOne(NOTICE).flush(privacyNotice({ due: 'month', language: 'en' }));
    h.render();

    h.q<HTMLButtonElement>('[data-testid="privacy-acknowledge"]')!.click();
    const request = h.http.expectOne(ACK);
    expect(request.request.method).toBe('POST');
    expect(request.request.body).toEqual({ version: '2026-09-16.abc', language: 'en' });
    request.flush({ version: '2026-09-16.abc', language: 'en', first_at: 'x', last_at: 'y' });
    h.render();

    expect(h.q('[data-testid="privacy-notice"]')).toBeNull();
  });

  it('switches language inside the window and keeps it open', () => {
    const h = setup();
    h.http.expectOne(NOTICE).flush(privacyNotice({ due: 'first' }));
    h.render();
    expect(h.q('[data-testid="privacy-language-de"]')!.getAttribute('aria-pressed')).toBe('true');

    h.q<HTMLButtonElement>('[data-testid="privacy-language-en"]')!.click();
    h.http
      .expectOne((r) => r.url === NOTICE && r.params.get('language') === 'en')
      .flush(privacyNotice({ due: 'first', language: 'en' }));
    h.render();

    expect(h.q('[data-testid="privacy-notice"]')).not.toBeNull();
    expect(h.q('#privacy-notice-title')!.textContent).toContain('Privacy notice');
    expect(h.q('[data-testid="privacy-acknowledge"]')!.textContent).toContain('I have read');
    expect(h.q('[data-testid="privacy-language-en"]')!.getAttribute('aria-pressed')).toBe('true');
    expect(h.q('[data-testid="privacy-notice-body"]')!.getAttribute('lang')).toBe('en');
  });

  it('keeps the window open and says so when the acknowledgement fails', () => {
    const h = setup();
    h.http.expectOne(NOTICE).flush(privacyNotice());
    h.render();

    h.q<HTMLButtonElement>('[data-testid="privacy-acknowledge"]')!.click();
    h.http
      .expectOne(ACK)
      .flush({ error: { code: 'x', message: 'Database away.' } }, { status: 500, statusText: 'x' });
    h.render();

    expect(h.q('[data-testid="privacy-notice"]')).not.toBeNull();
    expect(h.q('[data-testid="privacy-acknowledge-error"]')!.textContent).toContain(
      'Database away.',
    );
  });

  it('shows the current notice when it changed while it was open, and keeps asking', () => {
    const h = setup();
    h.http.expectOne(NOTICE).flush(privacyNotice());
    h.render();

    h.q<HTMLButtonElement>('[data-testid="privacy-acknowledge"]')!.click();
    h.http
      .expectOne(ACK)
      .flush(
        { error: { code: 'conflict', message: 'The privacy notice changed while it was open.' } },
        { status: 409, statusText: 'Conflict' },
      );
    h.http
      .expectOne((r) => r.url === NOTICE && r.params.get('language') === 'de')
      .flush(privacyNotice({ version: '2026-10-01.def', due: 'changed' }));
    h.render();

    expect(h.q('[data-testid="privacy-notice"]')).not.toBeNull();
    expect(h.text()).toContain('changed while it was open');
    h.q<HTMLButtonElement>('[data-testid="privacy-acknowledge"]')!.click();
    expect(h.http.expectOne(ACK).request.body.version).toBe('2026-10-01.def');
  });

  it('says a notice that could not be loaded, without locking the console, and retries', () => {
    const h = setup();
    h.http.expectOne(NOTICE).flush(null, { status: 502, statusText: 'Bad Gateway' });
    h.render();

    expect(h.q('[data-testid="privacy-notice"]')).toBeNull();
    const alert = h.q('[data-testid="privacy-load-error"]')!;
    expect(alert.textContent).toContain('The privacy notice could not be loaded.');

    alert.querySelector<HTMLButtonElement>('button')!.click();
    h.http.expectOne(NOTICE).flush(privacyNotice({ due: null }));
    h.render();
    expect(h.q('[data-testid="privacy-load-error"]')).toBeNull();
  });

  it('reports a failed language switch inside the window rather than behind it', () => {
    const h = setup();
    h.http.expectOne(NOTICE).flush(privacyNotice());
    h.render();

    h.q<HTMLButtonElement>('[data-testid="privacy-language-en"]')!.click();
    h.http.expectOne(() => true).flush(null, { status: 503, statusText: 'x' });
    h.render();

    expect(h.q('[data-testid="privacy-acknowledge-error"]')!.textContent).toContain(
      'Nicht geladen.',
    );
    expect(h.q('[data-testid="privacy-load-error"]')).toBeNull();
  });

  it('renders the server’s text as text, never as markup', () => {
    const h = setup();
    h.http.expectOne(NOTICE).flush(privacyNotice());
    h.render();

    expect(h.q('[data-section="access"] b')).toBeNull();
    expect(h.q('[data-section="access"] li')!.textContent).toContain('<b>not markup</b>');
  });
});
