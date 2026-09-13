import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { PlatformPage } from './platform-page';

describe('PlatformPage (`FRD-622` FR-5)', () => {
  it('offers its pages in a menu beside the page itself', () => {
    TestBed.configureTestingModule({ imports: [PlatformPage], providers: [provideRouter([])] });
    const fixture = TestBed.createComponent(PlatformPage);
    fixture.detectChanges();
    const html = fixture.nativeElement as HTMLElement;

    const menu = html.querySelector('nav[aria-label="Platform administration"]');
    const entry = menu?.querySelector('[data-testid="platform-nav-content-reads"]');
    expect(entry?.textContent).toContain('Content reads');
    expect(entry?.getAttribute('href')).toContain('content-reads');
    expect(html.querySelector('.platform__page router-outlet')).not.toBeNull();
  });
});
