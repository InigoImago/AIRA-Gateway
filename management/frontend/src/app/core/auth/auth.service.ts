import { Injectable, inject, signal } from '@angular/core';
import { OAuthService } from 'angular-oauth2-oidc';
import { authConfig } from './auth.config';

/** Facade over angular-oauth2-oidc so components/guards depend on a small surface. */
@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly oauth = inject(OAuthService);
  readonly authenticated = signal(false);
  /** Set once a re-login has been started, so concurrent 401s do not each start their own. */
  private reauthenticating = false;

  async init(): Promise<void> {
    this.oauth.configure(authConfig);
    await this.oauth.loadDiscoveryDocumentAndTryLogin();
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
    const target = this.oauth.state;
    if (!target || typeof window === 'undefined') return;
    const path = decodeURIComponent(target);
    // Only a same-origin path, never a URL: `state` survives a round trip through the browser, so
    // treating it as a destination would be an open redirect with extra steps.
    if (!path.startsWith('/') || path.startsWith('//')) return;
    if (path !== window.location.pathname + window.location.search) {
      window.history.replaceState(null, '', path);
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
