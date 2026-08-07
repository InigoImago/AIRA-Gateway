import { Injectable, inject, signal } from '@angular/core';
import { OAuthService } from 'angular-oauth2-oidc';
import { authConfig } from './auth.config';

/** Facade over angular-oauth2-oidc so components/guards depend on a small surface. */
@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly oauth = inject(OAuthService);
  readonly authenticated = signal(false);

  async init(): Promise<void> {
    this.oauth.configure(authConfig);
    await this.oauth.loadDiscoveryDocumentAndTryLogin();
    this.authenticated.set(this.oauth.hasValidAccessToken());

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
      }
    });
  }

  login(): void {
    this.oauth.initCodeFlow();
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
