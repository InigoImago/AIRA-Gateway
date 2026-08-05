import { AuthConfig } from 'angular-oauth2-oidc';

/**
 * OIDC client configuration (ADR-0007).
 *
 * `requireHttps: 'remoteOnly'` keeps localhost development on plain HTTP while refusing to run
 * the code flow against a remote issuer over HTTP — an authorization code or token must never
 * cross the network in the clear. PKCE stays on (the library's default for `responseType:
 * 'code'`) and the discovery document is validated strictly against the issuer.
 */
export const authConfig: AuthConfig = {
  issuer: 'http://localhost:8080/realms/aira',
  redirectUri: window.location.origin + '/',
  postLogoutRedirectUri: window.location.origin + '/',
  clientId: 'aira-gateway',
  responseType: 'code',
  scope: 'openid profile email',
  requireHttps: 'remoteOnly',
  strictDiscoveryDocumentValidation: true,
  disablePKCE: false,
  // Drop the authorization code fragment from the address bar after login so it does not linger
  // in history, bookmarks, or a copied URL.
  clearHashAfterLogin: true,
  showDebugInformation: false,
};
