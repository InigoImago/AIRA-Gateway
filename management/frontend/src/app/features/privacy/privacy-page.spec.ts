import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { privacyNotice } from '../../core/privacy/privacy-notice.fixture';
import { PrivacyPage } from './privacy-page';

const NOTICE = '/api/v1/privacy-notice';

function setup() {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({
    imports: [PrivacyPage],
    providers: [provideHttpClient(), provideHttpClientTesting()],
  });
  const fixture = TestBed.createComponent(PrivacyPage);
  fixture.detectChanges();
  const root = () => fixture.nativeElement as HTMLElement;
  return {
    http: TestBed.inject(HttpTestingController),
    render: () => fixture.detectChanges(),
    q: <T extends Element>(selector: string) => root().querySelector<T>(selector),
    text: () => root().textContent ?? '',
  };
}

describe('PrivacyPage', () => {
  it('shows the notice whether or not it is due, with its edition and the last acknowledgement', () => {
    const h = setup();
    expect(h.q('[data-testid="privacy-loading"]')).not.toBeNull();

    h.http
      .expectOne(NOTICE)
      .flush(privacyNotice({ due: null, acknowledged_at: '2026-09-02T08:00:00Z' }));
    h.render();

    expect(h.q('h2')!.textContent).toContain('Datenschutzhinweise');
    expect(h.q('[data-testid="privacy-edition"]')!.textContent).toContain('Stand');
    expect(h.q('[data-testid="privacy-acknowledged"]')!.textContent).toContain('2026');
    expect(h.text()).toContain('Protokoll jeder Anfrage');
    // A page, not a window: nothing here to acknowledge.
    expect(h.q('[data-testid="privacy-acknowledge"]')).toBeNull();
  });

  it('reads the notice in another language on request', () => {
    const h = setup();
    h.http.expectOne(NOTICE).flush(privacyNotice());
    h.render();

    h.q<HTMLButtonElement>('[data-testid="privacy-language-en"]')!.click();
    h.http
      .expectOne(`${NOTICE}?language=en`)
      .flush(privacyNotice({ language: 'en', acknowledged_at: null }));
    h.render();

    expect(h.q('h2')!.textContent).toContain('Privacy notice');
    expect(h.q('[data-testid="privacy-acknowledged"]')).toBeNull();
  });

  it('says so when the notice cannot be loaded', () => {
    const h = setup();
    h.http
      .expectOne(NOTICE)
      .flush(
        { error: { code: 'x', message: 'Management is down.' } },
        { status: 503, statusText: 'x' },
      );
    h.render();

    expect(h.q('[data-testid="privacy-page-error"]')!.textContent).toContain('Management is down.');
  });
});
