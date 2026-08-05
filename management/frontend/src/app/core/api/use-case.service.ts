import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  ApiKey,
  Budget,
  BudgetUsage,
  RateLimit,
  CatalogModel,
  DryRunResult,
  IssuedApiKey,
  Membership,
  PipelineConfig,
  UseCase,
} from './models';

/**
 * Every value interpolated into a URL is encoded: slugs, usernames, and key prefixes come from
 * user input, and an unencoded `/` or `..` would silently retarget the request at a different
 * endpoint (ADR-0007).
 */
const seg = (value: string): string => encodeURIComponent(value);

@Injectable({ providedIn: 'root' })
export class UseCaseService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/v1/use-cases/';

  list(): Observable<UseCase[]> {
    return this.http.get<UseCase[]>(this.base);
  }

  get(slug: string): Observable<UseCase> {
    return this.http.get<UseCase>(`${this.base}${seg(slug)}/`);
  }

  create(useCase: Partial<UseCase>): Observable<UseCase> {
    return this.http.post<UseCase>(this.base, useCase);
  }

  update(slug: string, changes: Partial<UseCase>): Observable<UseCase> {
    return this.http.patch<UseCase>(`${this.base}${seg(slug)}/`, changes);
  }

  remove(slug: string): Observable<void> {
    return this.http.delete<void>(`${this.base}${seg(slug)}/`);
  }

  members(slug: string): Observable<Membership[]> {
    return this.http.get<Membership[]>(`${this.base}${seg(slug)}/members/`);
  }

  addMember(slug: string, username: string, role: string): Observable<Membership> {
    return this.http.post<Membership>(`${this.base}${seg(slug)}/members/`, { username, role });
  }

  removeMember(slug: string, username: string): Observable<void> {
    return this.http.delete<void>(`${this.base}${seg(slug)}/members/${seg(username)}/`);
  }

  apiKeys(slug: string): Observable<ApiKey[]> {
    return this.http.get<ApiKey[]>(`${this.base}${seg(slug)}/api-keys/`);
  }

  issueApiKey(slug: string, label: string): Observable<IssuedApiKey> {
    return this.http.post<IssuedApiKey>(`${this.base}${seg(slug)}/api-keys/`, { label });
  }

  revokeApiKey(slug: string, prefix: string): Observable<void> {
    return this.http.delete<void>(`${this.base}${seg(slug)}/api-keys/${seg(prefix)}/`);
  }

  getPipeline(slug: string): Observable<PipelineConfig> {
    return this.http.get<PipelineConfig>(`${this.base}${seg(slug)}/pipeline/`);
  }

  savePipeline(slug: string, config: PipelineConfig): Observable<PipelineConfig> {
    return this.http.put<PipelineConfig>(`${this.base}${seg(slug)}/pipeline/`, config);
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
    return this.http.get<Budget[]>(`${this.base}${seg(slug)}/budgets/`);
  }

  createBudget(slug: string, budget: Budget): Observable<Budget> {
    return this.http.post<Budget>(`${this.base}${seg(slug)}/budgets/`, budget);
  }

  deleteBudget(slug: string, id: number): Observable<void> {
    return this.http.delete<void>(`${this.base}${seg(slug)}/budgets/${id}/`);
  }

  rateLimits(slug: string): Observable<RateLimit[]> {
    return this.http.get<RateLimit[]>(`${this.base}${seg(slug)}/rate-limits/`);
  }

  createRateLimit(slug: string, limit: RateLimit): Observable<RateLimit> {
    return this.http.post<RateLimit>(`${this.base}${seg(slug)}/rate-limits/`, limit);
  }

  deleteRateLimit(slug: string, id: number): Observable<void> {
    return this.http.delete<void>(`${this.base}${seg(slug)}/rate-limits/${id}/`);
  }

  /** The model catalog with its prices; everyone reads it, only a global admin writes. */
  models(): Observable<CatalogModel[]> {
    return this.http.get<CatalogModel[]>('/api/v1/models/');
  }

  saveModel(model: CatalogModel): Observable<CatalogModel> {
    return this.http.post<CatalogModel>('/api/v1/models/', model);
  }

  removeModel(name: string): Observable<void> {
    return this.http.delete<void>(`/api/v1/models/${seg(name)}/`);
  }

  /** Current-period consumption per budget, from the gateway. */
  budgetUsage(slug: string): Observable<{ usage: BudgetUsage[] }> {
    return this.http.get<{ usage: BudgetUsage[] }>(`/gw/v1beta/usage/${seg(slug)}`);
  }
}
