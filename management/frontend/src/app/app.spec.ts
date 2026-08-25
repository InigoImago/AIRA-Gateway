import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of, throwError } from 'rxjs';
import { App } from './app';
import { MeService } from './core/api/me.service';
import { Me } from './core/api/models';
import { AuthService } from './core/auth/auth.service';

const baseMe: Me = {
  subject: 's',
  username: 'demo',
  email: 'demo@x',
  roles: [],
  use_cases: [],
};

let loggedOut = false;

function configure(authenticated: boolean, roles: string[] = []): void {
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
          logout: () => {
            loggedOut = true;
          },
        },
      },
      {
        provide: MeService,
        useValue: { currency: signal(''), get: () => of({ ...baseMe, roles }) },
      },
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
    expect(el.textContent).toContain('Use Cases');
    expect(el.querySelector('.aira-user')?.textContent).toContain('demo');
  });

  it('names the role in the header instead of showing tabs that do not respond', () => {
    // The console used to render a disabled tab per oversight role — "Security", "Governance",
    // "Administration" — for screens that do not exist. Somebody logging in as the administrator
    // clicked "Administration" and nothing happened, which teaches only that something is broken.
    //
    // What a role *is* answers the question they actually have. The tabs come back when
    // `FRD-500`/`501`/`503` build the console behind them.
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
    expect(el.textContent).toContain('Global administrator');
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
  // ---- the requests screen (`FRD-505`) -------------------------------------------------------

  function render(roles: string[]): HTMLElement {
    configure(true, roles);
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  it('offers cross-use-case requests to a role that may act on an incident', () => {
    const el = render(['it-security']);

    expect(el.querySelector('[data-testid="nav-requests"]')).not.toBeNull();
  });

  it('does not offer it to a role that sees figures and not content', () => {
    /** `it-steuerung` sees every use case and reads no prompts. Offering the screen and refusing
     *  on use is `FRD-206`'s defect; withholding the tab is the boundary stated plainly. */
    const el = render(['it-steuerung']);

    expect(el.querySelector('[data-testid="nav-requests"]')).toBeNull();
    expect(el.querySelector('[data-testid="nav-security"]')).not.toBeNull();
  });

  it('does not offer it to somebody who only runs a use case', () => {
    const el = render([]);

    expect(el.querySelector('[data-testid="nav-requests"]')).toBeNull();
  });
});

describe('App when the identity provider cannot be reached', () => {
  /**
   * The console said **nothing at all** in this case until 2026-08-11.
   *
   * `AuthService.init()` runs in an app initialiser, and a rejected initialiser makes
   * `bootstrapApplication` reject — so an unreachable Keycloak produced a completely white page
   * with a `200` from the web server. A reader cannot tell "the login service is down" from "this
   * application is broken", and they report the second. It cost a real afternoon: the stack's
   * infrastructure had crashed, and the console was indistinguishable from a broken deployment
   * of itself.
   *
   * These cases are about what a person **sees**, which is why they assert on rendered text
   * rather than on the signal: a flag nobody renders is the same blank page with better
   * bookkeeping.
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
   * `/me` had no error branch, and everything role-shaped in the shell comes from it: the
   * username, the role chips, **Logout**, and the nav entries for investigating an incident and
   * for oversight. A failure removed all of them and said nothing — so an IT Security reader saw
   * a console built for somebody with fewer rights, with no error to explain it and no way to
   * sign out.
   *
   * That is `FRD-206`'s complaint inverted, and the inverted one is the harder to notice: a
   * refused action announces itself, an absent one reads as a boundary.
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
    // The one action that reliably fixes it, and the one the failure used to take away: `Logout`
    // lives inside the block that renders only when `me()` resolved.
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
});
