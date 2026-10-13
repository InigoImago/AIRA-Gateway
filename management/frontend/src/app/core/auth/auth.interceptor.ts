import { HttpErrorResponse, HttpInterceptorFn, HttpResponse } from '@angular/common/http';
import { inject } from '@angular/core';
import { catchError, tap, throwError } from 'rxjs';
import { AIRA_PREFIXES } from '../api/prefixes';
import { AuthService } from './auth.service';

/**
 * Attach the bearer token to AIRA's own API calls, and treat a 401 as "log in again".
 *
 * Only the first-party prefixes get the token (`ADR-0007`), and which those are is `prefixes.json`'s
 * to say, not this file's: a prefix the services use and this list lacks sends requests without a
 * token, and the 401 that comes back would log a valid session out.
 */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const plane = AIRA_PREFIXES.find((prefix) => req.url.startsWith(prefix));
  if (!plane) {
    return next(req);
  }

  const auth = inject(AuthService);
  const token = auth.accessToken;
  if (token) {
    req = req.clone({ setHeaders: { Authorization: `Bearer ${token}` } });
  }

  return next(req).pipe(
    // A call that answered ends a login loop that this plane's refusal started
    // (`AuthService.reauthenticate`) — the plane is named, because the other one answering says
    // nothing about this one.
    tap((event) => {
      if (event instanceof HttpResponse) {
        auth.noteFirstPartySuccess(plane);
      }
    }),
    catchError((error: unknown) => {
      // A 401 means this session is over; the login says so, where an error on every screen would
      // read as the backend rejecting the user. Not 403: that is a real answer about a real
      // permission, from somebody signed in perfectly well.
      if (error instanceof HttpErrorResponse && error.status === 401) {
        auth.reauthenticate(plane);
      }
      return throwError(() => error);
    }),
  );
};
