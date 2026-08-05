import { TestBed } from '@angular/core/testing';
import { OAuthService } from 'angular-oauth2-oidc';
import { AuthService } from './auth.service';

function setup(overrides: Partial<Record<string, unknown>> = {}) {
  TestBed.resetTestingModule();
  const calls: string[] = [];
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
    ...overrides,
  };
  TestBed.configureTestingModule({
    providers: [{ provide: OAuthService, useValue: oauth }, AuthService],
  });
  return { service: TestBed.inject(AuthService), calls };
}

describe('AuthService', () => {
  it('configures the client and picks up an existing session on init', async () => {
    const { service, calls } = setup();
    await service.init();
    expect(calls).toEqual(['configure', 'discovery']);
    expect(service.authenticated()).toBe(true);
    expect(service.isAuthenticated()).toBe(true);
  });

  it('reports an unauthenticated session after init', async () => {
    const { service } = setup({ hasValidAccessToken: () => false });
    await service.init();
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
