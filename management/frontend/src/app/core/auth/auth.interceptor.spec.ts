import { HttpErrorResponse, HttpHandlerFn, HttpRequest } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';
import { authInterceptor } from './auth.interceptor';
import { AuthService } from './auth.service';

function runInterceptor(url: string, token: string): HttpRequest<unknown> {
  TestBed.configureTestingModule({
    providers: [{ provide: AuthService, useValue: { accessToken: token } }],
  });
  const request = new HttpRequest<unknown>('GET', url);
  let captured = request;
  const next: HttpHandlerFn = (req) => {
    captured = req;
    return of();
  };
  TestBed.runInInjectionContext(() => authInterceptor(request, next).subscribe());
  return captured;
}

/** Run the interceptor against a failing handler, reporting whether it sent us back to the login. */
function runFailing(url: string, status: number) {
  TestBed.resetTestingModule();
  let reauthenticated = 0;
  TestBed.configureTestingModule({
    providers: [
      {
        provide: AuthService,
        useValue: { accessToken: 'tok', reauthenticate: () => (reauthenticated += 1) },
      },
    ],
  });
  const request = new HttpRequest<unknown>('GET', url);
  const next: HttpHandlerFn = () => throwError(() => new HttpErrorResponse({ status, url }));
  let seen: unknown = null;
  TestBed.runInInjectionContext(() =>
    authInterceptor(request, next).subscribe({ error: (e: unknown) => (seen = e) }),
  );
  return { reauthenticated, seen };
}

describe('authInterceptor', () => {
  it('adds the bearer token to /api requests', () => {
    expect(runInterceptor('/api/v1/me', 'tok').headers.get('Authorization')).toBe('Bearer tok');
  });

  it('adds the bearer token to /gw gateway requests', () => {
    expect(runInterceptor('/gw/v1beta/usage/demo-uc', 'tok').headers.get('Authorization')).toBe(
      'Bearer tok',
    );
  });

  it('leaves third-party requests untouched', () => {
    expect(
      runInterceptor('https://evil.example/collect', 'tok').headers.get('Authorization'),
    ).toBeNull();
  });

  it('leaves non-api requests untouched', () => {
    expect(runInterceptor('/assets/logo.png', 'tok').headers.get('Authorization')).toBeNull();
  });

  it('does not add a header when there is no token', () => {
    expect(runInterceptor('/api/v1/me', '').headers.get('Authorization')).toBeNull();
  });

  it('sends an expired session to the login instead of reporting invalid credentials', () => {
    // A 401 on a first-party call means the session is over — the token expired while the tab sat
    // open, or Keycloak was restarted and took the session with it. Reported as an error it read
    // as "the backend is rejecting you", which in a console whose purpose is evidence makes
    // somebody doubt the *figures* rather than the session.
    const { reauthenticated, seen } = runFailing('/api/v1/use-cases/', 401);

    expect(reauthenticated).toBe(1);
    // Still rethrown: the caller's own error handling decides what to render in the moment
    // before the browser leaves for Keycloak.
    expect(seen).toBeInstanceOf(HttpErrorResponse);
  });

  it('leaves a 403 alone, because that is a real answer about a real permission', () => {
    // Logging somebody out over a permission boundary would hide the boundary behind a login
    // screen — and they are signed in perfectly well.
    expect(runFailing('/api/v1/use-cases/x/members/', 403).reauthenticated).toBe(0);
  });

  it('does not react to a third party answering 401', () => {
    expect(runFailing('https://evil.example/collect', 401).reauthenticated).toBe(0);
  });
});
