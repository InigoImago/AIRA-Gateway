import { HttpErrorResponse, HttpHandlerFn, HttpRequest, HttpResponse } from '@angular/common/http';
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

/** Run the interceptor against a failing handler, reporting which planes it said refused. */
function runFailing(url: string, status: number) {
  TestBed.resetTestingModule();
  const refusedBy: (string | null)[] = [];
  TestBed.configureTestingModule({
    providers: [
      {
        provide: AuthService,
        useValue: {
          accessToken: 'tok',
          reauthenticate: (plane: string | null) => refusedBy.push(plane),
        },
      },
    ],
  });
  const request = new HttpRequest<unknown>('GET', url);
  const next: HttpHandlerFn = () => throwError(() => new HttpErrorResponse({ status, url }));
  let seen: unknown = null;
  TestBed.runInInjectionContext(() =>
    authInterceptor(request, next).subscribe({ error: (e: unknown) => (seen = e) }),
  );
  return { reauthenticated: refusedBy.length, refusedBy, seen };
}

/**
 * Run the interceptor against a handler that answers, reporting which planes it said answered.
 *
 * The handler emits a real `HttpResponse`: a stand-in that emits nothing never reaches the branch
 * this is about, and that branch is what ends a login loop.
 */
function runAnswering(url: string): string[] {
  TestBed.resetTestingModule();
  const answeredBy: string[] = [];
  TestBed.configureTestingModule({
    providers: [
      {
        provide: AuthService,
        useValue: {
          accessToken: 'tok',
          noteFirstPartySuccess: (plane: string) => answeredBy.push(plane),
        },
      },
    ],
  });
  const request = new HttpRequest<unknown>('GET', url);
  const next: HttpHandlerFn = () => of(new HttpResponse({ status: 200, url, body: {} }));
  TestBed.runInInjectionContext(() => authInterceptor(request, next).subscribe());
  return answeredBy;
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
    // A 401 on a first-party call means the session is over. Reported as an error it would read as
    // "the backend is rejecting you", which makes somebody doubt the figures rather than the session.
    const { reauthenticated, seen } = runFailing('/api/v1/use-cases/', 401);

    expect(reauthenticated).toBe(1);
    // Still rethrown: the caller's own error handling decides what to render in the moment
    // before the browser leaves for Keycloak.
    expect(seen).toBeInstanceOf(HttpErrorResponse);
  });

  it('names the plane that refused, so only its answer can end a login loop', () => {
    expect(runFailing('/api/v1/use-cases/', 401).refusedBy).toEqual(['/api/']);
    expect(runFailing('/gw/v1beta/reports', 401).refusedBy).toEqual(['/gw/']);
  });

  it('leaves a 403 alone, because that is a real answer about a real permission', () => {
    // Logging somebody out over a permission boundary would hide the boundary behind a login
    // screen — and they are signed in perfectly well.
    expect(runFailing('/api/v1/use-cases/x/members/', 403).reauthenticated).toBe(0);
  });

  it('does not react to a third party answering 401', () => {
    expect(runFailing('https://evil.example/collect', 401).reauthenticated).toBe(0);
  });

  it('reports an answer from either plane as that plane answering', () => {
    expect(runAnswering('/api/v1/me')).toEqual(['/api/']);
    expect(runAnswering('/gw/v1beta/usage/demo-uc')).toEqual(['/gw/']);
  });

  it('does not take a third party answering for the API answering', () => {
    // Somebody else's server answering says nothing about whether AIRA accepts the token.
    expect(runAnswering('https://evil.example/collect')).toEqual([]);
    expect(runAnswering('/assets/logo.png')).toEqual([]);
  });
});
