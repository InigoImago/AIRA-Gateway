import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of, throwError } from 'rxjs';
import { App } from './app';
import { MeService } from './core/api/me.service';
import { Me } from './core/api/models';
import { AuthService } from './core/auth/auth.service';
import { PrivacyNoticeService } from './core/privacy/privacy-notice.service';
import { privacyNotice } from './core/privacy/privacy-notice.fixture';

const baseMe: Me = {
  subject: 's',
  username: 'demo',
  email: 'demo@x',
  roles: [],
  use_cases: [],
  // The server's names for every role (`FRD-614` FR-10), an installation's own role included.
  role_labels: {
    'global-admin': 'Global Administrator',
    'it-security': 'IT Security',
    'it-steuerung': 'IT Steuerung',
    controlling: 'Controlling',
  },
};

let loggedOut = false;

/**
 * What `/me` lists for each built-in role, as `aira_common.permissions` defines them. Used once, to
 * show the navigation each built-in role sees did not change when the console stopped asking roles.
 */
const BUILTIN: Record<string, string[]> = {
  'global-admin': [
    'usecase.create',
    'usecase.read_all',
    'usecase.manage_all',
    'usecase.read_retired',
    'usecase.purge',
    'catalog.write',
    'budget.installation.write',
    'report.read_all',
    'trace.read_all',
    'anomaly.read_all',
    'anomaly.rule.global.write',
    'incident.suspend',
    'incident.investigate',
    'payload.read_any',
    'content_read.read',
    'operations.diagnose',
    'smoketest.author',
    'smoketest.run_any',
    'directory.search',
    'role.read',
    'role.manage',
  ],
  'it-security': [
    'usecase.read_all',
    'report.read_all',
    'trace.read_all',
    'anomaly.read_all',
    'anomaly.rule.global.write',
    'incident.suspend',
    'incident.investigate',
    'payload.read_any',
    'content_read.read',
    'operations.diagnose',
    'smoketest.author',
    'smoketest.run_any',
    'role.read',
  ],
  'it-steuerung': [
    'usecase.read_all',
    'usecase.read_retired',
    'report.read_all',
    'trace.read_all',
    'anomaly.read_all',
    'content_read.read',
    'role.read',
  ],
};

function configure(authenticated: boolean, roles: string[] = [], permissions: string[] = []): void {
  TestBed.resetTestingModule();
  loggedOut = false;
  TestBed.configureTestingModule({
    imports: [App],
    providers: [
      provideRouter([]),
      {
        provide: AuthService,
        useValue: {
          isAuthenticated: () => authenticated,
          // The shell reads this to decide whether to render the routes at all. A stub without
          // it is a stub of a different service — the trap `Live`'s teardown case recorded.
          startupError: signal<string | null>(null),
          loginLoop: signal<string | null>(null),
          logout: () => {
            loggedOut = true;
          },
        },
      },
      {
        provide: MeService,
        useValue: { currency: signal(''), get: () => of({ ...baseMe, roles, permissions }) },
      },
      // Not due, so the shell's own assertions see the shell. The gate has its own spec.
      { provide: PrivacyNoticeService, useValue: { get: () => of(privacyNotice({ due: null })) } },
    ],
  });
}

describe('App', () => {
  it('renders the title and use-case navigation', async () => {
    configure(true);
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('h1')?.textContent).toContain('AIRA Gateway');
    expect(el.textContent).toContain('Use cases');
    expect(el.querySelector('.aira-user')?.textContent).toContain('demo');
  });

  it('names the role in the header instead of showing tabs that do not respond', () => {
    // No disabled tabs for screens that do not exist — a tab that does not respond reads as broken
    // (`FRD-206`). Which role is asking is said in the header instead.
    configure(true, ['it-security', 'global-admin']);
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;

    // The tabs are gone from the navigation…
    expect(el.querySelector('.aira-nav__item.is-disabled')).toBeNull();
    // …and what they encoded — the console follows the roles in the token — is now a chip per
    // role in the header, carrying the slug so it stays assertable without depending on wording.
    expect(el.querySelector('.aira-user__role[data-role="it-security"]')).not.toBeNull();
    expect(el.querySelector('.aira-user__role[data-role="global-admin"]')).not.toBeNull();
    // A role the caller does **not** hold gets no chip. `it-steuerung` rather than the abolished
    // `use-case-user`: a slug nobody can hold would make this assertion true for ever.
    expect(el.querySelector('[data-role="it-steuerung"]')).toBeNull();
    expect(el.textContent).toContain('IT Security');
    expect(el.textContent).toContain('Global Administrator');
  });

  it('names every role the caller holds as the server does, an installation’s own included', () => {
    // `FRD-614` FR-10: the labels come from `/me`, so a role created in the console has a chip in
    // its own words, and a role the server names nowhere is shown by its slug rather than dropped.
    configure(true, ['global-admin', 'controlling', 'unnamed']);
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    const chip = (slug: string) => el.querySelector(`.aira-user__role[data-role="${slug}"]`);

    expect(chip('controlling')?.textContent?.trim()).toBe('Controlling');
    expect(chip('global-admin')?.textContent?.trim()).toBe('Global Administrator');
    expect(chip('global-admin')?.getAttribute('title')).toContain('price a model');
    expect(chip('unnamed')?.textContent?.trim()).toBe('unnamed');
    expect(chip('controlling')?.getAttribute('title')).toBe('');
  });
  it('signs the user out from the header', async () => {
    configure(true);
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    const button = (fixture.nativeElement as HTMLElement).querySelector<HTMLButtonElement>(
      '.aira-user button',
    );
    expect(button?.textContent).toContain('Logout');
    button?.click();
    expect(loggedOut).toBe(true);
  });

  it('does not load the profile when unauthenticated', async () => {
    configure(false);
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    await fixture.whenStable();
    expect((fixture.nativeElement as HTMLElement).querySelector('.aira-user')).toBeNull();
  });
  // ---- the navigation follows the permissions `/me` lists (`FRD-614`) -----------------------

  function render(permissions: string[], roles: string[] = []): HTMLElement {
    configure(true, roles, permissions);
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  /** Which of the permission-gated entries a rendered shell offers. */
  function offered(el: HTMLElement): string[] {
    return ['nav-requests', 'nav-register', 'nav-security', 'platform-admin'].filter(
      (id) => el.querySelector(`[data-testid="${id}"]`) !== null,
    );
  }

  it('offers cross-use-case requests to whoever may investigate an incident', () => {
    const el = render(['incident.investigate']);

    expect(el.querySelector('[data-testid="nav-requests"]')).not.toBeNull();
  });

  it('does not offer it to a reader who sees figures and not content', () => {
    /** Offering the screen and refusing on use is `FRD-206`'s defect; withholding the tab is the
     *  boundary stated plainly. */
    const el = render(['anomaly.read_all', 'trace.read_all', 'report.read_all']);

    expect(el.querySelector('[data-testid="nav-requests"]')).toBeNull();
    expect(el.querySelector('[data-testid="nav-security"]')).not.toBeNull();
  });

  it('does not offer it to somebody who only runs a use case', () => {
    const el = render([]);

    expect(el.querySelector('[data-testid="nav-requests"]')).toBeNull();
  });

  it('gives each entry its own permission', () => {
    // One permission, one entry: an entry that followed a neighbour's permission would still pass
    // every case that grants both.
    expect(offered(render(['report.read_all']))).toEqual(['nav-register']);
    expect(offered(render(['anomaly.read_all']))).toEqual(['nav-security']);
    expect(offered(render(['incident.investigate']))).toEqual(['nav-requests']);
    expect(offered(render(['content_read.read']))).toEqual(['platform-admin']);
    expect(offered(render(['role.read']))).toEqual(['platform-admin']);
  });

  it('shows each built-in role the navigation it had', () => {
    expect(offered(render(BUILTIN['global-admin'], ['global-admin']))).toEqual([
      'nav-requests',
      'nav-register',
      'nav-security',
      'platform-admin',
    ]);
    expect(offered(render(BUILTIN['it-security'], ['it-security']))).toEqual([
      'nav-requests',
      'nav-register',
      'nav-security',
      'platform-admin',
    ]);
    // Every figure and no content: no Requests.
    expect(offered(render(BUILTIN['it-steuerung'], ['it-steuerung']))).toEqual([
      'nav-register',
      'nav-security',
      'platform-admin',
    ]);
  });

  it('asks the permissions and never the roles', () => {
    // The server decides what a role holds. A role with nothing listed is offered nothing, and a
    // permission with no built-in role behind it is offered its entry.
    expect(offered(render([], ['global-admin', 'it-security', 'it-steuerung']))).toEqual([]);
    expect(offered(render(['incident.investigate'], []))).toEqual(['nav-requests']);
  });

  // ---- platform administration (`FRD-622` FR-5) --------------------------------------------------

  it('offers platform administration beside the name to whoever may read one of its pages', () => {
    for (const permission of ['content_read.read', 'role.read']) {
      const link = render([permission]).querySelector('.aira-user [data-testid="platform-admin"]');

      expect(link?.getAttribute('href'), permission).toBe('/platform');
    }
  });

  it('places platform administration at the far right, after Logout', () => {
    const el = render(BUILTIN['global-admin']);
    const controls = [...el.querySelectorAll('.aira-user a, .aira-user button')];
    const platform = el.querySelector('[data-testid="platform-admin"]');

    expect(controls.at(-1)).toBe(platform);
    expect(platform?.previousElementSibling?.classList.contains('aira-user__divider')).toBe(true);
    expect(platform?.getAttribute('aria-label')).toBe('Platform administration');
  });

  it('does not offer platform administration to somebody who only works in use cases', () => {
    expect(render([]).querySelector('[data-testid="platform-admin"]')).toBeNull();
  });
});

describe('App when the identity provider cannot be reached', () => {
  /**
   * An unreachable Keycloak must not leave a blank page: `AuthService.init()` runs in an app
   * initialiser, and a reader cannot tell "the login service is down" from "this application is
   * broken".
   *
   * Asserted on rendered text rather than on the signal: a flag nobody renders is the same blank
   * page with better bookkeeping.
   */
  function renderWithStartupError(issuer: string | null): HTMLElement {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      imports: [App],
      providers: [
        provideRouter([]),
        {
          provide: AuthService,
          useValue: {
            isAuthenticated: () => false,
            startupError: signal<string | null>(issuer),
            loginLoop: signal<string | null>(null),
            logout: () => {},
          },
        },
        { provide: MeService, useValue: { currency: signal(''), get: () => of(baseMe) } },
      ],
    });
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  it('says so, instead of rendering nothing', () => {
    const el = renderWithStartupError('http://localhost:8080/realms/aira');
    const panel = el.querySelector('[data-testid="startup-error"]');

    expect(panel).not.toBeNull();
    // The issuer is named: a *misdirected* console fails identically to an unreachable one, and
    // the two need different people to fix them.
    expect(panel?.textContent).toContain('http://localhost:8080/realms/aira');
    // And it distinguishes the two things a reader is trying to tell apart.
    expect(panel?.textContent).toContain('identity provider');
  });

  it('does not render the routes behind it', () => {
    // Every screen behind this needs a token. Showing them would fill the page with failures that
    // all have one cause and name none of it.
    const el = renderWithStartupError('http://localhost:8080/realms/aira');

    expect(el.querySelector('router-outlet')).toBeNull();
  });

  it('renders the console normally when the provider is reachable', () => {
    // The guard against a panel that is always on — which would be the same defect with the
    // opposite sign, and would announce itself to a stakeholder rather than to a test.
    const el = renderWithStartupError(null);

    expect(el.querySelector('[data-testid="startup-error"]')).toBeNull();
    expect(el.querySelector('router-outlet')).not.toBeNull();
  });
});

describe('App when the console cannot find out who you are', () => {
  /**
   * Everything role-shaped in the shell comes from `/me`: the username, the role chips, **Logout**,
   * and the incident and oversight navigation. A failure that removes them must say so — a refused
   * action announces itself, an absent one reads as a boundary (`FRD-206`).
   */
  function configureFailing(): void {
    TestBed.resetTestingModule();
    loggedOut = false;
    TestBed.configureTestingModule({
      imports: [App],
      providers: [
        provideRouter([]),
        {
          provide: AuthService,
          useValue: {
            isAuthenticated: () => true,
            startupError: signal<string | null>(null),
            loginLoop: signal<string | null>(null),
            logout: () => {
              loggedOut = true;
            },
          },
        },
        {
          provide: MeService,
          useValue: {
            currency: signal(''),
            get: () =>
              throwError(() => ({
                status: 500,
                error: { error: { message: 'Account service down.' } },
              })),
          },
        },
      ],
    });
  }

  it('says so instead of quietly showing a console with fewer controls', async () => {
    configureFailing();
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;

    const notice = el.querySelector('[data-testid="account-error"]');
    expect(notice).toBeTruthy();
    expect(notice?.textContent).toContain('Account service down.');
  });

  it('leaves the reader able to sign out', async () => {
    // The one action that reliably fixes it — and the header's own `Logout` renders only when
    // `me()` resolved.
    configureFailing();
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;

    const button = el.querySelector<HTMLButtonElement>('[data-testid="account-error"] button');
    expect(button).toBeTruthy();
    button?.click();
    expect(loggedOut).toBe(true);
  });

  it('shows nothing when the account loads', async () => {
    // The paired case: a notice that is always present is a notice nobody reads, and an
    // assertion about something appearing is defended only by one that shows it normally does not.
    configure(true, ['it-security']);
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="account-error"]')).toBeNull();
  });

  it('links the privacy notice from every page', () => {
    configure(true);
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    const link = (fixture.nativeElement as HTMLElement).querySelector<HTMLAnchorElement>(
      '[data-testid="footer-privacy"]',
    );

    expect(link?.getAttribute('href')).toBe('/privacy');
    expect(link?.textContent).toContain('Datenschutzhinweise');
  });
});
