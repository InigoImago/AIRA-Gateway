import { HttpClient } from '@angular/common/http';
import { inject } from '@angular/core';
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

/** A class a resource mixin can extend. TypeScript requires a mixin base to take `any[]`. */
export type ApiClientClass = new (...args: any[]) => ApiClient;
