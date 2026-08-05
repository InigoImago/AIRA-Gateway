# FRD-002 — Seed Data & Demo Mode

> Phase: 0 · Status: **Draft** · Owner: Vadim Scheibe · Last updated: 2026-08-04
> Related: `docs/PRD.md` §5 (FR-GW-13, FR-MG-11), §11, §12; `docs/ROADMAP.md` Phase 0

## 1. Summary
Provide a **demo mode** and an **automated seeding** mechanism so the whole system is demonstrable
immediately after startup, without real upstream credentials. Demo mode enables a built-in **mock
upstream** and a deterministic dataset; seeding loads realistic test data (users mapped to all
roles, projects, use cases, pipelines, budgets, anomaly rules, and sample request/response traffic)
via a single command. The seed framework is designed to be **extended by every later phase** so each
new feature ships with demo data that showcases it.

## 2. Goals & Non-Goals
**Goals**
- A `DEMO_MODE` toggle that activates the mock upstream and demo-safe defaults.
- One command (`make seed`) that idempotently loads test data across both components.
- An **extensible seed framework**: phases register their own seed contributions.
- Deterministic, reproducible data (no reliance on wall-clock/random for identity) suitable for tests.
- Data covering **all five roles** so every permission path is demonstrable.

**Non-Goals**
- The full mock-upstream behavior/response fidelity (basic here; richer in `FRD-104`).
- Real provider integration (Phase 3).
- Anomaly detection logic (Phase 5) — seeding only prepares placeholder data structures as they exist.

## 3. User Stories
- As a **developer/evaluator**, I want to run one command and immediately explore a populated system.
- As a **presenter**, I want demo mode so I can show features without real API keys or cost.
- As a **tester**, I want deterministic seed data I can assert against.

## 4. Functional Requirements
- **FR-1 Demo toggle**: `DEMO_MODE` (env) enables the mock upstream and demo defaults across gateway
  and management; clearly indicated in the UI/logs. Off by default outside local.
- **FR-2 Seed command**: `make seed` runs seeding for both components idempotently (safe to re-run;
  upserts rather than duplicates).
- **FR-3 Seed content** (initial, grows per phase):
  - Users mapped to **Global Admin, IT Security, IT Steuerung, Use Case Admin, Use Case User**.
  - 2–3 **projects**, several **use cases** with descriptions/processing-logic text.
  - **Memberships** linking users to use cases with appropriate roles.
  - Sample **API keys** for demo users (clearly demo-only).
  - Placeholder **pipeline**, **budget**, and **anomaly-rule** records (as those models land in later
    phases; guarded so seeding degrades gracefully when a model doesn't exist yet).
  - Sample **request/response logs** and traffic to populate dashboards.
- **FR-4 Extensibility**: a registry/interface where each phase adds a seed module
  (`seed/contributions/*`), executed in a defined order with clear logging of what was created.
- **FR-5 Determinism**: fixed IDs/slugs/timestamps (from a seed clock, not `now()`), so results are
  reproducible and assertable in tests.
- **FR-6 Reset**: `make seed-reset` (or `make seed FRESH=1`) wipes demo data and reseeds cleanly.
- **FR-7 Mock upstream (basic)**: in demo mode the gateway routes to a deterministic mock provider
  returning canned but plausible completions/embeddings (full fidelity in `FRD-104`).

## 5. Design & Architecture
- **Two-sided seeding** coordinated by one entrypoint:
  - Management (Django): a management command `seed_demo` using idempotent upserts (fixtures or
    factory-based, e.g. `factory_boy`), covering users/roles/use cases/memberships.
  - Gateway: seeds gateway-side data (API keys, request/response samples) via a script/CLI.
- **Contribution registry**: ordered list of seed steps; each step is independently testable and
  logs a summary. Later FRDs append steps rather than editing a monolith.
- **Determinism**: a `SEED_EPOCH` constant provides stable timestamps; slugs/UUIDs derived
  deterministically from stable names.
- **Demo mode** is read once at startup into typed config (from `libs/` config loader).

## 6. Data Model
- No new domain entities of its own; it **populates** entities defined by other FRDs. Where a target
  model doesn't exist yet (early phases), the corresponding contribution is a no-op/skip with a log.

## 7. API / Interface Contract
- CLI/Make interface: `make seed`, `make seed-reset`.
- Django management command: `python manage.py seed_demo [--fresh]`.
- Gateway seed CLI: `python -m gateway.seed [--fresh]`.
- No public HTTP API.

## 8. Security & Privacy
- Demo credentials/API keys are clearly labeled and **only** created when `DEMO_MODE` is on.
- Seeding refuses to run against a non-local/production environment unless explicitly forced.
- No real secrets in seed data; demo API keys are non-privileged and scoped to demo use cases.

## 9. Observability
- Seed run logs a structured summary (counts per entity, skips, duration).
- In demo mode, seeded sample traffic makes SigNoz dashboards (from `FRD-001`) non-empty out of the box.

## 10. Testing & Acceptance Criteria
- **Tests**: unit tests that seeding is **idempotent** (running twice yields the same state), that
  all five roles are created, and that `--fresh` resets cleanly. Contribution registry ordering
  tested. Coverage gate stays green.
- **Acceptance**:
  - **Given** `DEMO_MODE=1` and a running stack, **when** I run `make seed`, **then** users for all
    five roles, projects, use cases, and memberships exist and are visible.
  - **When** I run `make seed` again, **then** no duplicates are created (idempotent).
  - **When** I log in as each seeded role, **then** access matches that role's scope.
  - **When** demo mode is on, **then** gateway calls resolve via the mock upstream without external
    network access.

## 11. Dependencies & Risks
- Depends on `FRD-000` (Make targets, service skeletons, config) and benefits from `FRD-001`
  (dashboards). Consumes models from `FRD-101/102` (auth/attribution) and later phases as they land.
- Risk: seed drift as schema evolves → keep contributions modular and covered by tests.
- Risk: accidental seeding in a real environment → hard environment guard + explicit `--force`.

## 12. Rollout / Demo
- Demo: `DEMO_MODE=1 make up && make seed`, then log in as each role and browse a fully populated
  system; show a gateway call served by the mock upstream with a trace in SigNoz.
- Every subsequent phase adds a seed contribution so its features are demoable on day one.


## Addendum (2026-08-05) — Keycloak carries the roles too

Seeding created the five roles and demo users as Django groups/users only. Keycloak is the
source of truth for roles (FRD-201), so those accounts could never actually log in, and the one
realm user had no roles — the acceptance in FRD-203 §5 was not reachable. The realm import now
carries the five realm roles and one user per role (`admin`, `itsec`, `itgov`, `ucadmin`,
`ucuser`, password `demo-password`), with usernames matching this seed so a first login adopts
the seeded Django account instead of provisioning a duplicate (see `OidcIdentity`, ADR-0007).
