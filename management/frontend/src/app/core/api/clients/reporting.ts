import { Observable } from 'rxjs';
import { GW } from '../prefixes';
import { Granularity, Register, Report, SeriesSplit } from '../types/reporting';
import { ApiClientClass } from './base';

/**
 * What a caller asks of the time series (`FRD-626`): how wide a bucket is, and what the bands are.
 *
 * `'auto'` lets the window decide — hours for a day or two, days beyond that — so a caller who has
 * no opinion does not have to invent one.
 */
export interface SeriesRequest {
  granularity: Granularity | 'auto';
  split: SeriesSplit;
}

/**
 * The two query parameters a series needs, or **none at all** when none was asked for.
 *
 * Omitted rather than sent empty: `series=''` is how the gateway spells "no series", and a report
 * loaded only for its figures should not pay for a query no screen reads.
 */
function seriesParams(series?: SeriesRequest): Record<string, string> {
  return series ? { series: series.granularity, split: series.split } : {};
}

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
     *
     * `person` narrows it further, to one person's own traffic (`FRD-626` FR-15) — what a member
     * opening their own use case asks about. Omitted rather than sent empty, because the gateway
     * reads an absent filter and an empty one the same way and a client should not rely on that.
     */
    useCaseReport(
      slug: string,
      from: string,
      to: string,
      series?: SeriesRequest,
      person?: string,
    ): Observable<Report> {
      return this.http.get<Report>(`${GW}/v1beta/reporting`, {
        params: {
          from,
          to,
          use_case: slug,
          ...seriesParams(series),
          ...(person ? { person } : {}),
        },
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
