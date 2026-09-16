# ADR-0027 — The privacy notice is written from a register the schema is checked against

- **Status:** Accepted
- **Date:** 2026-09-16
- **Deciders:** Vadim Scheibe
- **Realised by:** [FRD-625](../features/FRD-625-privacy-notice.md).

## Context

AIRA records who asked which model what, when, from where and at what cost. In a company that is
personal data about employees, and a system objectively capable of monitoring them. Every user needs
an Art. 13 DSGVO notice, and the owner's standing requirement is that a change to the processing is
a change to the notice.

A notice kept as a document beside the code would be the pattern this project has recorded again
and again: the copy that is read stays true, the copy nobody opens rots. Here the copy nobody opens
is the one with legal weight.

## Decision

1. **The notice is rendered from code.** A register (`apps/privacy/activities.py`) lists every
   processing activity. Per-language texts (`texts/<code>.toml`) describe each one. Values that
   depend on the installation are filled in from its settings and roles, not written into the
   text: controller, retention, content storage, key lifetimes, and what each role may read.
2. **The register is checked against the schema in both directions.** Every column either plane
   stores is named by an activity or declared not personal. A person-shaped column in a table
   declared not personal fails too.
3. **The text is pinned to its date.** A digest of the register and the texts is committed beside
   the edition date. A change without a new digest fails the suite.
4. **The version covers what was rendered**, in every language. A changed controller, retention,
   role or text asks everybody again. Switching language asks nothing.
5. **Acknowledging is taking note, not consenting.** The legal bases are contract, legal obligation
   and legitimate interest. The acknowledgement is evidence of being informed (Art. 5(2)).

## Consequences

- Adding a column that stores data about people fails the suite until the register names it. The
  register change moves the digest, which demands a new edition.
- The check cannot see processing that stores nothing, such as a transmission, a span attribute or
  browser storage. Those activities are in the register for their text and remain a reviewer's
  duty.
- The notice has to describe the system as it is, gaps included. Three gaps it states are open
  decisions (`FRD-625` §11).
- The texts are a starting point and not legal advice. A deployment has them reviewed by its DPO
  and, in Germany, agreed with the works council.
