# FRD-204 — Config distribution over Kafka (Management → Gateway read-model)

> Phase: 2 · Status: **Done (Phase 2) except FR-1's schema versions and FR-5's metrics** · Owner: Vadim Scheibe
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

> **As built: no versioned schemas, and no Schema Registry.** An event is a JSON object with an
> `event_type` header (`aira_common/kafka.py`) — no version field, no schema module, no registry
> client in either plane. Each payload is built per view in Management
> (`apps/usecases/views/payloads.py` and its siblings) and parsed per handler in the gateway
> (`consumer/apply.py`, `HANDLERS`), which fills an absent field with the handler's default and
> skips an event type it does not know (`config_event_not_applied`). The two planes are kept in
> step by cross-plane tests in `tools/tests` — the event vocabulary
> (`test_the_two_planes_agree_on_the_event_vocabulary.py`), a model event's fields
> (`test_a_model_event_is_applied_whole.py`) and the column widths
> (`test_what_management_accepts_fits_the_gateway.py`). The Compose stack still runs a
> `schema-registry` container that no code uses. At-least-once delivery, idempotent apply,
> ordering per key and replay from compacted topics are built.
- Management producer: publish on create/update/delete (transactional-outbox-style reliability).
- Gateway consumer: apply events into read-model tables; W3C trace context on headers (FRD-001).
- Backfill/replay: a way to rebuild the Gateway read-model from a compacted topic.

**Non-Goals**
- The API-key events specifically (FRD-205 defines their schema/consumer). Anomaly events (Phase 5).

## 3. Functional Requirements
- **FR-1 Schemas**: define `usecase.upserted`/`usecase.deleted`, `membership.upserted`/`.removed`
  (keys, use-case slug, members) with schema versions. *As built: the events, without versions —
  see the note under §2.*
- **FR-2 Producer**: reliable publish from Management (outbox table + relay, or transactional
  producer) so events are never lost on commit.
- **FR-3 Consumer**: idempotent Gateway consumer writing read-model rows; ordered per key.
- **FR-4 Propagation**: use-case membership changes reflect into the Gateway's authorization data
  (and/or Keycloak groups) so `/uc/<slug>` authorization stays correct.
- **FR-5 Observability**: consumer lag + apply errors are metered; trace context propagated.
  *As built: only the trace context.* No metric instrument exists in either plane. An event that
  cannot be applied is a `config_event_failed` log line, with topic, partition and offset, and an
  error status on the event's span (`consumer/worker.py`); consumer lag is whatever the broker
  reports for the consumer group.
- **FR-6 Replay**: compacted topics allow rebuilding the read-model from scratch.
- **FR-7 What Management accepts, the read-model holds**: every text an event carries fits the
  column it is applied to. A narrower column makes PostgreSQL refuse the **whole** event — the
  consumer logs `config_event_failed` and moves on, the console shows the change saved, and the
  plane that enforces it never receives it. SQLite enforces no length, so the two declarations are
  compared across the planes (`tools/tests/test_what_management_accepts_fits_the_gateway.py`); a
  use case's `description` and `processing_notes` are unbounded on the gateway, the bound being
  Management's (`MAX_PROSE_LENGTH`).
- **FR-8 A use case the read-model does not hold serves nothing** (`refuse_unless_live`, the first
  step of `prepare_for_dispatch`, and the dry run). Every control a use case carries lives on its
  row — its release, whether its content is stored, its retirement — and each reads its permissive
  value when there is none. So a request naming a slug without a row is refused `403` ("not known
  to this gateway"), recorded as `invalid_request` with no payload stored, rather than served with
  every model, every prompt stored and no retirement. A use case created a moment ago serves once
  its event has arrived; a Keycloak group `/use-cases/<slug>` naming a slug Management does not
  have is refused for good. Retired use cases are refused the same way (`FRD-607` §4.1).

## 4. Design & Architecture
- `aira_common` gains a typed Kafka producer/consumer wrapper over the FRD-000 abstraction (real
  aiokafka/confluent client) with Schema Registry (Avro/JSON Schema). Outbox in Management; consumer
  worker in the Gateway (separate process/entrypoint). *As built: aiokafka, JSON, no Schema
  Registry (§2).*

## 5. Testing & Acceptance
- Hermetic tests: schema (de)serialization; producer writes outbox; consumer applies idempotently
  (replay-safe) using an in-memory/fake broker. Integration tests (marked) use real Kafka.
- **Acceptance**: creating a use case in Management makes it appear in the Gateway read-model;
  re-delivering the same event changes nothing (idempotent).

## 6. Dependencies & Risks
- Builds on FRD-202. Foundation for FRD-205 and later phases. Risk: eventual consistency window →
  bounded (not monitored: there is no lag metric, FR-5), and a use case inside it is refused rather
  than served without its controls (FR-8); Risk: delivery guarantees → outbox + idempotent apply;
  Risk: an event the read-model cannot hold → the planes' bounds compared in CI (FR-7).
