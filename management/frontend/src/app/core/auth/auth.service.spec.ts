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
  // The console's issuer comes from `runtime-config.js`, which the container writes at start and
  // `ng serve` serves from `public/`. A test environment has neither, and an empty issuer is now a
  // *reported* failure rather than a silent localhost — so the tests that exercise a working
  // startup have to arrange what a deployment arranges.
  (window as unknown as { __AIRA_CONFIG__?: unknown }).__AIRA_CONFIG__ = {
    issuer: 'http://keycloak.test/realms/aira',
    clientId: 'aira-gateway',
  };
  TestBed.configureTestingModule({
    providers: [{ provide: OAuthService, useValue: oauth }, AuthService],
  });
  return { service: TestBed.inject(AuthService), calls, fire: (event: OAuthEvent) => emit(event) };
}

// **The login counter lives in `sessionStorage`**, which is shared by every test in this file —
// that is the whole point of it (`AuthService.reauthenticate`: it has to survive a full-page
// navigation, so it cannot live in the service). Without this, the third test to sign in again
// trips the loop breaker and the ones after it fail for a reason that has nothing to do with them.
beforeEach(() => window.sessionStorage.clear());

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
    // `/\evil.example` is the one the pattern-matching guard let through: it is not the
    // protocol-relative form, and a URL parser resolves it to one anyway — `\` is `/` in a
    // special scheme. Listed here beside the two shapes that were already refused, because a
    // guard whose sibling cases are tested and whose third is not is the narrowest kind.
    for (const hostile of [
      'https://evil.example/steal',
      '//evil.example/steal',
      '/\\evil.example/steal',
      '/\\/evil.example/steal',
    ]) {
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

describe('AuthService — a console that does not know its issuer', () => {
  it('says so, and does not try to reach an identity provider it cannot name', async () => {
    /** The fallback used to be `http://localhost:8080/realms/aira`: a deployment whose
     *  `runtime-config.js` did not load sent every user to a login page on whatever machine their
     *  browser sat at, and the error named neither the realm nor the reason. Empty and reported
     *  beats plausible and wrong.
     *
     *  Reported rather than thrown: `authConfig` is read at startup, before the shell that could
     *  explain a thrown error exists — throwing took four test suites down when it was tried. */
    const { service, calls } = setup();
    delete (window as unknown as { __AIRA_CONFIG__?: unknown }).__AIRA_CONFIG__;

    await service.init();

    expect(calls).toEqual([]);
    expect(service.startupError()).toContain('no identity provider is configured');
  });
});

/**
 * The loop the guard above could not see.
 *
 * `reauthenticating` prevents five panels starting five logins **in one page**. It cannot prevent
 * the same refusal coming back **after** the redirect, because the redirect destroys the object
 * holding it — and that is the loop somebody actually meets: Keycloak still has a valid SSO
 * session, answers the authorization request without asking anything, and sends a fresh token that
 * is refused for the same reason as the last one. Reported from use: the page flickers through the
 * round trip as fast as the browser can navigate, until the account is locked out.
 */
describe('AuthService — signing in again when signing in is not the problem', () => {
  /** A fresh service each time, which is what a full-page navigation actually produces. */
  const afterRedirect = () => setup();

  it('redirects while there is reason to think a login would help', () => {
    const first = afterRedirect();
    first.service.reauthenticate();

    expect(first.calls.some((c) => c.startsWith('initCodeFlow'))).toBe(true);
    expect(first.service.loginLoop()).toBeNull();
  });

  it('stops redirecting once signing in again has demonstrably not helped', () => {
    // Three round trips, each a new page and therefore a new service — the in-memory guard is
    // `false` every time, which is precisely why it never saw this.
    for (let attempt = 0; attempt < 3; attempt += 1) {
      afterRedirect().service.reauthenticate();
    }

    const fourth = afterRedirect();
    fourth.service.reauthenticate();

    expect(fourth.calls.some((c) => c.startsWith('initCodeFlow'))).toBe(false);
    expect(fourth.service.loginLoop()).toContain('did not help');
    expect(fourth.service.authenticated()).toBe(false);
  });

  it('a first-party call that answers ends it', () => {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      afterRedirect().service.reauthenticate();
    }

    // The only evidence worth trusting: everything the console can check about itself was just as
    // true on every pass through the loop.
    afterRedirect().service.noteFirstPartySuccess();

    const next = afterRedirect();
    next.service.reauthenticate();
    expect(next.calls.some((c) => c.startsWith('initCodeFlow'))).toBe(true);
    expect(next.service.loginLoop()).toBeNull();
  });

  it('signing out completely ends the session at the provider, not only here', () => {
    const { service, calls } = afterRedirect();
    service.reauthenticate();
    service.signOutCompletely();

    // `logOut(true)` is the local-only form and is what `reauthenticate` uses. The escape needs
    // the other one: Keycloak is the half that keeps saying yes, and a local clear sends the
    // reader back to an SSO session that signs them straight in again.
    expect(calls).toContain('logOut:false');
    expect(service.loginLoop()).toBeNull();
    expect(window.sessionStorage.getItem('aira.reauth-attempts')).toBeNull();
  });

  it('counts a window rather than a lifetime', () => {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      afterRedirect().service.reauthenticate();
    }
    // Two minutes later this is a new session ending, not the same one repeating.
    const stale = JSON.parse(window.sessionStorage.getItem('aira.reauth-attempts') ?? '{}');
    window.sessionStorage.setItem(
      'aira.reauth-attempts',
      JSON.stringify({ ...stale, first: Date.now() - 3 * 60 * 1000 }),
    );

    const later = afterRedirect();
    later.service.reauthenticate();
    expect(later.calls.some((c) => c.startsWith('initCodeFlow'))).toBe(true);
  });

  it('survives storage it cannot read', () => {
    window.sessionStorage.setItem('aira.reauth-attempts', 'not json');

    const { service, calls } = afterRedirect();
    service.reauthenticate();

    // Errs towards letting a login happen: the failure this guard prevents is a storm, not one
    // redirect, and a console that refused to sign anybody in because of a stray string would be
    // a worse bug than the one being fixed.
    expect(calls.some((c) => c.startsWith('initCodeFlow'))).toBe(true);
  });
});
