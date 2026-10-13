import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { inject } from '@angular/core';
import { MonoTypeOperatorFunction, catchError, from, switchMap, throwError } from 'rxjs';
import { API } from '../prefixes';

/**
 * Encode a value interpolated into a URL. Slugs, usernames and key prefixes come from user input,
 * and an unencoded `/` or `..` would silently retarget the request at another endpoint (ADR-0007).
 */
export const seg = (value: string): string => encodeURIComponent(value);

/** What every resource client shares: the HTTP client, and the use-case collection URL. */
export class ApiClient {
  protected readonly http = inject(HttpClient);
  protected readonly base = `${API}/v1/use-cases/`;
}

/**
 * For a request whose answer is a file: a refusal arrives as a `Blob` too, like the file would
 * have, and `errorMessage` cannot read the server's reason out of one. Read back into JSON here, so
 * every caller of a download reports the gateway's words rather than its own fallback.
 */
export function readableErrors<T>(): MonoTypeOperatorFunction<T> {
  return catchError((response: unknown) => {
    if (!(response instanceof HttpErrorResponse) || !(response.error instanceof Blob)) {
      return throwError(() => response);
    }
    return from(response.error.text()).pipe(
      switchMap((text) => {
        let error: unknown = text;
        try {
          error = JSON.parse(text);
        } catch {
          // Not JSON: the text itself is what the server said.
        }
        return throwError(
          () =>
            new HttpErrorResponse({
              error,
              headers: response.headers,
              status: response.status,
              statusText: response.statusText,
              url: response.url ?? undefined,
            }),
        );
      }),
    );
  });
}

/** A class a resource mixin can extend. TypeScript requires a mixin base to take `any[]`. */
export type ApiClientClass = new (...args: any[]) => ApiClient;
