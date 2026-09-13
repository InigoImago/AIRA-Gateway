import { AuthConfig } from 'angular-oauth2-oidc';

/**
 * What a deployment may set without rebuilding the bundle (`public/runtime-config.js`), so one
 * build serves every environment.
 */
interface RuntimeConfig {
  issuer?: string;
  clientId?: string;
}

/**
 * Read at the point of use, not at import: the bundle loads before anything can arrange for
 * `runtime-config.js` to be there, and a module-level read would fix the console's identity then.
 */
function runtimeConfig(): RuntimeConfig {
  return (window as unknown as { __AIRA_CONFIG__?: RuntimeConfig }).__AIRA_CONFIG__ ?? {};
}

/**
 * OIDC client configuration (ADR-0007).
 *
 * `requireHttps: 'remoteOnly'` keeps localhost development on plain HTTP while refusing the code
 * flow against a remote issuer over HTTP. PKCE stays on (the library's default for
 * `responseType: 'code'`) and the discovery document is validated strictly against the issuer.
 */
export const authConfig: AuthConfig = {
  /**
   * **Empty, never a guess.** A fallback address would send every user of a deployment whose
   * runtime config failed to load to a login page on their own machine. Empty rather than thrown:
   * `AuthService.init` reports it through `startupError` like every other startup failure.
   */
  get issuer(): string {
    return runtimeConfig().issuer ?? '';
  },
  redirectUri: window.location.origin + '/',
  postLogoutRedirectUri: window.location.origin + '/',
  get clientId(): string {
    return runtimeConfig().clientId ?? 'aira-gateway';
  },
  responseType: 'code',
  // Not `offline_access`: this realm refuses offline tokens (the exchange then fails as a CORS
  // error), the code flow already returns a refresh token, and a governance console should not
  // hold a credential that outlives the SSO session.
  scope: 'openid profile email',
  requireHttps: 'remoteOnly',
  strictDiscoveryDocumentValidation: true,
  disablePKCE: false,
  // Drop the authorization code from the address bar so it does not linger in history, bookmarks
  // or a copied URL.
  clearHashAfterLogin: true,
  showDebugInformation: false,

  // Renew ahead of expiry: a token refreshed the instant it dies still loses the requests in flight.
  timeoutFactor: 0.75,
  // A silent iframe login is the fallback for a realm that returns no refresh token.
  silentRefreshRedirectUri: window.location.origin + '/silent-refresh.html',
  useSilentRefresh: true,
  sessionChecksEnabled: false,
};
