import { TestBed } from '@angular/core/testing';
import { OAuthService } from 'angular-oauth2-oidc';
import { authConfig } from './auth.config';
import { AuthService } from './auth.service';

type OAuthEvent = { type: string };

function setup(overrides: Partial<Record<string, unknown>> = {}) {
  TestBed.resetTestingModule();
  const calls: string[] = [];
  let emit: (event: OAuthEvent) => void = () => {};
  const oauth = {
    configure: () => calls.push('configure'),
    loadDiscoveryDocumentAndTryLogin: () => {
      calls.push('discovery');
      return Promise.resolve(true);
    },
    hasValidAccessToken: () => true,
    initCodeFlow: () => calls.push('initCodeFlow'),
    logOut: () => calls.push('logOut'),
    getAccessToken: () => 'token-abc',
    // The renewal half of the facade. Stubbed with the real names rather than left out: a
    // stand-in that silently lacks the method under test is how a session-expiry defect survives
    // a green suite — which is exactly how this one did.
    setupAutomaticSilentRefresh: () => calls.push('silentRefresh'),
    events: {
      subscribe: (handler: (event: OAuthEvent) => void) => {
        emit = handler;
        return { unsubscribe: () => {} };
      },
    },
    ...overrides,
  };
  TestBed.configureTestingModule({
    providers: [{ provide: OAuthService, useValue: oauth }, AuthService],
  });
  return { service: TestBed.inject(AuthService), calls, fire: (event: OAuthEvent) => emit(event) };
}

describe('AuthService', () => {
  it('configures the client and picks up an existing session on init', async () => {
    const { service, calls } = setup();
    await service.init();
    expect(calls).toEqual(['configure', 'discovery', 'silentRefresh']);
    expect(service.authenticated()).toBe(true);
    expect(service.isAuthenticated()).toBe(true);
  });

  it('reports an unauthenticated session after init', async () => {
    const { service } = setup({ hasValidAccessToken: () => false });
    await service.init();
    expect(service.authenticated()).toBe(false);
  });

  it('arms the silent refresh, so a session does not simply end mid-use', async () => {
    // Without this the access token expires and every screen starts reporting "invalid
    // credentials" — which reads as the backend rejecting the user rather than as a session that
    // ran out, and in a console about spend and controls that makes somebody doubt the data.
    const { service, calls } = setup();
    await service.init();
    expect(calls).toContain('silentRefresh');
  });

  it('stays authenticated when a token is renewed', async () => {
    const { service, fire } = setup({ hasValidAccessToken: () => false });
    await service.init();
    expect(service.authenticated()).toBe(false);

    fire({ type: 'token_refreshed' });
    expect(service.authenticated()).toBe(true);
  });

  it('reflects a failed renewal instead of pretending the session is alive', async () => {
    let valid = true;
    const { service, fire } = setup({ hasValidAccessToken: () => valid });
    await service.init();

    valid = false;
    fire({ type: 'silent_refresh_error' });
    expect(service.authenticated()).toBe(false);
  });

  it('starts the code flow on login and ends the session on logout', () => {
    const { service, calls } = setup();
    service.login();
    service.logout();
    expect(calls).toEqual(['initCodeFlow', 'logOut']);
  });

  it('exposes the access token, and an empty string when there is none', () => {
    expect(setup().service.accessToken).toBe('token-abc');
    expect(setup({ getAccessToken: () => null }).service.accessToken).toBe('');
  });
});

describe('the scopes the console asks for', () => {
  it('does not ask for an offline token', () => {
    // This one line took the whole console down. A realm that does not permit offline tokens
    // answers the code-to-token exchange with `not_allowed`, and answers it *without* CORS
    // headers — so the browser reports a CORS error naming neither the scope nor the setting.
    // Nothing rendered at all.
    //
    // It was also unnecessary: the authorization-code flow already returns a refresh token.
    // `offline_access` asks for one that outlives the SSO session, which is a credential a
    // governance console has no business holding.
    expect(authConfig.scope).toBe('openid profile email');
  });

  it('still renews ahead of expiry, which is what the scope was reached for', () => {
    // The requirement survives; only the instrument was wrong. Without these the session ends at
    // the token's lifetime and every screen reports "invalid credentials", which reads as the
    // data being untrustworthy rather than the session having run out.
    expect(authConfig.timeoutFactor).toBeLessThan(1);
    expect(authConfig.silentRefreshRedirectUri).toContain('/silent-refresh.html');
  });
});
