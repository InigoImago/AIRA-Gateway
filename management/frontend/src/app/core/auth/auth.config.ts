import { AuthConfig } from 'angular-oauth2-oidc';

/**
 * What a deployment may set without rebuilding the bundle (`public/runtime-config.js`).
 *
 * The issuer used to be a compiled-in constant, which meant one build per environment and, in
 * practice, one published build pointing at whichever Keycloak the person who ran the build had
 * in mind. A misdirected console does not fail — it sends people to a real login page at the
 * wrong realm.
 *
 * The fallbacks are the local Compose stack, so a laptop and the unit tests need no file at all.
 */
interface RuntimeConfig {
  issuer?: string;
  clientId?: string;
}

/**
 * **Empty, never a guess.**
 *
 * This read `?? 'http://localhost:8080/realms/aira'` — the failure the runtime config exists to
 * prevent, one layer down: a deployment whose `runtime-config.js` did not load would silently send
 * every user to a login page on whatever machine their browser sits at, and the error they
 * eventually see names neither the realm nor the reason.
 *
 * Empty rather than a thrown error, because this is a module-level constant: throwing here fails
 * the *import*, which took four test suites down with it and would take the whole console down
 * before the shell that could explain it exists. `AuthService.init` reports it through the same
 * `startupError` every other startup failure uses.
 */

/**
 * Read at the point of use, not at import.
 *
 * A module-level read is evaluated when the bundle loads, before anything can have arranged for
 * `runtime-config.js` to be there — which made this untestable without a global setup file and,
 * worse, fixed the console's identity at load time. A function is read when somebody asks.
 */
function runtimeConfig(): RuntimeConfig {
  return (window as unknown as { __AIRA_CONFIG__?: RuntimeConfig }).__AIRA_CONFIG__ ?? {};
}

/**
 * OIDC client configuration (ADR-0007).
 *
 * `requireHttps: 'remoteOnly'` keeps localhost development on plain HTTP while refusing to run
 * the code flow against a remote issuer over HTTP — an authorization code or token must never
 * cross the network in the clear. PKCE stays on (the library's default for `responseType:
 * 'code'`) and the discovery document is validated strictly against the issuer.
 */
export const authConfig: AuthConfig = {
  get issuer(): string {
    return runtimeConfig().issuer ?? '';
  },
  redirectUri: window.location.origin + '/',
  postLogoutRedirectUri: window.location.origin + '/',
  get clientId(): string {
    return runtimeConfig().clientId ?? 'aira-gateway';
  },
  responseType: 'code',
  // Deliberately **not** `offline_access`.
  //
  // It was added here to get a refresh token, and it broke login outright: this realm does not
  // permit offline tokens, so the code-to-token exchange came back `not_allowed` and the console
  // never rendered at all. Keycloak answers that particular failure without CORS headers, so the
  // browser reported it as a CORS/network error — a message that names neither the scope nor the
  // realm setting that caused it.
  //
  // It was also the wrong instrument. The authorization-code flow already returns a refresh
  // token; `offline_access` asks for an *offline* one, which keeps working after the user's SSO
  // session has ended. A governance console holding a credential that outlives the session is a
  // worse answer than the problem it was solving.
  scope: 'openid profile email',
  requireHttps: 'remoteOnly',
  strictDiscoveryDocumentValidation: true,
  disablePKCE: false,
  // Drop the authorization code fragment from the address bar after login so it does not linger
  // in history, bookmarks, or a copied URL.
  clearHashAfterLogin: true,
  showDebugInformation: false,

  // Renew ahead of expiry rather than at it. A token that is refreshed the instant it dies still
  // loses every request already in flight, and a governance console is exactly the place where a
  // spurious authentication error makes somebody doubt the data rather than the session.
  timeoutFactor: 0.75,
  // A silent iframe login is the fallback when no refresh token came back — a realm can be
  // configured either way, and a console that only works under one of them is a console that
  // breaks on somebody else's Keycloak.
  silentRefreshRedirectUri: window.location.origin + '/silent-refresh.html',
  useSilentRefresh: true,
  sessionChecksEnabled: false,
};
