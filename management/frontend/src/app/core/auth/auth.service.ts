import { Injectable, inject, signal } from '@angular/core';
import { OAuthService } from 'angular-oauth2-oidc';
import { authConfig } from './auth.config';

/**
 * Where login attempts are counted. `sessionStorage`, not a field: what is counted is a full-page
 * navigation to Keycloak and back, which destroys every field this application has. Per tab, and
 * gone when the tab closes.
 */
const LOOP_KEY = 'aira.reauth-attempts';

/**
 * How many logins may start within {@link LOOP_WINDOW_MS} before the console stops trying.
 *
 * One is an ordinary expiry, two is that plus a race between panels; a third inside two minutes is
 * the same refusal coming back. Reached long before Keycloak's brute-force limit locks an account.
 */
const LOOP_LIMIT = 3;
const LOOP_WINDOW_MS = 2 * 60 * 1000;

/**
 * The logins started in the current window, and which first-party prefix refused the session
 * before the last of them — `null` when none did and a renewal failed instead.
 */
interface ReauthAttempts {
  count: number;
  first: number;
  refusedBy: string | null;
}

/** Facade over angular-oauth2-oidc so components and guards depend on a small surface. */
@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly oauth = inject(OAuthService);
  /**
   * Set when the identity provider could not be reached at startup.
   *
   * `init()` runs in an app initialiser, and a rejected initialiser leaves a blank page that cannot
   * be told apart from a broken deployment. So the failure is **recorded, not thrown**: the app
   * boots and the shell says what is wrong — no silent failures, before anything else exists.
   */
  readonly startupError = signal<string | null>(null);
  /** Set once a re-login has started, so concurrent 401s do not each start their own. */
  private reauthenticating = false;

  /**
   * Set when signing in again has stopped helping — see {@link reauthenticate}. Rendered instead of
   * the routes: every screen behind it needs a token the API is refusing.
   */
  readonly loginLoop = signal<string | null>(null);

  async init(): Promise<void> {
    if (!authConfig.issuer) {
      // A deployment fault, said as one — not a reason to check whether Keycloak is up.
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
      // Swallowed on purpose (see `startupError`). The issuer is named: a misdirected console fails
      // like an unreachable one, and the two need different people to fix them.
      this.startupError.set(authConfig.issuer ?? 'the configured issuer');
      console.error('AIRA: the identity provider could not be reached at startup', error);
      return;
    }
    this.restoreLocation();

    // Renew before expiry: a session that simply ends reads as the backend rejecting the user. The
    // method is the library's name for automatic renewal; with `useSilentRefresh: false` it renews
    // with the refresh token (`auth.config.ts`).
    this.oauth.setupAutomaticSilentRefresh();

    // When renewal genuinely fails, send the reader to the login once, instead of leaving a console
    // that answers 401 to everything. A token that is still good keeps the reader where they are:
    // one failed refresh is a hiccup, and the next attempt may succeed.
    this.oauth.events.subscribe((event) => {
      if (event.type === 'token_refresh_error' && !this.oauth.hasValidAccessToken()) {
        this.reauthenticate();
      }
    });
  }

  /**
   * Start the login, coming back to `target` afterwards — the route being entered, which the guard
   * knows — or to the current page. The address bar is the page being *left* while a navigation is
   * under way, so it is only the fallback.
   */
  login(target?: string): void {
    this.oauth.initCodeFlow(target ?? this.currentPath());
  }

  /**
   * The session is over — send the reader to the login rather than to an error message.
   *
   * `refusedBy` is the first-party prefix whose call answered 401, or `null` when no plane refused
   * and a renewal failed instead.
   *
   * Guarded against two loops:
   *
   * - **Within one page**, `reauthenticating` stops five panels' 401s from starting five logins. It
   *   is never cleared: the only thing that follows is a full-page navigation.
   * - **Across pages**, where that flag does not survive the redirect. When the API refuses a token
   *   for a reason a fresh token does not change, Keycloak's SSO session answers at once and the
   *   console would redirect as fast as the browser navigates, until the account is locked. So the
   *   attempts are counted in `sessionStorage`, and past {@link LOOP_LIMIT} within
   *   {@link LOOP_WINDOW_MS} this stops and says so. Only an answer from the plane that refused
   *   clears the count ({@link noteFirstPartySuccess}).
   */
  reauthenticate(refusedBy: string | null = null): void {
    if (this.reauthenticating) return;

    const now = Date.now();
    const previous = this.attemptsWithinWindow(now);
    const attempts = (previous?.count ?? 0) + 1;
    if (attempts > LOOP_LIMIT) {
      // Neither another redirect nor a silent stop: the reader is told why it stopped.
      this.loginLoop.set(
        `signing in again did not help ${attempts - 1} times in a row. The identity provider is ` +
          'accepting you and this installation is refusing the token it issues, so another login ' +
          'would be the same round trip.',
      );
      return;
    }

    // Counted only when a login starts. A refusal after the loop stopped — another panel's, or a
    // reload of the stopped page — is the same refusal again, and counting it would make the
    // message claim sign-ins that never happened.
    this.writeReauthAttempts({ count: attempts, first: previous?.first ?? now, refusedBy });
    this.reauthenticating = true;
    // Drop the dead token first, or the guard on the way back still sees a stored one.
    this.oauth.logOut(true);
    this.oauth.initCodeFlow(this.currentPath());
  }

  /**
   * A first-party call under `answeredBy` answered — the one honest evidence that a login loop is
   * over, and only from the plane that refused. The two planes validate tokens separately, so the
   * one that accepts the token answers on every pass while the other keeps refusing it; anything
   * the console can check about itself was just as true on every pass through the loop. After a
   * failed renewal no plane refused, and any answer shows the new login worked.
   */
  noteFirstPartySuccess(answeredBy: string): void {
    const recorded = this.readReauthAttempts();
    if (recorded && (recorded.refusedBy === null || recorded.refusedBy === answeredBy)) {
      this.clearReauthAttempts();
    }
  }

  /**
   * Leave properly: end the session at the identity provider, not just here.
   *
   * The only way out of the loop: Keycloak's SSO session is the half that keeps saying yes, so
   * clearing tokens locally signs the reader straight back in. `logOut()` with no `true` is the
   * RP-initiated logout.
   */
  signOutCompletely(): void {
    this.clearReauthAttempts();
    this.loginLoop.set(null);
    // `client_id`, because each pass through the loop removed the id token that would otherwise
    // identify us, and Keycloak refuses a `post_logout_redirect_uri` without one of the two.
    this.oauth.logOut({ client_id: authConfig.clientId ?? '' });
  }

  /** The attempts recorded inside the current window, or `null` when there are none. */
  private attemptsWithinWindow(now: number): ReauthAttempts | null {
    const recorded = this.readReauthAttempts();
    return recorded !== null && now - recorded.first < LOOP_WINDOW_MS ? recorded : null;
  }

  private writeReauthAttempts(record: ReauthAttempts): void {
    try {
      window.sessionStorage?.setItem(LOOP_KEY, JSON.stringify(record));
    } catch {
      // Storage can be unavailable (a private window, a policy); the in-memory guard still holds.
    }
  }

  private readReauthAttempts(): ReauthAttempts | null {
    try {
      const raw = window.sessionStorage?.getItem(LOOP_KEY);
      if (!raw) return null;
      const parsed: unknown = JSON.parse(raw);
      if (typeof parsed === 'object' && parsed !== null) {
        const { count, first, refusedBy } = parsed as Partial<
          Record<keyof ReauthAttempts, unknown>
        >;
        if (
          typeof count === 'number' &&
          typeof first === 'number' &&
          (refusedBy === null || typeof refusedBy === 'string')
        ) {
          return { count, first, refusedBy };
        }
      }
    } catch {
      // Unreadable or not ours: treated as no attempts, erring towards letting a login happen —
      // the failure this prevents is a storm, not a single redirect.
    }
    return null;
  }

  private clearReauthAttempts(): void {
    try {
      window.sessionStorage?.removeItem(LOOP_KEY);
    } catch {
      // Storage unavailable; nothing to clear.
    }
  }

  /**
   * Put the reader back where the session ended, from the `state` the login round trip returns.
   *
   * `replaceState` rather than a router navigation: this runs in an app initialiser, before the
   * router exists. Only a same-origin path is honoured — `state` survives a trip through the
   * browser, so treating it as a destination would be an open redirect. And a `state` that does not
   * decode is ignored like any other unusable destination: it comes from whatever link was opened,
   * and a decode that throws here rejects the initialiser and leaves a blank page.
   */
  private restoreLocation(): void {
    const stored = this.oauth.state;
    if (!stored || typeof window === 'undefined') return;
    // Resolved by the browser's own parser rather than pattern-matched: `/\evil.example` is not
    // `//…` and still resolves to another origin. Whatever `replaceState` would resolve is what is
    // compared, so a spelling nobody listed is refused by construction.
    let resolved: URL;
    try {
      const path = decodeURIComponent(stored);
      if (!path.startsWith('/')) return;
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

  /** Where to come back to, so a session that ends mid-task does not cost the reader their place. */
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
