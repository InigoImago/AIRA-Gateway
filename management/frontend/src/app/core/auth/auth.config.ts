import { AuthConfig } from 'angular-oauth2-oidc';

export const authConfig: AuthConfig = {
  issuer: 'http://localhost:8080/realms/aira',
  redirectUri: window.location.origin + '/',
  clientId: 'aira-gateway',
  responseType: 'code',
  scope: 'openid profile email',
  requireHttps: false,
  showDebugInformation: false,
};
