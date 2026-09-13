import { Observable } from 'rxjs';
import { GW } from '../prefixes';
import { Register, Report } from '../types/reporting';
import { ApiClientClass } from './base';

/**
 * Spend reports and the register of processing activities, from the gateway.
 *
 * What a caller sees is decided by their token, by one function on the gateway; nothing here can
 * widen it. Windows are half-open — `to` is excluded — so adjacent periods never share a request.
 * CSV comes back as a blob because the endpoints need the bearer token, which an `<a href>` does
 * not carry (`FRD-602`).
 */
export function withReporting<T extends ApiClientClass>(Base: T) {
  return class extends Base {
    /** Spend and usage over a window (FRD-601). */
    report(from: string, to: string): Observable<Report> {
      return this.http.get<Report>(`${GW}/v1beta/reporting`, { params: { from, to } });
    }

    /**
     * The same report, narrowed to one use case (FRD-603).
     *
     * The **same endpoint** on purpose: an endpoint of its own would be a second place to decide
     * visibility. The parameter can only intersect with what the token already allows.
     */
    useCaseReport(slug: string, from: string, to: string): Observable<Report> {
      return this.http.get<Report>(`${GW}/v1beta/reporting`, {
        params: { from, to, use_case: slug },
      });
    }

    /** The same report as a spreadsheet (FRD-602). */
    reportCsv(from: string, to: string, breakdown: string): Observable<Blob> {
      return this.http.get(`${GW}/v1beta/reporting`, {
        params: { from, to, breakdown },
        headers: { Accept: 'text/csv' },
        responseType: 'blob',
      });
    }

    /** The register of processing activities (`FRD-608`), one row per use case. */
    register(from: string, to: string): Observable<Register> {
      return this.http.get<Register>(`${GW}/v1beta/register`, { params: { from, to } });
    }

    /** The same register as a spreadsheet (`FRD-608` §2.2). */
    registerCsv(from: string, to: string): Observable<Blob> {
      return this.http.get(`${GW}/v1beta/register`, {
        params: { from, to },
        headers: { Accept: 'text/csv' },
        responseType: 'blob',
      });
    }
  };
}
