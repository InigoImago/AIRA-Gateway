/**
 * What the caller may do across the installation, as the server answers it (`FRD-614`).
 *
 * The server's permission catalogue, in its order. `test_the_console_agrees_about_who_may_do_what.py`
 * holds this list to `aira_common.permissions`, so a permission added or renamed on the server fails
 * a test rather than leaving a screen that asks for a name nobody holds.
 */
export const PERMISSIONS = [
  'usecase.create',
  'usecase.read_all',
  'usecase.manage_all',
  'usecase.read_retired',
  'usecase.purge',
  'catalog.write',
  'report.read_all',
  'budget.installation.write',
  'trace.read_all',
  'content_read.read',
  'payload.read_any',
  'anomaly.read_all',
  'anomaly.rule.global.write',
  'incident.suspend',
  'incident.investigate',
  'operations.diagnose',
  'smoketest.author',
  'smoketest.run_any',
  'directory.search',
  'role.read',
  'role.manage',
] as const;

export type Permission = (typeof PERMISSIONS)[number];

/**
 * Whether the server listed this permission for the caller in `/me`.
 *
 * The console decides only what to offer; the server decides what happens. It asks the permission
 * and never a role, because a role list here would be a second definition of who may do what — one
 * that goes wrong silently the day an installation changes what a role holds.
 */
export function can(
  me: { permissions?: readonly string[] } | null | undefined,
  permission: Permission,
): boolean {
  return me?.permissions?.includes(permission) ?? false;
}
