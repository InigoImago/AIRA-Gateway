# FRD-204 — Config distribution over Kafka (Management → Gateway read-model)

> Phase: 2 · Status: **Done (Phase 2)** · Owner: Vadim Scheibe
> Related: `docs/PRD.md` §4.3, §8; `docs/ROADMAP.md` Phase 2; builds on FRD-202; `ADR-0006`

## 1. Summary
Establish the event backbone between the two components: Management **publishes** configuration
changes (use cases, memberships, and — via FRD-205 — API keys) to **Kafka**; the Gateway **consumes**
them into local **read-models** it uses on the hot path. This realizes the PRD's "communicate over
Kafka, Gateway holds read-models" architecture and removes any synchronous coupling.

## 2. Goals & Non-Goals
**Goals**
- Versioned event schemas (Schema Registry) for `usecase.*` and `membership.*` (and `api_key.*` in
  FRD-205); at-least-once delivery; idempotent consumers.
- Management producer: publish on create/update/delete (transactional-outbox-style reliability).
- Gateway consumer: apply events into read-model tables; W3C trace context on headers (FRD-001).
- Backfill/replay: a way to rebuild the Gateway read-model from a compacted topic.

**Non-Goals**
- The API-key events specifically (FRD-205 defines their schema/consumer). Anomaly events (Phase 5).

## 3. Functional Requirements
- **FR-1 Schemas**: define `usecase.upserted`/`usecase.deleted`, `membership.upserted`/`.removed`
  (keys, use-case slug, members) with schema versions.
- **FR-2 Producer**: reliable publish from Management (outbox table + relay, or transactional
  producer) so events are never lost on commit.
- **FR-3 Consumer**: idempotent Gateway consumer writing read-model rows; ordered per key.
- **FR-4 Propagation**: use-case membership changes reflect into the Gateway's authorization data
  (and/or Keycloak groups) so `/uc/<slug>` authorization stays correct.
- **FR-5 Observability**: consumer lag + apply errors are metered; trace context propagated.
- **FR-6 Replay**: compacted topics allow rebuilding the read-model from scratch.

## 4. Design & Architecture
- `aira_common` gains a typed Kafka producer/consumer wrapper over the FRD-000 abstraction (real
  aiokafka/confluent client) with Schema Registry (Avro/JSON Schema). Outbox in Management; consumer
  worker in the Gateway (separate process/entrypoint).

## 5. Testing & Acceptance
- Hermetic tests: schema (de)serialization; producer writes outbox; consumer applies idempotently
  (replay-safe) using an in-memory/fake broker. Integration tests (marked) use real Kafka.
- **Acceptance**: creating a use case in Management makes it appear in the Gateway read-model;
  re-delivering the same event changes nothing (idempotent).

## 6. Dependencies & Risks
- Builds on FRD-202. Foundation for FRD-205 and later phases. Risk: eventual consistency window →
  bounded + monitored; Risk: delivery guarantees → outbox + idempotent apply.
