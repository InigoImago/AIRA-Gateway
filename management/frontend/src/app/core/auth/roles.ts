/**
 * Who may do what, asked once.
 *
 * The gateway has `INCIDENT_ROLES` and `OVERSIGHT_ROLES` as single definitions for exactly the
 * reason this file exists: on 2026-08-07 a live round found `it-steuerung` able to stop traffic in
 * the gateway while Management refused it a global rule — **two planes, one question, two
 * answers**, because the predicate had been written by hand in both. A console that restates the
 * list a third time is the same defect with a longer fuse: nothing fails when the server's list
 * changes, the screen simply starts offering, or withholding, the wrong thing.
 *
 * These are *console* predicates — they decide what to **offer**. The server decides what happens,
 * every time, and disagreement here shows up as a refusal rather than as access.
 */

/** Roles allowed to stop traffic and to investigate an incident (the gateway's `INCIDENT_ROLES`). */
const INCIDENT_ROLES = ['it-security', 'global-admin'];

/** Roles that see every use case's figures, whether or not they may act (`OVERSIGHT_ROLES`). */
const OVERSIGHT_ROLES = ['it-security', 'it-steuerung', 'global-admin'];

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
 *
 * The same two roles as `INCIDENT_ROLES` today, and deliberately a **separate** list: they are two
 * different questions that happen to have the same answer, and folding them together would make a
 * future change to one silently change the other.
 */
export function maySetStandards(roles: readonly string[] | undefined): boolean {
  return (roles ?? []).some((role) => SECURITY_ROLES.includes(role));
}

/** Roles that write security-level configuration (the server's `IsITSecurity`). */
const SECURITY_ROLES = ['it-security', 'global-admin'];

/**
 * Roles that may declare a model, price it and release it into the catalogue.
 *
 * The server's `CATALOG_ROLES`, which `MayCatalogueModels` is built from — and which has its own
 * name there for the reason it has one here: it is *"the same set as `IsGlobalAdmin` today, and a
 * separate name anyway"*, because two questions that happen to share an answer must not be one
 * question. Written out by hand in `model-catalog.ts` until 2026-08-27, which is the third copy
 * this file exists to prevent, inside the file that says so.
 */
const CATALOG_ROLES = ['global-admin'];

/** The single role that runs the installation (the server's `IsGlobalAdmin`). */
const INSTALLATION_ROLES = ['global-admin'];

/**
 * May this caller declare a model and release it for use (the server's `MayCatalogueModels`)?
 */
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
