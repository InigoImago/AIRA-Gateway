import { Observable } from 'rxjs';
import { API } from '../prefixes';
import { Page } from '../types/common';
import { UseCase } from '../types/use-cases';
import { ApiClientClass, seg } from './base';

/** Use cases themselves: list, read, create, change, delete. */
export function withUseCases<T extends ApiClientClass>(Base: T) {
  return class extends Base {
    /**
     * One page of use cases, searched at the server (`FRD-208`).
     *
     * Paged there because this endpoint computes object-level permissions per row: slicing in the
     * browser would still compute every one of them on every load.
     */
    listPage(query: string, page: number): Observable<Page<UseCase>> {
      const params: Record<string, string | number> = { page };
      if (query) params['q'] = query;
      return this.http.get<Page<UseCase>>(`${API}/v1/use-cases/`, { params });
    }

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
  };
}
