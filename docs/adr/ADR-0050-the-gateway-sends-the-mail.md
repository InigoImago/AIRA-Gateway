# ADR-0050 — The gateway sends the mail, and asks the directory for the address when it sends

- **Status:** Accepted
- **Date:** 2026-10-29
- **Deciders:** Vadim Scheibe
- For `FRD-409`. Fills the gap the gap analysis named ("alert delivery has every input it needs
  and waits on a decision rather than on code — who gets told"); the owner decided who.

## Context

People are to be told by mail when a budget crosses a threshold, and a use case's administrators,
unless they switch it off, when one of the use case's anomaly rules fires. The address is to come from the
person's Keycloak profile.

The facts both mails start from are the **gateway's**. The budget counters sit in its Redis and its
`budget_usage` table, and the findings in its `anomaly_events`. The directory is **Management's**:
a read-only Keycloak account (`view-users`, `query-groups`) that the console uses to find people and
groups when granting access. Between the two planes, configuration flows one way only. Management
publishes to Kafka, the gateway's Kafka identity only reads (`FRD-150`), and Management makes no
call to the gateway. An installation also pays for every infrastructure change with a ticket, so a
new topic, Kafka ACL or Keycloak client is a cost and not a detail.

## Options considered

- **Management sends, and the gateway publishes what crossed.** This needs a gateway-to-Management
  topic and a gateway identity allowed to write it, which is a Kafka change at every installation.
  Management would also have to know the budget hierarchy to tell who a counter belongs to.
- **Management sends, and asks the gateway over HTTP.** The gateway would need a credential for
  Management: a new Keycloak client or audience. Management would poll every counter of every use
  case to find the few that crossed.
- **The gateway takes the address from the token's `email` claim and stores it.** A person who only
  uses an API key never presents one. The stored address goes stale when the profile changes, and
  the gateway would start keeping addresses it does not need for anything else.
- **The gateway checks and sends, and reads the address through the directory account at the
  moment it sends.** It needs nothing new at an installation except the relay, which any mail
  needs.

## Decision

The last option.

1. **A background task in the gateway, beside the anomaly evaluation.** Every 60 seconds it reads
   the counters of the current periods and the new findings, writes one row per mail owed into
   `mail_notices`, which is unique per counter, period and threshold, or per finding, and sends what
   is owed. A transaction-scoped advisory lock, held by a session of its own, makes it one notifier
   per round across the fleet. Each notice commits on its own, so a crash loses at most the mail in
   hand, and that mail is sent again rather than lost.
2. **The address is read when the mail is sent, from Keycloak, by exact username**, through the same
   read-only directory account Management uses. The account's three settings (`AIRA_DIRECTORY_*`)
   become shared by both planes, and the realm is read from the issuer. Nothing about the address is
   stored.
3. **SMTP through the installation's relay, with the standard library**, in a worker thread with a
   timeout. There is no new dependency, and a slow relay holds no request.
4. **Off unless configured.** Without a relay host and a sender nothing is checked or stored, and
   nothing refuses to start. The relay host and the sender are shared settings, so Management can
   tell the console, and the privacy notice, whether mail is sent at all.
5. **A mail is a channel the console's rules do not reach.** A budget mail carries only figures its
   reader may already see. An anomaly mail names no person, key or address, whatever strict data
   mode is set to; the Warnings tab shows each reader what they may see.

## Consequences

- Positive: the only new requirement at an installation is a relay and a route to it from the
  gateway. No Kafka, Keycloak or database change is needed, and the image deploys before the relay
  exists.
- Positive: the counters and findings are read where they are written, under the same hierarchy
  that enforces them (`ADR-0044`).
- Negative: **the data plane gains read access to every profile in the realm** through the
  directory account. It is read-only, and only the notifier uses it, but it is a wider credential
  than the gateway held before. An installation that gives each plane its own Vault path must add
  the account's secret to the gateway's.
- Negative: delivery is at least once. A database lost between the relay's acceptance and the row's
  update sends the mail again.
- Follow-ups: channels other than mail, and per-person opt-out, are not decided here.
