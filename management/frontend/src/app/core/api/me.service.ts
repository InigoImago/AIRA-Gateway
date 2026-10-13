import { HttpClient } from '@angular/common/http';
import { Injectable, inject, signal } from '@angular/core';
import { Observable, shareReplay, tap } from 'rxjs';
import { Me } from './models';
import { API } from './prefixes';

/**
 * The unit after a money label — `Spend (EUR)` — or nothing before `/v1/me` has answered. A label
 * never carries a symbol of its own: a hard-coded `$` beside figures in `AIRA_CURRENCY` is a
 * figure in the wrong currency.
 */
export function unitSuffix(currency: string): string {
  return currency ? ` (${currency})` : '';
}

/** How a price per million tokens is headed: `EUR / 1M`, or `/ 1M` without a unit. */
export function perMillion(currency: string): string {
  return currency ? `${currency} / 1M` : '/ 1M';
}

/**
 * Who is signed in and what they may do (`/v1/me`), asked **once** and shared.
 *
 * The shell asks at start-up and every screen reads the same answer: screens that each fetched
 * their own could disagree on one page about what the reader may do, the moment one request failed
 * and another did not. A failure is not kept — the next caller asks again.
 */
@Injectable({ providedIn: 'root' })
export class MeService {
  private readonly http = inject(HttpClient);

  /** The account as `/v1/me` answered, or `null` before it has. */
  readonly me = signal<Me | null>(null);

  /** The request in flight, shared by everybody who asks before it answers. */
  private pending: Observable<Me> | null = null;

  /**
   * The unit every money figure on this console is in, from the last `/v1/me` (`AIRA_CURRENCY`).
   *
   * One signal here rather than a copy per screen: the CSV export labels the same numbers with this
   * setting, and a second definition is how a screen comes to say one currency and the file
   * another. Empty until the first response, and screens then show no unit rather than a guess.
   * The shell fetches `/v1/me` at start-up, so no screen has to fetch for itself.
   */
  readonly currency = signal('');

  /**
   * The answer, from the one shared request: replayed to whoever asks after it arrived, and asked
   * again by whoever asks after it failed — `shareReplay` resets on an error and keeps a success.
   */
  get(): Observable<Me> {
    this.pending ??= this.http.get<Me>(`${API}/v1/me`).pipe(
      tap((me) => {
        this.me.set(me);
        if (me.currency) {
          this.currency.set(me.currency);
        }
      }),
      shareReplay({ bufferSize: 1, refCount: false }),
    );
    return this.pending;
  }

  /** Ask again, for something that changed what `/me` says. */
  reload(): Observable<Me> {
    this.me.set(null);
    this.pending = null;
    return this.get();
  }
}
