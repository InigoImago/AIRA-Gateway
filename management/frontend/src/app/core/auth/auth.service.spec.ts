import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import {
  OAuthEvent,
  OAuthInfoEvent,
  OAuthService,
  TokenResponse,
  provideOAuthClient,
} from 'angular-oauth2-oidc';
import { Subject } from 'rxjs';
import { authConfig } from './auth.config';
import { AuthService } from './auth.service';

type StubEvent = { type: string };

function setup(overrides: Partial<Record<string, unknown>> = {}) {
  TestBed.resetTestingModule();
  const calls: string[] = [];
  let emit: (event: StubEvent) => void = () => {};
  const oauth = {
    configure: () => calls.push('configure'),
    loadDiscoveryDocumentAndTryLogin: () => {
      calls.push('discovery');
      return Promise.resolve(true);
    },
    hasValidAccessToken: () => true,
    initCodeFlow: (state?: string) => calls.push(`initCodeFlow:${state ?? ''}`),
    // Recorded as JSON: the escape passes an **object** (`{ client_id }`), and flattening it would
    // make the local-only and the provider-side logout indistinguishable.
    logOut: (options?: boolean | Record<string, string>) =>
      calls.push(`logOut:${JSON.stringify(options ?? false)}`),
    /** Where the login was started from, handed back after the redirect. */
    state: '',
    getAccessToken: () => 'token-abc',
    // The renewal half of the facade, stubbed with the real names: a stand-in that silently lacks
    // the method under test lets a session-expiry defect survive a green suite.
    setupAutomaticSilentRefresh: () => calls.push('renewal'),
    events: {
      subscribe: (handler: (event: StubEvent) => void) => {
        emit = handler;
        return { unsubscribe: () => {} };
      },
    },
    ...overrides,
  };
  // The issuer comes from `runtime-config.js`, which a test environment does not have, and an empty
  // issuer is a reported startup failure — so a working startup arranges what a deployment does.
  (window as unknown as { __AIRA_CONFIG__?: unknown }).__AIRA_CONFIG__ = {
    issuer: 'http://keycloak.test/realms/aira',
    clientId: 'aira-gateway',
  };
  TestBed.configureTestingModule({
    providers: [{ provide: OAuthService, useValue: oauth }, AuthService],
  });
  return { service: TestBed.inject(AuthService), calls, fire: (event: StubEvent) => emit(event) };
}

/** Whether a login round trip to the identity provider was started. */
const startedLogin = (calls: string[]) => calls.some((call) => call.startsWith('initCodeFlow'));

// **The login counter lives in `sessionStorage`**, shared by every test in this file (it has to
// survive a full-page navigation). Without clearing it, the third test to sign in again trips the
// loop breaker for the tests after it.
beforeEach(() => window.sessionStorage.clear());

describe('AuthService', () => {
  it('configures the client and picks up an existing session on init', async () => {
    const { service, calls } = setup();
    await service.init();
    expect(calls).toEqual(['configure', 'discovery', 'renewal']);
    expect(service.isAuthenticated()).toBe(true);
  });

  it('reports an unauthenticated session after init', async () => {
    const { service } = setup({ hasValidAccessToken: () => false });
    await service.init();
    expect(service.isAuthenticated()).toBe(false);
  });

  it('arms the renewal, so a session does not simply end mid-use', async () => {
    // Without it the token expires and every screen reports "invalid credentials", which reads as
    // the backend rejecting the user rather than as a session that ran out.
    const { service, calls } = setup();
    await service.init();
    expect(calls).toContain('renewal');
  });

  it('leaves the reader where they are when a token is renewed', async () => {
    // A renewal that worked is the invisible case: no login, no logout, nothing for the page.
    const { service, calls, fire } = setup();
    await service.init();

    fire({ type: 'token_received' });
    fire({ type: 'token_refreshed' });

    expect(startedLogin(calls)).toBe(false);
    expect(calls.filter((call) => call.startsWith('logOut'))).toEqual([]);
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

  it('comes back to the page the reader was going to, when it is named', () => {
    // The guard names the route being entered. The address bar still shows the page being left
    // while a navigation is under way, so reading it would return the reader to the wrong page.
    const { service, calls } = setup();
    service.login('/models?tab=prices');

    expect(calls).toEqual(['initCodeFlow:/models?tab=prices']);
  });

  it('sends a dead session to the login rather than leaving it to fail on every screen', () => {
    // Restarting Keycloak takes the session with it; "invalid credentials" on every panel at once
    // would read as the backend rejecting the user rather than as a session that ended.
    const { service, calls } = setup({ hasValidAccessToken: () => false });
    service.reauthenticate('/api/');

    // The dead token is dropped first, without a redirect of its own: otherwise the guard on the
    // way back can still see a stored one and the login round-trips for nothing.
    expect(calls).toEqual(['logOut:true', 'initCodeFlow:/']);
  });

  it('starts one login however many requests fail at once', () => {
    // A screen makes several calls in parallel. Five 401s starting five logins would leave four
    // stale `state` entries and a race over which one comes back.
    const { service, calls } = setup({ hasValidAccessToken: () => false });
    service.reauthenticate('/api/');
    service.reauthenticate('/gw/');
    service.reauthenticate('/api/');

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
    expect(service.isAuthenticated()).toBe(false);
  });

  it('does not restart the login when a renewal fails but the token is still good', async () => {
    // A refresh that failed with a token that has not expired yet is a hiccup, not the end of the
    // session — throwing the reader out over it would be its own defect.
    const { service, calls, fire } = setup({ hasValidAccessToken: () => true });
    await service.init();

    fire({ type: 'token_refresh_error' });

    expect(startedLogin(calls)).toBe(false);
    expect(service.isAuthenticated()).toBe(true);
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
    // an open redirect. Refused: an absolute URL, the protocol-relative form, and `/\evil.example`,
    // which a URL parser resolves to another origin because `\` is `/` in a special scheme.
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

  it('starts anyway when the address carries a `state` that is not ours to decode', async () => {
    // `state` is whatever the link said, and the library hands it over after decoding it once. A
    // link like `/?state=%3B%25E0%25A4%25A` leaves a malformed escape, and the console runs this
    // inside the application initialiser: a decode that throws there leaves a blank page. Nothing
    // in the link is worth that, so it is ignored like any other unusable destination.
    const { service } = setup({ state: '%E0%A4%A' });

    await expect(service.init()).resolves.toBeUndefined();

    expect(pathNow()).toBe('/');
    expect(service.startupError()).toBeNull();
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
    // A realm that does not permit offline tokens refuses the code-to-token exchange without CORS
    // headers, so the console never renders. The code flow already returns a refresh token, and
    // one that outlives the SSO session is not a credential a governance console should hold.
    expect(authConfig.scope).toBe('openid profile email');
  });

  it('still renews ahead of expiry, with the refresh token rather than an iframe', () => {
    // Renewal is still required: without it the session ends at the token's lifetime and every
    // screen reports "invalid credentials". The hidden-iframe renewal would have to frame the
    // identity provider, which this console's content policy refuses — so it is not configured.
    expect(authConfig.timeoutFactor).toBeLessThan(1);
    expect(authConfig.useSilentRefresh).toBe(false);
  });
});

describe('AuthService — how a session is renewed', () => {
  /**
   * The real library with this console's configuration, rather than a stand-in: what is asserted
   * is the mechanism a browser uses when a token is about to expire, not that a method of the same
   * name was called. A stand-in recorded `setupAutomaticSilentRefresh` as called while every
   * renewal the browser attempted was refused by the content policy.
   */
  afterEach(() => vi.useRealTimers());

  it('renews with the refresh token the code flow returned, never through a hidden iframe', () => {
    vi.useFakeTimers();
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting(), provideOAuthClient()],
    });
    const oauth = TestBed.inject(OAuthService);
    oauth.configure(authConfig);
    const used: string[] = [];
    vi.spyOn(oauth, 'refreshToken').mockImplementation(() => {
      used.push('refresh token');
      return Promise.resolve({} as TokenResponse);
    });
    vi.spyOn(oauth, 'silentRefresh').mockImplementation(() => {
      used.push('iframe');
      return Promise.resolve(new OAuthInfoEvent('silently_refreshed'));
    });

    oauth.setupAutomaticSilentRefresh();
    // What the library's own expiry timer emits at `timeoutFactor` of the token's lifetime.
    (oauth as unknown as { eventsSubject: Subject<OAuthEvent> }).eventsSubject.next(
      new OAuthInfoEvent('token_expires', 'access_token'),
    );
    vi.advanceTimersByTime(1500);

    expect(used).toEqual(['refresh token']);
  });
});

describe('AuthService — a console that does not know its issuer', () => {
  it('says so, and does not try to reach an identity provider it cannot name', async () => {
    /** A fallback issuer would send every user of a deployment whose `runtime-config.js` did not
     *  load to a login page on their own machine. Empty and reported beats plausible and wrong —
     *  reported rather than thrown, because the shell that explains it must still boot. */
    const { service, calls } = setup();
    delete (window as unknown as { __AIRA_CONFIG__?: unknown }).__AIRA_CONFIG__;

    await service.init();

    expect(calls).toEqual([]);
    expect(service.startupError()).toContain('no identity provider is configured');
  });
});

/**
 * The loop across pages.
 *
 * `reauthenticating` prevents five panels starting five logins **in one page**; it cannot see the
 * same refusal coming back **after** the redirect, which destroys the object holding it. Keycloak's
 * SSO session answers at once with a fresh token refused for the same reason, and the page would
 * flicker through the round trip until the account is locked out.
 */
describe('AuthService — signing in again when signing in is not the problem', () => {
  /** A fresh service each time, which is what a full-page navigation actually produces. */
  const afterRedirect = () => setup();

  it('redirects while there is reason to think a login would help', () => {
    const first = afterRedirect();
    first.service.reauthenticate('/api/');

    expect(startedLogin(first.calls)).toBe(true);
    expect(first.service.loginLoop()).toBeNull();
  });

  it('stops redirecting once signing in again has demonstrably not helped', () => {
    // Three round trips, each a new page and therefore a new service — the in-memory guard is
    // `false` every time.
    for (let attempt = 0; attempt < 3; attempt += 1) {
      afterRedirect().service.reauthenticate('/api/');
    }

    const fourth = afterRedirect();
    fourth.service.reauthenticate('/api/');

    expect(startedLogin(fourth.calls)).toBe(false);
    expect(fourth.service.loginLoop()).toContain('did not help 3 times');
  });

  it('is ended by the plane that refused answering', () => {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      afterRedirect().service.reauthenticate('/gw/');
    }

    // The only evidence worth trusting: everything the console can check about itself was just as
    // true on every pass through the loop.
    afterRedirect().service.noteFirstPartySuccess('/gw/');

    const next = afterRedirect();
    next.service.reauthenticate('/gw/');
    expect(startedLogin(next.calls)).toBe(true);
    expect(next.service.loginLoop()).toBeNull();
  });

  it('is not ended by the other plane answering', () => {
    // The realistic loop with two planes and two token validators: management accepts the token
    // and the gateway refuses it. The shell's own `/api/v1/me` answers on every pass, and an answer
    // from the plane that never refused says nothing about the one that does.
    for (let page = 0; page < 3; page += 1) {
      const pass = afterRedirect();
      pass.service.noteFirstPartySuccess('/api/');
      pass.service.reauthenticate('/gw/');
      pass.service.noteFirstPartySuccess('/api/');
    }

    const fourth = afterRedirect();
    fourth.service.noteFirstPartySuccess('/api/');
    fourth.service.reauthenticate('/gw/');

    expect(startedLogin(fourth.calls)).toBe(false);
    expect(fourth.service.loginLoop()).toContain('did not help 3 times');
  });

  it('is ended by any first-party answer after a renewal that failed', () => {
    // No plane refused: the refresh token stopped working. A login that is followed by any answer
    // from either plane is a login that worked.
    for (let attempt = 0; attempt < 3; attempt += 1) {
      afterRedirect().service.reauthenticate();
    }
    afterRedirect().service.noteFirstPartySuccess('/api/');

    const next = afterRedirect();
    next.service.reauthenticate();
    expect(startedLogin(next.calls)).toBe(true);
  });

  it('counts the logins that happened, not the refusals that came after it stopped', () => {
    // Three panels refusing on the page where the loop stopped are one page, not three more
    // failed sign-ins — and the message is the reader's only account of what happened.
    for (let attempt = 0; attempt < 3; attempt += 1) {
      afterRedirect().service.reauthenticate('/api/');
    }

    const stopped = afterRedirect();
    stopped.service.reauthenticate('/api/');
    stopped.service.reauthenticate('/gw/');
    stopped.service.reauthenticate('/api/');
    expect(stopped.service.loginLoop()).toContain('did not help 3 times');

    // Reloading the stopped page starts no login and claims no extra one either.
    const reloaded = afterRedirect();
    reloaded.service.reauthenticate('/api/');
    expect(startedLogin(reloaded.calls)).toBe(false);
    expect(reloaded.service.loginLoop()).toContain('did not help 3 times');
  });

  it('signing out completely ends the session at the provider, not only here', () => {
    const { service, calls } = afterRedirect();
    service.reauthenticate('/api/');
    service.signOutCompletely();

    // `logOut(true)` is the local-only form `reauthenticate` uses; the escape needs the other one,
    // or the SSO session signs the reader straight back in. And it carries `client_id`, because
    // the loop has already removed the `id_token` the provider would otherwise take as the hint.
    const escape = calls.filter((call) => call.startsWith('logOut:')).at(-1);
    expect(escape).not.toBe('logOut:true');
    expect(escape).toContain('client_id');
    expect(service.loginLoop()).toBeNull();
    expect(window.sessionStorage.getItem('aira.reauth-attempts')).toBeNull();
  });

  it('counts a window rather than a lifetime', () => {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      afterRedirect().service.reauthenticate('/api/');
    }
    // Two minutes later this is a new session ending, not the same one repeating.
    const stale = JSON.parse(window.sessionStorage.getItem('aira.reauth-attempts') ?? '{}');
    window.sessionStorage.setItem(
      'aira.reauth-attempts',
      JSON.stringify({ ...stale, first: Date.now() - 3 * 60 * 1000 }),
    );

    const later = afterRedirect();
    later.service.reauthenticate('/api/');
    expect(startedLogin(later.calls)).toBe(true);
  });

  it('survives storage it cannot read', () => {
    window.sessionStorage.setItem('aira.reauth-attempts', 'not json');

    const { service, calls } = afterRedirect();
    service.reauthenticate('/api/');

    // Errs towards letting a login happen: the failure this guard prevents is a storm, not one
    // redirect.
    expect(startedLogin(calls)).toBe(true);
  });
});
