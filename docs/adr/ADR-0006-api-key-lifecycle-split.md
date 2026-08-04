# ADR-0006 — API key lifecycle: issued by Management, validated by Gateway (Kafka-distributed)

- **Status:** Accepted
- **Date:** 2026-08-04
- **Deciders:** Vadim Scheibe

## Context
FRD-101 (Phase 1) put API-key **generation, storage, and validation** all in the Gateway (with a
CLI), to unblock authentication before the Management identity/RBAC exists. That co-location is a
Phase-1 bootstrap, not the target. The question: where should API-key **issuance** live?

Key forces:
- Keys are **self-service** and shown **once** — an inherently UI/control-plane concern.
- Keys are bound to a **user / project / use case** — those models live in Management (Phase 2).
- The Gateway is the **data plane**: per-request validation must be fast and must not depend
  synchronously on Management being available.
- The two components communicate over **Kafka**, with the Gateway holding local read-models
  (established project architecture; PRD §4.3).

## Options considered
- **Gateway issues & validates (current Phase-1 state)** — simple, but issuance/UI/secret-handling
  end up in the data plane; the plaintext would have to cross to Management to be shown once.
- **Management issues; Gateway calls Management to validate per request** — clean ownership, but a
  synchronous hot-path dependency (latency + availability coupling). Rejected.
- **Shared `api_keys` DB table read by both** — simplest distribution, but couples the components at
  the database boundary, against the "communicate via Kafka, separate stores" design. Rejected.
- **Management issues; Kafka distributes; Gateway validates against a local read-model** — clean
  control-plane/data-plane split, no hot-path coupling, eventual consistency. Chosen.

## Decision
- **Issuance & lifecycle** (create, show-once, revoke, list, bind to user/project/use-case) live in
  **Management** (control plane, behind SSO). The plaintext is generated in Management and shown
  **once** in the UI; only the **hash + metadata** are stored.
- **Distribution:** Management publishes `api_key.created` / `api_key.revoked` events to **Kafka**,
  carrying `prefix`, `key_hash`, `subject`, scope (use case/project), and status — **never the
  plaintext**.
- **Validation:** the **Gateway** consumes these events into its local **read-model** (the existing
  `api_keys` table, repurposed) and validates each request locally, resolving a `Principal`.
- The Gateway **CLI** (`python -m aira_gateway.cli api-key …`) is retained as an **ops/bootstrap**
  tool (break-glass, local dev), not the primary path.

## Consequences
- Positive: correct CP/DP separation; secret material stays in Management; no synchronous Management
  dependency on the request path; scoping to use cases lands naturally where those models live.
- Negative / trade-offs: eventual consistency — a newly issued/revoked key propagates via Kafka with
  a short delay (acceptable; revocation latency is bounded and can be logged/monitored). Two places
  touch the `api_keys` concept (writer = Management, read-model = Gateway).
- Migration: this move happens in **Phase 2**, once Management identity/RBAC (FRD-201) and Kafka
  config distribution (FRD-204) exist. Until then the Phase-1 CLI-seeded Gateway store stands in.
- Follow-ups: a Phase-2 FRD for Management-side issuance + the `api_key.*` Kafka event schema and the
  Gateway consumer (see ROADMAP `FRD-205`). OIDC bearer validation stays in the Gateway (unchanged).
