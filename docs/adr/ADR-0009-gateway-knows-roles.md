# ADR-0009 — The gateway learns realm roles, from one shared definition

- **Status:** Accepted
- **Date:** 2026-08-06
- **Deciders:** Vadim Scheibe

## Context

Spend and usage reporting is the next piece of work (`FRD-601`), and it runs into a boundary the
architecture has not had to state before.

**The data is in the data plane.** `request_logs` lives in the gateway's database: one row per
dispatched request with its use case, subject, model, tokens, latency, status and cost. Management
has no access to it and no equivalent of it.

**The authorization is in the control plane.** Management owns RBAC. `scope_queryset` already
answers the question reporting asks — governance roles see every use case, everyone else sees
what `django-guardian` grants them.

The gateway can answer "may this caller see *this* use case": an OIDC principal carries the
use-case slugs derived from its Keycloak groups, and `authorize_use_case` compares against them.
That is enough for `GET /v1beta/usage/{use_case}`, which is why consumption already works.

It is not enough for the question governance actually asks — *across all use cases*. A member of
the `it-steuerung` role is deliberately **not** a member of the use cases they oversee; that
separation is the point of the role (ADR-0007: read visibility must never imply the right to act
inside a use case). To the gateway, such a caller currently looks like someone with no
memberships at all.

## Options considered

- **Management aggregates instead.** Usage events flow to Management over Kafka; it serves the
  reporting from its own store, where the RBAC already lives. Governance authorization stays in
  exactly one place. But the figures are then held twice, a new pipeline has to be built and
  operated, and the two copies can disagree — for numbers somebody is accountable for, "which
  one is right" is a question worth avoiding entirely.

- **Per-use-case only.** Serve reporting through the existing path and give governance nothing.
  No new architecture, and no answer for the role the PRD defines. Deferring the requirement is
  not the same as meeting it.

- **The gateway learns roles.** The OIDC token already carries `realm_access.roles`; the
  validator simply does not read them. Extracting them lets the gateway answer the same question
  Management answers, against the same claim Keycloak issues.

## Decision

**The gateway extracts realm roles from the token and its `Principal` carries them.** A caller
holding a governance role may read reporting across use cases; everyone else is confined to their
memberships, exactly as before.

**The role names and the governance set move to `aira_common.roles`.** This is the load-bearing
half of the decision. Two services deciding independently who counts as governance is precisely
how a role gets added in one place, missed in the other, and quietly grants or withholds access
for months. One definition, imported by both, makes that impossible rather than unlikely.

What deliberately does **not** move: what the two services *do* with a role. Management maps
roles to Django groups and object permissions; the gateway compares a claim on a request. Those
are different mechanisms answering different questions, and merging them would mean the data
plane depending on `django-guardian`.

Keycloak stays the source of truth in both. Neither service stores a role decision of its own —
they read the same claim from the same token.

## Consequences

- **Positive**: governance reporting is served from the store that holds the data, with no second
  copy of the figures and no new pipeline to operate.
- **Positive**: `Principal.roles` is available for other data-plane decisions that need one — the
  IT-Security console of Phase 5 (`FRD-502`) needs precisely this to scope its cross-use-case
  view.
- **Negative**: the gateway now has an authorization input it did not have. A token minted for
  the data plane carries organisational roles, so a compromised realm client is worth marginally
  more than before. This does not change what the roles *grant* — read access to figures, never
  the right to act inside a use case, which stays with membership.
- **Negative**: the gateway must be reachable for reporting to work at all. That is already true
  of the consumption view (`FRD-402`), and both degrade to showing what Management knows.
- **Follow-up**: reporting shows **aggregates only**. Browsing individual requests would show
  stored prompts to people who are deliberately not members of the use case that produced them,
  which is exactly what content redaction (`FRD-406`, deferred) exists to make safe. That view
  waits for it, and this ADR is the reason the wait is not merely a backlog ordering.
