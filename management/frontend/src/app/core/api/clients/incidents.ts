import { Observable } from 'rxjs';
import { API, GW } from '../prefixes';
import {
  AnomalyPage,
  AnomalyRule,
  ContentReadPage,
  Suspension,
  TracePage,
  TracePayload,
} from '../types/incidents';
import { ApiClientClass, seg } from './base';

/** Anomaly findings and rules, suspensions, and request traces (`FRD-500`–`FRD-505`). */
export function withIncidents<T extends ApiClientClass>(Base: T) {
  return class extends Base {
    /**
     * What the detector has found (`FRD-501` FR-8), scoped by the caller's token — an empty list,
     * never a refusal, for somebody who may see none of it.
     */
    anomalies(limit = 50, useCase?: string, cursor?: string): Observable<AnomalyPage> {
      const params: Record<string, string | number> = { limit };
      // A cursor, not an offset: findings are an append-only log.
      if (cursor) params['cursor'] = cursor;
      // Filtered at the server: filtering the newest hundred in the browser would show a quiet use
      // case nothing on a busy installation.
      if (useCase) params['use_case'] = useCase;
      return this.http.get<AnomalyPage>(`${GW}/v1beta/anomalies`, { params });
    }

    /** What actually happened, request by request (`FRD-502`). Metadata only, paged by cursor. */
    traces(options: {
      useCase?: string;
      outcome?: string;
      refusalsOnly?: boolean;
      /** The three an incident starts with: which system, whose identity, which machine. */
      credential?: string;
      subject?: string;
      sourceIp?: string;
      /** Only my own requests — offered to every role, including those that see everything. */
      mine?: boolean;
      /** Only the turns where the model asked for a function. */
      toolsOnly?: boolean;
      /** Only the requests a pipeline step objected to (`FRD-505` FR-5). */
      flaggedOnly?: boolean;
      /** One request by its id — where the content-read log links to (`FRD-622` FR-6). */
      requestId?: string;
      cursor?: string;
      limit?: number;
    }): Observable<TracePage> {
      const params: Record<string, string | number | boolean> = { limit: options.limit ?? 50 };
      if (options.useCase) params['use_case'] = options.useCase;
      if (options.outcome) params['outcome'] = options.outcome;
      if (options.refusalsOnly) params['refusals_only'] = true;
      if (options.credential) params['credential'] = options.credential;
      if (options.subject) params['subject'] = options.subject;
      if (options.sourceIp) params['source_ip'] = options.sourceIp;
      if (options.mine) params['mine'] = true;
      if (options.toolsOnly) params['tools_only'] = true;
      if (options.flaggedOnly) params['flagged_only'] = true;
      if (options.requestId) params['request_id'] = options.requestId;
      if (options.cursor) params['cursor'] = options.cursor;
      return this.http.get<TracePage>(`${GW}/v1beta/traces`, { params });
    }

    /**
     * The prompt and the answer for one request — and, on the server, a record that it was read.
     *
     * A second call rather than a field on the row: content has a different audience from
     * metadata, and a list carrying payloads would disclose them to everybody who may see the list.
     */
    tracePayload(id: string): Observable<TracePayload> {
      return this.http.get<TracePayload>(`${GW}/v1beta/traces/${seg(id)}/payload`);
    }

    /** Who read which stored content, for the platform roles (`FRD-622` FR-4). Paged by cursor. */
    contentReads(options: {
      useCase?: string;
      reader?: string;
      cursor?: string;
      limit?: number;
    }): Observable<ContentReadPage> {
      const params: Record<string, string | number> = { limit: options.limit ?? 50 };
      if (options.useCase) params['use_case'] = options.useCase;
      if (options.reader) params['reader'] = options.reader;
      if (options.cursor) params['cursor'] = options.cursor;
      return this.http.get<ContentReadPage>(`${GW}/v1beta/content-reads`, { params });
    }

    /** Traffic that is currently stopped, and what was stopped before (`FRD-503`). */
    suspensions(): Observable<{ suspensions: Suspension[] }> {
      return this.http.get<{ suspensions: Suspension[] }>(`${GW}/v1beta/suspensions`);
    }

    /** Stop a subject, a credential or a use case. Needs an incident role; the server decides. */
    suspend(body: {
      target: string;
      target_value: string;
      action?: string;
      throttle_rpm?: number | null;
      minutes?: number | null;
      reason?: string;
      use_case?: string | null;
    }): Observable<Suspension> {
      return this.http.post<Suspension>(`${GW}/v1beta/suspensions`, body);
    }

    /** Lift one. The row is kept and stamped, never deleted. */
    liftSuspension(id: string): Observable<Suspension> {
      return this.http.delete<Suspension>(`${GW}/v1beta/suspensions/${seg(id)}`);
    }

    /** The anomaly rules of one use case. Members read; whoever manages it writes. */
    useCaseRules(slug: string): Observable<AnomalyRule[]> {
      return this.http.get<AnomalyRule[]>(`${this.base}${seg(slug)}/anomaly-rules/`);
    }

    /** Create or replace a rule on one use case. The server upserts **by name**, so renaming one
     *  would create a second and leave the first watching. */
    saveUseCaseRule(slug: string, rule: Partial<AnomalyRule>): Observable<AnomalyRule> {
      return this.http.post<AnomalyRule>(`${this.base}${seg(slug)}/anomaly-rules/`, rule);
    }

    deleteUseCaseRule(slug: string, id: number): Observable<void> {
      return this.http.delete<void>(`${this.base}${seg(slug)}/anomaly-rules/${id}`);
    }

    /** Anomaly rules that apply everywhere, plus the ones on use cases the caller may see. */
    globalRules(): Observable<AnomalyRule[]> {
      return this.http.get<AnomalyRule[]>(`${API}/v1/anomaly-rules/`);
    }

    /** Author a rule that applies to **every** use case (`FRD-500`). */
    createGlobalRule(rule: Partial<AnomalyRule>): Observable<AnomalyRule> {
      return this.http.post<AnomalyRule>(`${API}/v1/anomaly-rules/`, rule);
    }

    /**
     * Change a rule. The server decides who may: a global rule needs an incident role, a use-case
     * rule needs to manage that use case (`AnomalyRuleViewSet._guard`).
     *
     * `PATCH`, not `PUT`: sending the whole object back would make every save a chance to overwrite
     * a field the form never showed.
     */
    updateRule(id: number, changes: Partial<AnomalyRule>): Observable<AnomalyRule> {
      return this.http.patch<AnomalyRule>(`${API}/v1/anomaly-rules/${id}/`, changes);
    }

    deleteRule(id: number): Observable<void> {
      return this.http.delete<void>(`${API}/v1/anomaly-rules/${id}/`);
    }
  };
}
