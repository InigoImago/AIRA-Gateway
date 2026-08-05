import { HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { AuthService } from './auth.service';

/**
 * Attach the bearer token to AIRA's own API calls.
 *
 * Scoped to the two first-party prefixes on purpose (`/api` = management, `/gw` = gateway):
 * an interceptor that attached the token to *every* request would hand it to any third-party
 * host the app ever talks to (ADR-0007).
 */
const AIRA_PREFIXES = ['/api/', '/gw/'];

export const authInterceptor: HttpInterceptorFn = (req, next) => {
  if (AIRA_PREFIXES.some((prefix) => req.url.startsWith(prefix))) {
    const token = inject(AuthService).accessToken;
    if (token) {
      req = req.clone({ setHeaders: { Authorization: `Bearer ${token}` } });
    }
  }
  return next(req);
};
