import { Observable, map } from 'rxjs';
import { API, GW } from '../prefixes';
import {
  TestAttribution,
  TestCase,
  TestModelStats,
  TestResult,
  TestRun,
} from '../types/smoke-tests';
import { ApiClientClass, seg } from './base';

/** Putting the question catalogue to a use case's pipeline (`FRD-504`, `ADR-0020`). */
export function withSmokeTests<T extends ApiClientClass>(Base: T) {
  return class extends Base {
    /** Which use cases this caller may put the catalogue to, and why not where they may not. */
    testAttribution(): Observable<TestAttribution[]> {
      return this.http.get<TestAttribution[]>(`${API}/v1/test-attribution/`);
    }

    /** The whole catalogue. A hundred rows, so it is fetched once and read in the browser. */
    testCases(): Observable<TestCase[]> {
      return this.http.get<TestCase[]>(`${API}/v1/test-cases/`);
    }

    /** Authoring the catalogue. IT Security only on the server; the console offers it to nobody
     *  else. */
    createCase(body: Partial<TestCase>): Observable<TestCase> {
      return this.http.post<TestCase>(`${API}/v1/test-cases/`, body);
    }

    updateCase(id: number, body: Partial<TestCase>): Observable<TestCase> {
      return this.http.patch<TestCase>(`${API}/v1/test-cases/${id}/`, body);
    }

    deleteCase(id: number): Observable<void> {
      return this.http.delete<void>(`${API}/v1/test-cases/${id}/`);
    }

    testRuns(useCase?: string): Observable<TestRun[]> {
      const params: Record<string, string> = {};
      if (useCase) params['use_case'] = useCase;
      return this.http.get<TestRun[]>(`${API}/v1/test-runs/`, { params });
    }

    /**
     * Start a run against a use case's pipeline, entered at `model` — picked by the person starting
     * it and bounded by the server to what is released to that use case.
     */
    startRun(useCase: string, model: string): Observable<TestRun> {
      return this.http.post<TestRun>(`${API}/v1/test-runs/`, { use_case: useCase, model });
    }

    runResults(runId: number): Observable<TestResult[]> {
      return this.http.get<TestResult[]>(`${API}/v1/test-runs/${runId}/results/`);
    }

    finishRun(runId: number): Observable<TestRun> {
      return this.http.post<TestRun>(`${API}/v1/test-runs/${runId}/finish/`, {});
    }

    /** Store an answer, a verdict, or both. The server keeps the two apart: writing a response does
     *  not stamp a rater, because nobody has read it yet. */
    updateResult(id: number, changes: Partial<TestResult>): Observable<TestResult> {
      return this.http.patch<TestResult>(`${API}/v1/test-results/${id}/`, changes);
    }

    /**
     * Put one prompt to one model through the **gateway**, the ordinary way (`FRD-504` §5): priced,
     * budgeted, rate-limited and audited like any other request, or it measures a path nobody uses.
     */
    askModel(model: string, prompt: string, useCase: string): Observable<string> {
      const path = useCase ? `${GW}/uc/${seg(useCase)}` : `${GW}`;
      return this.http
        .post<{ candidates?: { content?: { parts?: { text?: string }[] } }[] }>(
          `${path}/v1beta/models/${seg(model)}:generateContent`,
          { contents: [{ parts: [{ text: prompt }] }] },
        )
        .pipe(
          map((body) =>
            (body.candidates?.[0]?.content?.parts ?? []).map((part) => part.text ?? '').join(''),
          ),
        );
    }

    /** The evaluation as CSV — a blob, because a plain link carries no bearer token (`FRD-602`). */
    testRunCsv(runId: number): Observable<Blob> {
      return this.http.get(`${API}/v1/test-runs/${runId}/export/`, { responseType: 'blob' });
    }

    testStats(): Observable<TestModelStats[]> {
      return this.http.get<TestModelStats[]>(`${API}/v1/test-stats/`);
    }
  };
}
