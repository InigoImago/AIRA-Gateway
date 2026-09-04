import { Injectable, inject, signal } from '@angular/core';
import { OAuthService } from 'angular-oauth2-oidc';
import { authConfig } from './auth.config';

/**
 * Where the login attempts are counted. `sessionStorage` and not a field, because the thing being
 * counted is a **full-page navigation to Keycloak and back** — which destroys every field this
 * application has. Per tab, and gone when the tab closes, which is the right lifetime for
 * "am I going round in circles right now".
 */
const LOOP_KEY = 'aira.reauth-attempts';

/**
 * How many logins may be started in {@link LOOP_WINDOW_MS} before the console stops trying.
 *
 * Three, because one is an ordinary expiry, two is that plus a race between panels, and a third
 * inside two minutes is not a session ending — it is the same refusal coming back. Keycloak's own
 * brute-force default trips at thirty, and the point of this number is to be reached long before
 * an account is locked.
 */
const LOOP_LIMIT = 3;
const LOOP_WINDOW_MS = 2 * 60 * 1000;

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

  /**
   * Set when signing in again has demonstrably stopped helping — see {@link reauthenticate}.
   *
   * Rendered instead of the routes, like {@link startupError}: every screen behind it needs a
   * token the API is refusing, so showing them would fill the page with failures that have one
   * cause and name none of it.
   */
  readonly loginLoop = signal<string | null>(null);

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
   * Guarded twice, against two different loops, and the second guard is the one that matters.
   *
   * **Within one page**, five panels each getting a 401 would otherwise start five logins, and the
   * last one wins the `state` while the others leave stale entries behind. `reauthenticating` is
   * that guard, and it is never cleared because the only thing that follows is a full-page
   * navigation to Keycloak.
   *
   * **Across pages**, that flag is worse than useless: the navigation it describes destroys the
   * service holding it, so on the way back it is `false` again. And there is a real case where the
   * way back leads straight here — the API refusing a token for a reason a **fresh token does not
   * change**. Keycloak still has a valid SSO session, so it answers the authorization request
   * without asking anybody anything and redirects back at once; the console exchanges the code,
   * calls the API, is refused again, and redirects again. Reported from use: the page flickers
   * through the round trip as fast as the browser can navigate, throwing an error each time, until
   * Keycloak's brute-force limit locks the account out.
   *
   * A login cannot fix a refusal that is not about the login. So the attempts are counted in
   * `sessionStorage` — which survives the redirect precisely because it is not in this object —
   * and past {@link LOOP_LIMIT} within {@link LOOP_WINDOW_MS} this stops and says so instead.
   * The counter is cleared by the first first-party call that succeeds, which is the only honest
   * evidence that the loop is over.
   */
  reauthenticate(): void {
    if (this.reauthenticating) return;

    const attempts = this.recordReauthAttempt();
    if (attempts > LOOP_LIMIT) {
      // Deliberately not another redirect, and deliberately not a silent stop: a console that
      // simply gave up would look like the one that was flickering, minus the explanation.
      this.authenticated.set(false);
      this.loginLoop.set(
        `signing in again did not help ${attempts - 1} times in a row. The identity provider is ` +
          'accepting you and this installation is refusing the token it issues, so another login ' +
          'would be the same round trip.',
      );
      return;
    }

    this.reauthenticating = true;
    this.authenticated.set(false);
    // Drop the dead token first: without this the guard on the way back can still see a stored
    // one and the login round-trips for nothing.
    this.oauth.logOut(true);
    this.oauth.initCodeFlow(this.currentPath());
  }

  /**
   * A first-party call answered — so whatever the last refusal was, it is over.
   *
   * The one signal worth trusting. Anything the console could check about *itself* — a token that
   * parses, an expiry in the future — is exactly what was true on every pass through the loop.
   */
  noteFirstPartySuccess(): void {
    this.clearReauthAttempts();
  }

  /**
   * Leave properly: end the session at the identity provider, not just here.
   *
   * The escape from the loop above, and the only one that works. Keycloak is the half that keeps
   * saying yes — clearing tokens locally sends the reader back to an SSO session that signs them
   * straight in again, which is the loop with an extra step. `logOut()` without an argument is the
   * RP-initiated logout, which ends the session at the provider.
   */
  signOutCompletely(): void {
    this.clearReauthAttempts();
    this.loginLoop.set(null);
    // **`client_id`, because by this point there is no `id_token` left to identify us with.**
    //
    // RP-initiated logout wants either an `id_token_hint` or a `client_id` alongside a
    // `post_logout_redirect_uri`, and the library only sends the hint when an id token is in
    // storage. Every pass through the loop called `logOut(true)`, which removes it — so the one
    // state this button exists for is exactly the state where the hint is gone, and Keycloak
    // answers `Invalid parameter: post_logout_redirect_uri` instead of ending the session.
    //
    // Found by the browser test rather than by reading: the loop stopped correctly and the way out
    // led to an error page. A guard whose escape does not work is a guard that traps somebody.
    this.oauth.logOut({ client_id: authConfig.clientId ?? '' });
  }

  /** How many logins have been started in the current window, this one included. */
  private recordReauthAttempt(): number {
    const now = Date.now();
    const previous = this.readReauthAttempts();
    const within = previous !== null && now - previous.first < LOOP_WINDOW_MS;
    const record = within
      ? { count: previous.count + 1, first: previous.first }
      : { count: 1, first: now };
    try {
      window.sessionStorage?.setItem(LOOP_KEY, JSON.stringify(record));
    } catch {
      // Storage can be unavailable — a private window, a policy. The loop guard is then only as
      // good as the in-memory one, which is the behaviour this replaced rather than a regression.
    }
    return record.count;
  }

  private readReauthAttempts(): { count: number; first: number } | null {
    try {
      const raw = window.sessionStorage?.getItem(LOOP_KEY);
      if (!raw) return null;
      const parsed: unknown = JSON.parse(raw);
      if (
        typeof parsed === 'object' &&
        parsed !== null &&
        typeof (parsed as { count?: unknown }).count === 'number' &&
        typeof (parsed as { first?: unknown }).first === 'number'
      ) {
        return parsed as { count: number; first: number };
      }
    } catch {
      // Unreadable or not ours. Treated as no attempts, which errs towards letting a login
      // happen — the failure this guard prevents is a storm, not a single redirect.
    }
    return null;
  }

  private clearReauthAttempts(): void {
    try {
      window.sessionStorage?.removeItem(LOOP_KEY);
    } catch {
      // See above.
    }
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
