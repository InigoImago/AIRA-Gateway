import { Observable } from 'rxjs';
import { GW } from '../prefixes';
import { DryRunResult, PipelineConfig } from '../types/pipelines';
import { ApiClientClass, seg } from './base';

/** A use case's pipeline, and running one against a sample prompt. */
export function withPipelines<T extends ApiClientClass>(Base: T) {
  return class extends Base {
    getPipeline(slug: string): Observable<PipelineConfig> {
      return this.http.get<PipelineConfig>(`${this.base}${seg(slug)}/pipeline/`);
    }

    savePipeline(slug: string, config: PipelineConfig): Observable<PipelineConfig> {
      return this.http.put<PipelineConfig>(`${this.base}${seg(slug)}/pipeline/`, config);
    }

    /**
     * Run a (possibly unsaved) pipeline against a sample prompt.
     *
     * `use_case` is **required by the gateway**: a dry run runs the real engine, so an LLM-backed
     * step spends real tokens and belongs to a use case exactly as a request does (`FRD-308`).
     */
    dryRunPipeline(payload: {
      use_case: string;
      system: string;
      user: string;
      model?: string;
      pipeline: PipelineConfig;
      /** Keep evaluating after a step refuses. Costs real tokens for steps production never runs. */
      past_blocks?: boolean;
    }): Observable<DryRunResult> {
      return this.http.post<DryRunResult>(`${GW}/v1beta/pipeline:dryRun`, payload);
    }
  };
}
