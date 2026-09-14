import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { of, throwError } from 'rxjs';
import { MeService } from '../../core/api/me.service';
import { Me } from '../../core/api/models';
import { landingFor, platformLanding } from './platform-landing';
import { PlatformPage } from './platform-page';

const ME: Me = { subject: 's', username: 'u', email: '', roles: [], use_cases: [] };

function meService(permissions: string[] | null) {
  return {
    provide: MeService,
    useValue: {
      currency: signal(''),
      get: () =>
        permissions === null
          ? throwError(() => ({ status: 500, error: { error: { message: 'Account down.' } } }))
          : of({ ...ME, permissions }),
    },
  };
}

function render(permissions: string[] | null): HTMLElement {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({
    imports: [PlatformPage],
    providers: [provideRouter([]), meService(permissions)],
  });
  const fixture = TestBed.createComponent(PlatformPage);
  fixture.detectChanges();
  return fixture.nativeElement as HTMLElement;
}

/** Which menu entries a rendered page offers. */
function offered(html: HTMLElement): string[] {
  return ['platform-nav-content-reads', 'platform-nav-roles'].filter(
    (id) => html.querySelector(`[data-testid="${id}"]`) !== null,
  );
}

@Component({ template: 'content reads' })
class ContentReadsStub {}

@Component({ template: 'roles' })
class RolesStub {}

/** Where `/platform` lands for this caller, through the real router and the real redirect. */
async function landing(permissions: string[] | null): Promise<string> {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({
    providers: [
      provideRouter([
        {
          path: 'platform',
          component: PlatformPage,
          children: [
            { path: '', pathMatch: 'full', redirectTo: platformLanding },
            { path: 'content-reads', component: ContentReadsStub },
            { path: 'roles', component: RolesStub },
          ],
        },
      ]),
      meService(permissions),
    ],
  });
  const harness = await RouterTestingHarness.create();
  await harness.navigateByUrl('/platform');
  return TestBed.inject(Router).url;
}

describe('PlatformPage (`FRD-622` FR-5, `FRD-614`)', () => {
  it('offers its pages in a menu beside the page itself', () => {
    const html = render(['content_read.read', 'role.read']);

    const menu = html.querySelector('nav[aria-label="Platform administration"]');
    const reads = menu?.querySelector('[data-testid="platform-nav-content-reads"]');
    const roles = menu?.querySelector('[data-testid="platform-nav-roles"]');
    expect(reads?.textContent).toContain('Content reads');
    expect(reads?.getAttribute('href')).toContain('content-reads');
    expect(roles?.textContent).toContain('Roles');
    expect(roles?.getAttribute('href')).toContain('roles');
    expect(html.querySelector('.platform__page router-outlet')).not.toBeNull();
  });

  it('gives each entry its own permission', () => {
    // One permission, one entry: an entry that followed its neighbour's would pass every case
    // that grants both.
    expect(offered(render(['content_read.read']))).toEqual(['platform-nav-content-reads']);
    expect(offered(render(['role.read']))).toEqual(['platform-nav-roles']);
    expect(offered(render(['content_read.read', 'role.read']))).toEqual([
      'platform-nav-content-reads',
      'platform-nav-roles',
    ]);
    expect(offered(render([]))).toEqual([]);
  });

  it('says why the menu is empty when the account cannot be loaded', () => {
    const html = render(null);

    expect(offered(html)).toEqual([]);
    expect(html.querySelector('[data-testid="platform-menu-error"]')?.textContent).toContain(
      'Account down.',
    );
    expect(render(['role.read']).querySelector('[data-testid="platform-menu-error"]')).toBeNull();
  });

  it('lands on the first page the caller may open', async () => {
    expect(await landing(['content_read.read', 'role.read'])).toBe('/platform/content-reads');
    expect(await landing(['content_read.read'])).toBe('/platform/content-reads');
    // May read the roles and not the content-read log: not a landing on a refusal.
    expect(await landing(['role.read'])).toBe('/platform/roles');
    // Neither, or an unknown account: the first page, which reports the server's answer.
    expect(await landing([])).toBe('/platform/content-reads');
    expect(await landing(null)).toBe('/platform/content-reads');
  });

  it('decides the landing from the permissions and nothing else', () => {
    expect(landingFor(null)).toBe('content-reads');
    expect(landingFor({ ...ME, roles: ['it-steuerung'], permissions: ['role.read'] })).toBe(
      'roles',
    );
    expect(landingFor({ ...ME, roles: ['global-admin'], permissions: [] })).toBe('content-reads');
  });
});
