import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { PrivacyAcknowledgement, PrivacyNotice } from '../api/models';
import { API } from '../api/prefixes';

/** The privacy notice and its acknowledgement (`FRD-625`). */
@Injectable({ providedIn: 'root' })
export class PrivacyNoticeService {
  private readonly http = inject(HttpClient);
  private readonly url = `${API}/v1/privacy-notice`;

  /**
   * The notice in `language`, or — without one — in the language the browser's `Accept-Language`
   * asks for, which the server negotiates. The console keeps no language preference of its own:
   * anything it stored in the browser would be one more thing the notice has to declare.
   */
  get(language?: string): Observable<PrivacyNotice> {
    const params = language ? new HttpParams().set('language', language) : undefined;
    return this.http.get<PrivacyNotice>(this.url, { params });
  }

  /** Records that the reader acknowledged `version`; a changed notice answers 409. */
  acknowledge(version: string, language: string): Observable<PrivacyAcknowledgement> {
    return this.http.post<PrivacyAcknowledgement>(`${this.url}/acknowledgements`, {
      version,
      language,
    });
  }
}
