/**
 * A role: a set of installation-wide permissions bound to Keycloak groups (`FRD-614`, `ADR-0025`).
 *
 * `fixed` roles are defined in code and never change, so nobody can lock everybody out. `builtin`
 * roles that are not fixed keep their name and group from configuration and change only what they
 * may do. Every other role is the installation's own.
 */
export interface Role {
  slug: string;
  label: string;
  group_paths: string[];
  permissions: string[];
  builtin: boolean;
  fixed: boolean;
}

/**
 * One entry of the server's permission catalogue, in the server's words (`FRD-614` FR-10).
 *
 * `reserved` permissions belong to the Global Administrator alone and are never offered as a
 * checkbox, so no role can be given the power to change roles.
 */
export interface PermissionInfo {
  name: string;
  area: string;
  label: string;
  sensitive: boolean;
  reserved: boolean;
}

/** What a role held at one moment, as the change log records it. */
export interface RoleSnapshot {
  label: string;
  group_path: string;
  permissions: string[];
}

/**
 * One creation, change or deletion of a role (`FRD-614` FR-6). `before` is `null` for a creation
 * and `after` is `null` for a deletion.
 */
export interface RoleChange {
  id: number;
  role: string;
  action: 'created' | 'updated' | 'deleted';
  actor: string;
  at: string;
  before: RoleSnapshot | null;
  after: RoleSnapshot | null;
}

/** What a role is created from. */
export interface RoleDraft {
  label: string;
  group_path: string;
  permissions: string[];
}

/** The directory's answer that a group exists and confers no role yet (`FRD-614` FR-4). */
export interface GroupCheck {
  group_path: string;
  exists: boolean;
}
