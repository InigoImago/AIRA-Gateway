import { EMPTY, Observable, expand, reduce } from 'rxjs';
import { API } from '../prefixes';
import { Page } from '../types/common';
import { UseCase } from '../types/use-cases';
import { ApiClientClass, seg } from './base';

/** The largest page the server serves (`MAX_PAGE_SIZE`), so `list` asks as few times as it can. */
const LIST_PAGE_SIZE = 200;

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

    /**
     * Every use case the caller may see, for a picker. The endpoint is paged (`FRD-208`), so this
     * follows the pages rather than reading the first body as a list — which is what it did, and
     * the security console's rule picker threw on the page object and offered only "everywhere".
     */
    list(): Observable<UseCase[]> {
      const page = (number: number) =>
        this.http.get<Page<UseCase>>(this.base, {
          params: { page: number, page_size: LIST_PAGE_SIZE },
        });
      return page(1).pipe(
        expand((current) => (current.page < current.pages ? page(current.page + 1) : EMPTY)),
        reduce((all, current) => [...all, ...current.results], [] as UseCase[]),
      );
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
