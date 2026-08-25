import { HttpClient } from '@angular/common/http';
import { Injectable, inject, signal } from '@angular/core';
import { Observable, tap } from 'rxjs';
import { Me } from './models';
import { API } from './prefixes';

@Injectable({ providedIn: 'root' })
export class MeService {
  private readonly http = inject(HttpClient);

  /**
   * The unit every money figure on this console is in, remembered from the last `/v1/me`.
   *
   * **A signal on the service rather than a field on each screen**, because the defect this fixes
   * was a second definition: three screens said *"US dollars"* in so many words — the model
   * catalog, the use-case budget window and the installation's — while `AIRA_CURRENCY` labelled
   * the very same numbers in every CSV export. Somebody typed dollars into a form and got a file
   * that said euros. Copying the fact into three components would be that mistake again with a
   * better source.
   *
   * Empty until the first response. Screens render **nothing** rather than a guess: an unlabelled
   * amount is a reader who asks, and a wrongly labelled one is a reader who does not.
   *
   * The shell fetches `/v1/me` once on start-up, so by the time any of these screens is reachable
   * this is filled — no screen has to fetch for itself, which is the rule about pages and panels
   * one layer out (`CLAUDE.md` §3).
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
