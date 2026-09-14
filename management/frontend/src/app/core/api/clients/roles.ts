import { Observable } from 'rxjs';
import { API } from '../prefixes';
import { Page } from '../types/common';
import { GroupCheck, PermissionInfo, Role, RoleChange, RoleDraft } from '../types/roles';
import { ApiClientClass, seg } from './base';

/**
 * The roles, the permission catalogue they are made of, and their change log (`FRD-614`).
 *
 * Reading needs `role.read`, writing needs `role.manage`; the server decides both.
 */
export function withRoles<T extends ApiClientClass>(Base: T) {
  return class extends Base {
    roles(): Observable<Role[]> {
      return this.http.get<Role[]>(`${API}/v1/roles/`);
    }

    /** Every permission with its area and label, in display order. */
    permissionCatalogue(): Observable<PermissionInfo[]> {
      return this.http.get<PermissionInfo[]>(`${API}/v1/roles/catalogue/`);
    }

    /** Who created, changed or deleted which role, newest first. Paged by page number. */
    roleChanges(options: {
      role?: string;
      page?: number;
      pageSize?: number;
    }): Observable<Page<RoleChange>> {
      const params: Record<string, string | number> = { page: options.page ?? 1 };
      if (options.pageSize) params['page_size'] = options.pageSize;
      if (options.role) params['role'] = options.role;
      return this.http.get<Page<RoleChange>>(`${API}/v1/roles/changes/`, { params });
    }

    /**
     * Whether Keycloak has this group and it confers no role yet. The save asks again: this answer
     * tells the person at the form, it does not bind anything.
     */
    checkGroup(groupPath: string): Observable<GroupCheck> {
      return this.http.post<GroupCheck>(`${API}/v1/roles/check-group/`, {
        group_path: groupPath,
      });
    }

    createRole(draft: RoleDraft): Observable<Role> {
      return this.http.post<Role>(`${API}/v1/roles/`, draft);
    }

    /** `PATCH` with only what changed, so a save never rewrites a field the form did not touch. */
    updateRole(slug: string, changes: Partial<RoleDraft>): Observable<Role> {
      return this.http.patch<Role>(`${API}/v1/roles/${seg(slug)}/`, changes);
    }

    deleteRole(slug: string): Observable<void> {
      return this.http.delete<void>(`${API}/v1/roles/${seg(slug)}/`);
    }
  };
}
