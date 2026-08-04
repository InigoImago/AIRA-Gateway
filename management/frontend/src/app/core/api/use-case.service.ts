import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  ApiKey,
  Budget,
  BudgetUsage,
  DryRunResult,
  IssuedApiKey,
  Membership,
  PipelineConfig,
  UseCase,
} from './models';

@Injectable({ providedIn: 'root' })
export class UseCaseService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/v1/use-cases/';

  list(): Observable<UseCase[]> {
    return this.http.get<UseCase[]>(this.base);
  }

  get(slug: string): Observable<UseCase> {
    return this.http.get<UseCase>(`${this.base}${slug}/`);
  }

  create(useCase: Partial<UseCase>): Observable<UseCase> {
    return this.http.post<UseCase>(this.base, useCase);
  }

  update(slug: string, changes: Partial<UseCase>): Observable<UseCase> {
    return this.http.patch<UseCase>(`${this.base}${slug}/`, changes);
  }

  remove(slug: string): Observable<void> {
    return this.http.delete<void>(`${this.base}${slug}/`);
  }

  members(slug: string): Observable<Membership[]> {
    return this.http.get<Membership[]>(`${this.base}${slug}/members/`);
  }

  addMember(slug: string, username: string, role: string): Observable<Membership> {
    return this.http.post<Membership>(`${this.base}${slug}/members/`, { username, role });
  }

  removeMember(slug: string, username: string): Observable<void> {
    return this.http.delete<void>(`${this.base}${slug}/members/${username}/`);
  }

  apiKeys(slug: string): Observable<ApiKey[]> {
    return this.http.get<ApiKey[]>(`${this.base}${slug}/api-keys/`);
  }

  issueApiKey(slug: string, label: string): Observable<IssuedApiKey> {
    return this.http.post<IssuedApiKey>(`${this.base}${slug}/api-keys/`, { label });
  }

  revokeApiKey(slug: string, prefix: string): Observable<void> {
    return this.http.delete<void>(`${this.base}${slug}/api-keys/${prefix}/`);
  }

  getPipeline(slug: string): Observable<PipelineConfig> {
    return this.http.get<PipelineConfig>(`${this.base}${slug}/pipeline/`);
  }

  savePipeline(slug: string, config: PipelineConfig): Observable<PipelineConfig> {
    return this.http.put<PipelineConfig>(`${this.base}${slug}/pipeline/`, config);
  }

  /** Dry-run a (possibly unsaved) pipeline against a sample prompt via the gateway. */
  dryRunPipeline(payload: {
    system: string;
    user: string;
    model?: string;
    pipeline: PipelineConfig;
  }): Observable<DryRunResult> {
    return this.http.post<DryRunResult>('/gw/v1beta/pipeline:dryRun', payload);
  }

  budgets(slug: string): Observable<Budget[]> {
    return this.http.get<Budget[]>(`${this.base}${slug}/budgets/`);
  }

  createBudget(slug: string, budget: Budget): Observable<Budget> {
    return this.http.post<Budget>(`${this.base}${slug}/budgets/`, budget);
  }

  deleteBudget(slug: string, id: number): Observable<void> {
    return this.http.delete<void>(`${this.base}${slug}/budgets/${id}/`);
  }

  /** Current-period consumption per budget, from the gateway. */
  budgetUsage(slug: string): Observable<{ usage: BudgetUsage[] }> {
    return this.http.get<{ usage: BudgetUsage[] }>(`/gw/v1beta/usage/${slug}`);
  }
}
