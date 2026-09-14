import { PermissionInfo } from '../../core/api/models';

/** One area of the catalogue and its permissions, in the server's order. */
export interface PermissionArea {
  area: string;
  permissions: PermissionInfo[];
}

/**
 * What the ⚠ beside a permission means. One sentence for all of them, because the catalogue says
 * which permissions are sensitive and a reason per permission written here would restate it.
 */
export const SENSITIVE_EXPLAINED =
  'Sensitive: it reaches stored content, stops traffic, changes every use case, or cannot be undone.';

/**
 * The catalogue grouped by area, areas in the order they first appear. The server lists the
 * catalogue in display order, so grouping must not re-sort it.
 */
export function byArea(permissions: readonly PermissionInfo[]): PermissionArea[] {
  const areas: PermissionArea[] = [];
  for (const permission of permissions) {
    const last = areas.find((entry) => entry.area === permission.area);
    if (last) last.permissions.push(permission);
    else areas.push({ area: permission.area, permissions: [permission] });
  }
  return areas;
}
