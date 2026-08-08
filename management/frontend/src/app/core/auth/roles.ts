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

/** Does this caller see every use case, whether or not they may change anything in one? */
export function hasOversight(roles: readonly string[] | undefined): boolean {
  return (roles ?? []).some((role) => OVERSIGHT_ROLES.includes(role));
}
