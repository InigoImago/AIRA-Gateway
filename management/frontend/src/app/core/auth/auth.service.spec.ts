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
    initCodeFlow: (state?: string) => calls.push(`initCodeFlow:${state ?? ''}`),
    logOut: (noRedirect?: boolean) => calls.push(`logOut:${noRedirect ?? false}`),
    /** Where the login was started from, handed back after the redirect. */
    state: '',
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
    // The login carries where it started from, so a session that ends mid-task does not also
    // cost the reader their place.
    expect(calls[0]).toMatch(/^initCodeFlow:/);
    expect(calls[1]).toBe('logOut:false');
  });

  it('sends a dead session to the login rather than leaving it to fail on every screen', () => {
    // Restarting Keycloak takes the session with it. Before this, the first request after that
    // reported "invalid credentials" — on every panel at once, which reads as the backend
    // rejecting the user rather than as a session that ended.
    const { service, calls } = setup({ hasValidAccessToken: () => false });
    service.reauthenticate();

    // The dead token is dropped first, without a redirect of its own: otherwise the guard on the
    // way back can still see a stored one and the login round-trips for nothing.
    expect(calls).toEqual(['logOut:true', 'initCodeFlow:/']);
    expect(service.authenticated()).toBe(false);
  });

  it('starts one login however many requests fail at once', () => {
    // A screen makes several calls in parallel. Five 401s starting five logins would leave four
    // stale `state` entries and a race over which one comes back.
    const { service, calls } = setup({ hasValidAccessToken: () => false });
    service.reauthenticate();
    service.reauthenticate();
    service.reauthenticate();

    expect(calls.filter((call) => call.startsWith('initCodeFlow')).length).toBe(1);
  });

  it('gives up on a renewal that cannot succeed, instead of waiting for the next 401', async () => {
    // The refresh token is gone, or the session was ended in Keycloak. Waiting for a request to
    // fail means the reader learns about it from an error on the screen they are already reading.
    let valid = true;
    const { service, calls, fire } = setup({ hasValidAccessToken: () => valid });
    await service.init();

    valid = false;
    fire({ type: 'token_refresh_error' });

    expect(calls).toContain('initCodeFlow:/');
  });

  it('does not restart the login when a renewal fails but the token is still good', async () => {
    // A silent-refresh timeout with a token that has not expired yet is a hiccup, not the end of
    // the session — throwing the reader out over it would be its own defect.
    const { service, calls, fire } = setup({ hasValidAccessToken: () => true });
    await service.init();

    fire({ type: 'silent_refresh_timeout' });

    expect(calls.filter((call) => call.startsWith('initCodeFlow'))).toEqual([]);
    expect(service.authenticated()).toBe(true);
  });
});

describe('AuthService — coming back to where the session ended', () => {
  const pathNow = () => window.location.pathname + window.location.search;

  afterEach(() => window.history.replaceState(null, '', '/'));

  it('restores the path the login was started from', async () => {
    const { service } = setup({ state: encodeURIComponent('/use-cases/demo-uc?tab=budgets') });
    await service.init();

    expect(pathNow()).toBe('/use-cases/demo-uc?tab=budgets');
  });

  it('does nothing when there is nowhere to go back to', async () => {
    const { service } = setup({ state: '' });
    await service.init();

    expect(pathNow()).toBe('/');
  });

  it('refuses anything that is not a same-origin path', async () => {
    // `state` survives a round trip through the browser, so treating it as a destination would be
    // an open redirect with extra steps. Both shapes are refused: an absolute URL, and the
    // protocol-relative form that looks like a path and is not.
    for (const hostile of ['https://evil.example/steal', '//evil.example/steal']) {
      const { service } = setup({ state: encodeURIComponent(hostile) });
      await service.init();
      expect(pathNow()).toBe('/');
    }
  });

  it('leaves the URL alone when it is already the right one', async () => {
    const { service } = setup({ state: '/' });
    await service.init();

    expect(pathNow()).toBe('/');
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
