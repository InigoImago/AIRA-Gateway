import { Observable } from 'rxjs';
import { API } from '../prefixes';
import { Page } from '../types/common';
import { ConfigurationChange, ConfigurationKind } from '../types/history';
import { ApiClientClass } from './base';

/** The configuration history (`FRD-632`): who changed what, when, from what to what. */
export function withHistory<T extends ApiClientClass>(Base: T) {
  return class extends Base {
    configurationChanges(options: {
      kind?: ConfigurationKind | '';
      useCase?: string;
      page?: number;
      pageSize?: number;
    }): Observable<Page<ConfigurationChange>> {
      const params: Record<string, string | number> = { page: options.page ?? 1 };
      if (options.pageSize) params['page_size'] = options.pageSize;
      if (options.kind) params['kind'] = options.kind;
      if (options.useCase) params['use_case'] = options.useCase;
      return this.http.get<Page<ConfigurationChange>>(`${API}/v1/configuration-changes/`, {
        params,
      });
    }
  };
}
