import { HttpErrorResponse, HttpInterceptorFn, HttpResponse } from '@angular/common/http';
import { inject } from '@angular/core';
import { catchError, tap, throwError } from 'rxjs';
import { AIRA_PREFIXES } from '../api/prefixes';
import { AuthService } from './auth.service';

/**
 * Attach the bearer token to AIRA's own API calls, and treat a 401 as "log in again".
 *
 * Scoped to the first-party prefixes on purpose: an interceptor that attached the token to *every*
 * request would hand it to any third-party host the app ever talks to (`ADR-0007`).
 *
 * **Which prefixes those are is not decided here.** It used to be — a local `['/api/', '/gw/']`
 * beside fifty call sites that spelled the same two prefixes, an nginx template that routed them,
 * and a dev-proxy config that repeated both. A third prefix added to the services and forgotten
 * here sends the request without a token, and the `401` that comes back is handled below by
 * *logging the user out*: a valid session ended over a list nobody remembered to extend.
 */

export const authInterceptor: HttpInterceptorFn = (req, next) => {
  if (!AIRA_PREFIXES.some((prefix) => req.url.startsWith(prefix))) {
    return next(req);
  }

  const auth = inject(AuthService);
  const token = auth.accessToken;
  if (token) {
    req = req.clone({ setHeaders: { Authorization: `Bearer ${token}` } });
  }

  return next(req).pipe(
    // A first-party call that answered is the end of any login loop that was running — and the
    // only evidence worth trusting for it. Everything the console can check about *itself* was
    // just as true on every pass through the loop (`AuthService.reauthenticate`).
    tap((event) => {
      if (event instanceof HttpResponse) {
        auth.noteFirstPartySuccess();
      }
    }),
    catchError((error: unknown) => {
      // A 401 on a first-party call means one thing: this session is over. The token expired
      // while the tab sat open, or Keycloak was restarted and the session went with it.
      //
      // Reporting it produced "invalid credentials" on every screen at once, which reads as the
      // *backend rejecting the user* — in a console whose whole purpose is evidence, that makes
      // somebody doubt the figures rather than the session. Sending them to the login says what
      // actually happened and is the only action available anyway.
      //
      // Deliberately **not** 403: that is a real answer about a real permission, and the caller
      // is signed in perfectly well. Logging them out over it would hide the boundary behind a
      // login screen.
      if (error instanceof HttpErrorResponse && error.status === 401) {
        auth.reauthenticate();
      }
      return throwError(() => error);
    }),
  );
};
