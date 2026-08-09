import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';
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
          logout: () => {
            loggedOut = true;
          },
        },
      },
      { provide: MeService, useValue: { get: () => of({ ...baseMe, roles }) } },
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
    expect(el.querySelector('[data-role="use-case-user"]')).toBeNull();
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
    const el = render(['use-case-admin']);

    expect(el.querySelector('[data-testid="nav-requests"]')).toBeNull();
  });
});
