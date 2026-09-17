import { HttpClient } from '@angular/common/http';
import { Injectable, inject, signal } from '@angular/core';
import { Observable, tap } from 'rxjs';
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

@Injectable({ providedIn: 'root' })
export class MeService {
  private readonly http = inject(HttpClient);

  /**
   * The unit every money figure on this console is in, from the last `/v1/me` (`AIRA_CURRENCY`).
   *
   * One signal here rather than a copy per screen: the CSV export labels the same numbers with this
   * setting, and a second definition is how a screen comes to say one currency and the file
   * another. Empty until the first response, and screens then show no unit rather than a guess.
   * The shell fetches `/v1/me` at start-up, so no screen has to fetch for itself.
   */
  readonly currency = signal('');

  get(): Observable<Me> {
    return this.http.get<Me>(`${API}/v1/me`).pipe(
      tap((me) => {
        if (me.currency) {
          this.currency.set(me.currency);
        }
      }),
    );
  }
}
