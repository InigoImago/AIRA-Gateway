import { Observable } from 'rxjs';
import { API, GW } from '../prefixes';
import { Budget, BudgetUsage, RateLimit } from '../types/limits';
import { ApiClientClass, seg } from './base';

/** Budgets (a use case's and the installation's) and rate limits. */
export function withLimits<T extends ApiClientClass>(Base: T) {
  return class extends Base {
    budgets(slug: string): Observable<Budget[]> {
      return this.http.get<Budget[]>(`${this.base}${seg(slug)}/budgets/`);
    }

    createBudget(slug: string, budget: Budget): Observable<Budget> {
      return this.http.post<Budget>(`${this.base}${seg(slug)}/budgets/`, budget);
    }

    deleteBudget(slug: string, id: number): Observable<void> {
      return this.http.delete<void>(`${this.base}${seg(slug)}/budgets/${id}/`);
    }

    /** Current-period consumption per budget, from the gateway. */
    budgetUsage(slug: string): Observable<{ usage: BudgetUsage[] }> {
      return this.http.get<{ usage: BudgetUsage[] }>(`${GW}/v1beta/usage/${seg(slug)}`);
    }

    /**
     * The installation's own budgets (`FRD-610`) — its own route, because this bucket has no slug.
     *
     * A caller without an oversight role gets an empty list rather than a 403: a refusal would tell
     * them there is something here to want.
     */
    installationBudgets(): Observable<Budget[]> {
      return this.http.get<Budget[]>(`${API}/v1/installation-budgets/`);
    }

    /** Upsert on the period, which is the only thing that distinguishes two of these. */
    saveInstallationBudget(budget: Budget): Observable<Budget> {
      return this.http.post<Budget>(`${API}/v1/installation-budgets/`, budget);
    }

    deleteInstallationBudget(id: number): Observable<void> {
      return this.http.delete<void>(`${API}/v1/installation-budgets/${id}/`);
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
  };
}
