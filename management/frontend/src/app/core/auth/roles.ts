/**
 * Who may do what, asked once.
 *
 * Each list is the server's single definition restated, and
 * `test_the_console_and_the_server_agree_about_roles.py` compares them: a predicate written by hand
 * in two planes gives one question two answers, and nothing fails when the server's list changes.
 * Lists that share an answer today keep separate names, because they are separate questions. These
 * decide what the console **offers**; the server decides what happens.
 */

/** Roles allowed to stop traffic and to investigate an incident (the gateway's `INCIDENT_ROLES`). */
const INCIDENT_ROLES = ['it-security', 'global-admin'];

/** Roles that see every use case's figures, whether or not they may act (`OVERSIGHT_ROLES`). */
const OVERSIGHT_ROLES = ['it-security', 'it-steuerung', 'global-admin'];

/** Roles that write security-level configuration (the server's `IsITSecurity`). */
const SECURITY_ROLES = ['it-security', 'global-admin'];

/** Roles that may declare, price and release a model (the server's `CATALOG_ROLES`). */
const CATALOG_ROLES = ['global-admin'];

/** The single role that runs the installation (the server's `IsGlobalAdmin`). */
const INSTALLATION_ROLES = ['global-admin'];

/**
 * May this caller act on an incident — stop a caller, filter traffic by the machine it came from?
 *
 * **Visibility and authority are different answers** (`FRD-206`): `it-steuerung` sees every figure
 * and is offered no kill switch, which is why this is not `hasOversight`.
 */
export function mayActOnIncidents(roles: readonly string[] | undefined): boolean {
  return (roles ?? []).some((role) => INCIDENT_ROLES.includes(role));
}

/**
 * May this caller write the standards this installation holds itself to — a global anomaly rule,
 * the question catalogue models are judged against (the server's `IsITSecurity`)?
 */
export function maySetStandards(roles: readonly string[] | undefined): boolean {
  return (roles ?? []).some((role) => SECURITY_ROLES.includes(role));
}

/** May this caller declare a model and release it for use (the server's `MayCatalogueModels`)? */
export function mayCatalogue(roles: readonly string[] | undefined): boolean {
  return (roles ?? []).some((role) => CATALOG_ROLES.includes(role));
}

/**
 * May this caller change what the **installation** does — its own budget, and creating a use case
 * (the server's `IsGlobalAdmin`)?
 */
export function runsTheInstallation(roles: readonly string[] | undefined): boolean {
  return (roles ?? []).some((role) => INSTALLATION_ROLES.includes(role));
}

/** Does this caller see every use case, whether or not they may change anything in one? */
export function hasOversight(roles: readonly string[] | undefined): boolean {
  return (roles ?? []).some((role) => OVERSIGHT_ROLES.includes(role));
}
