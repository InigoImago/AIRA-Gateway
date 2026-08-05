import { HttpHandlerFn, HttpRequest } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
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
});
