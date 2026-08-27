import { Injectable, inject, signal } from '@angular/core';
import { OAuthService } from 'angular-oauth2-oidc';
import { authConfig } from './auth.config';

/** Facade over angular-oauth2-oidc so components/guards depend on a small surface. */
@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly oauth = inject(OAuthService);
  readonly authenticated = signal(false);
  /**
   * Set when the identity provider could not be reached at startup.
   *
   * **This is why it exists.** `init()` runs in an app initialiser, and a rejected initialiser
   * makes `bootstrapApplication` reject — so an unreachable Keycloak produced a **completely white
   * page**: no message, no header, no hint, and a `200` from the web server. On 2026-08-11 that
   * cost real time: the stack's infrastructure had crashed, and the console was indistinguishable
   * from a broken deployment of itself. A reader cannot tell "the login service is down" from
   * "this application is broken", and they will report the second.
   *
   * So a failure here is **recorded, not thrown**: the app boots, and the shell renders a page
   * that says what is wrong and what to do. That is the same rule this console already applies to
   * every load and mutation (`core/api/error-message.ts`) — *no silent failures* — applied to the
   * one step that runs before any of that exists.
   */
  readonly startupError = signal<string | null>(null);
  /** Set once a re-login has been started, so concurrent 401s do not each start their own. */
  private reauthenticating = false;

  async init(): Promise<void> {
    if (!authConfig.issuer) {
      // A console that does not know its issuer cannot log anybody in, and must not pretend it is
      // merely unreachable: the fix is a deployment one and the message says so, rather than
      // sending somebody to check whether Keycloak is up.
      this.startupError.set(
        'no identity provider is configured — runtime-config.js did not load, or carries no ' +
          'issuer. It is written at container start from AIRA_OIDC_ISSUER.',
      );
      return;
    }
    this.oauth.configure(authConfig);
    try {
      await this.oauth.loadDiscoveryDocumentAndTryLogin();
    } catch (error: unknown) {
      // Deliberately swallowed, and the reason is in `startupError` above: rethrowing here is
      // exactly what produced the blank page. The issuer is named because a *misdirected* console
      // fails the same way as an unreachable one, and the two need different people to fix them.
      this.startupError.set(authConfig.issuer ?? 'the configured issuer');
      console.error('AIRA: the identity provider could not be reached at startup', error);
      return;
    }
    this.authenticated.set(this.oauth.hasValidAccessToken());
    this.restoreLocation();

    // **Renew before it expires.** Without this the session simply ends after the token's lifetime
    // and every screen starts reporting "invalid credentials" — which reads as the backend
    // rejecting the user, not as a session that ran out. In a console whose whole purpose is to
    // show whether spending and controls are working, an error that looks like the *data* is
    // untrustworthy is worse than an error that says "log in again".
    this.oauth.setupAutomaticSilentRefresh();

    // And when renewal genuinely fails — the refresh token is gone, the session was ended in
    // Keycloak — say so once and send them to the login, instead of leaving a dead console
    // answering 401 to everything.
    this.oauth.events.subscribe((event) => {
      if (event.type === 'token_received' || event.type === 'token_refreshed') {
        this.authenticated.set(true);
      }
      if (
        event.type === 'silent_refresh_timeout' ||
        event.type === 'silent_refresh_error' ||
        event.type === 'token_refresh_error'
      ) {
        this.authenticated.set(this.oauth.hasValidAccessToken());
        // Renewal failed and there is nothing left to renew: the refresh token is gone, or
        // Keycloak was restarted and the session went with it. Do not wait for the next request
        // to produce a 401 on a screen the reader is already looking at.
        if (!this.oauth.hasValidAccessToken()) {
          this.reauthenticate();
        }
      }
    });
  }

  login(): void {
    this.oauth.initCodeFlow(this.currentPath());
  }

  /**
   * The session is over — send them to the login rather than to an error message.
   *
   * Guarded, because a screen makes several requests at once: five panels each getting a 401
   * would otherwise start five logins, and the last one wins the `state` while the others leave
   * stale entries behind. The flag is never cleared, because the only thing that follows is a
   * full-page navigation to Keycloak.
   */
  reauthenticate(): void {
    if (this.reauthenticating) return;
    this.reauthenticating = true;
    this.authenticated.set(false);
    // Drop the dead token first: without this the guard on the way back can still see a stored
    // one and the login round-trips for nothing.
    this.oauth.logOut(true);
    this.oauth.initCodeFlow(this.currentPath());
  }

  /**
   * Put the reader back where the session ended.
   *
   * `state` is whatever `initCodeFlow` was given, handed back after the redirect. Applied with
   * `replaceState` rather than a router navigation because this runs in an app initialiser, before
   * the router exists — the router then boots on the restored URL, which is the same thing without
   * a second navigation the reader would see.
   */
  private restoreLocation(): void {
    const stored = this.oauth.state;
    if (!stored || typeof window === 'undefined') return;
    const path = decodeURIComponent(stored);
    // Only a same-origin path, never a URL: `state` survives a round trip through the browser, so
    // treating it as a destination would be an open redirect with extra steps.
    //
    // **Resolved rather than pattern-matched.** The guard was `startsWith('/') && !startsWith('//')`,
    // which is the rule one character narrower than it reads: a URL parser treats `\` as `/` in a
    // special scheme, so `/\evil.example` is not the protocol-relative form and resolves to one —
    // `new URL('/\\evil.example', origin).origin` is `https://evil.example`. The two shapes the
    // test names were refused and the third, which looks least like a URL, was not.
    //
    // Asking the browser's own parser is what makes the check the same width as the sentence
    // above it: whatever `replaceState` would resolve this to is what gets compared, so a fourth
    // spelling nobody thought of is refused by construction rather than by being listed.
    if (!path.startsWith('/')) return;
    let resolved: URL;
    try {
      resolved = new URL(path, window.location.origin);
    } catch {
      return;
    }
    if (resolved.origin !== window.location.origin) return;
    const target = resolved.pathname + resolved.search;
    if (target !== window.location.pathname + window.location.search) {
      window.history.replaceState(null, '', target);
    }
  }

  /** Where to come back to. A session that ends mid-task should not also cost the reader their
   * place — angular-oauth2-oidc hands `state` back after the redirect. */
  private currentPath(): string {
    if (typeof window === 'undefined') return '';
    return window.location.pathname + window.location.search;
  }

  logout(): void {
    this.oauth.logOut();
  }

  isAuthenticated(): boolean {
    return this.oauth.hasValidAccessToken();
  }

  get accessToken(): string {
    return this.oauth.getAccessToken() ?? '';
  }
}
