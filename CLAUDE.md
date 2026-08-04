# AIRA Gateway — Project Guidance (CLAUDE.md)

Guidance for anyone (human or AI) working on **AIRA Gateway — AI REST API**.
Read this first, then `docs/PRD.md` and `docs/ROADMAP.md`.

> Note: the sandbox/environment guidance lives in the parent `../CLAUDE.md`. **This** file is about
> the AIRA project itself: what we're building, how we build it, and the conventions to follow.

---

## 1. What we are building
An enterprise-grade AI gateway with two self-developed components + open-source infrastructure:
- **Gateway API** (data plane) — FastAPI.
- **Management & Monitoring** (control plane) — Angular SPA + Django REST Framework.
- Infra: PostgreSQL, Keycloak (SSO), Apache Kafka (event bus), HashiCorp Vault (secrets),
  OpenTelemetry Collector → SigNoz (observability). The two components communicate over **Kafka**.

Full detail: `docs/PRD.md`. Delivery is phased: `docs/ROADMAP.md`.

## 2. Locked-in decisions (see `docs/adr/` for rationale)
- **Language**: all docs, code, and identifiers in **English**.
- **Management UI**: **Angular** (TypeScript SPA) + **Django REST Framework** backend.
  Django keeps ORM/migrations/`django-guardian` object-level RBAC/admin; Angular is the frontend.
- **Gateway**: **FastAPI** (Python **3.14**).
- **Toolchain** (see `ADR-0003`): **Python 3.14 + uv**, **Node 26** (Angular). Pin versions in
  `pyproject.toml`/`.python-version` and `package.json` `engines`/`.nvmrc`.
- **AuthN**: Keycloak OIDC (bearer) **and** self-generated API keys (hashed at rest).
- **Roles (initial)**: Global Administrator, IT Security, IT Steuerung (Governance),
  Use Case Administrator, Use Case User. Least-privilege, object-scoped.
- **Secrets**: only in **HashiCorp Vault** — never commit secrets.
- **Observability**: OTLP → OpenTelemetry Collector → **Grafana `otel-lgtm`** locally (ADR-0004,
  supersedes the earlier SigNoz choice in ADR-0002).
- **Deployment**: **Docker Compose** locally now; Kubernetes/Helm later.
- **Demo mode**: mock upstream + one-command **automated seeding** must always work.

## 3. Engineering conventions
- **Test-first / high coverage**: near-100% unit-test coverage is a hard goal; CI enforces a
  coverage gate. Every feature ships with tests. No feature is "done" without tests.
- **Typed code**: Python type hints (mypy), TypeScript strict mode.
- **Lint/format**: Python (ruff + black), Angular (eslint + prettier). CI blocks on violations.
- **API contracts**: OpenAPI for HTTP; explicit, versioned schemas for Kafka events.
- **Config over code**: behavior driven by use-case configuration, not hard-coded branches.
- **Async on the hot path**: persistence and event emission must not block the gateway request path.
- **Security by default**: validate input, scope every query by role/object, redact where required.

## 4. Documentation discipline (IMPORTANT — keep this current)
Always keep documentation in sync with what is actually built. On any meaningful change:
1. **ADRs** — record every significant/architectural decision as an ADR in `docs/adr/`
   (copy `docs/adr/ADR-TEMPLATE.md`, increment the number, link it from `docs/adr/README.md`).
2. **FRDs** — before building a feature, write/refresh its FRD in `docs/features/`
   (from `docs/features/FRD-TEMPLATE.md`). Update it if the implementation deviates.
3. **DEVLOG** — append a dated entry to `docs/DEVLOG.md` summarizing what changed and why.
4. **PRD/ROADMAP** — update these when scope, phases, or requirements shift.
5. Keep this `CLAUDE.md` updated as conventions evolve.

Rule of thumb: if a future contributor would be surprised or have to reverse-engineer a decision,
write it down (ADR or DEVLOG). Prefer small, frequent updates over big retroactive ones.

## 5. Repository layout (target)
```
AIRA/
├── CLAUDE.md                  # this file
├── README.md
├── docs/
│   ├── PRD.md                 # requirements
│   ├── ROADMAP.md             # phases
│   ├── DEVLOG.md              # running change log
│   ├── adr/                   # architecture decision records
│   └── features/              # FRDs (one per feature)
├── gateway/                   # FastAPI data plane
├── management/
│   ├── backend/               # Django + DRF control plane
│   └── frontend/              # Angular SPA
└── deploy/                    # docker-compose, later helm/k8s
```
(Directories are created as phases begin; not all exist yet.)

## 6. Current status
**Phase 0 (Foundation & Infra) complete.** Local Compose stack (postgres, keycloak, kafka,
schema-registry, vault) runs via `make up`. uv workspace with three Python-side packages
(`aira_common`, `aira_gateway`, `aira_management` = Django+DRF) plus an Angular 22 frontend shell —
all with `/healthz`+`/readyz`, 100% Python coverage, and green ruff/mypy/prettier gates.
Observability (`FRD-001`): OTel Collector + Grafana `otel-lgtm` (traces verified in Tempo). Seed &
demo (`FRD-002`): extensible seed framework + `seed_demo` command creating the 5 roles/users
(verified end-to-end against Postgres); deterministic mock upstream for demo mode.
**Phase 0 complete. Phase 1 in progress:** `FRD-100` done — Gemini-compatible API
(`/v1beta/models/{model}:generateContent|:streamGenerateContent|:embedContent`, list/get models)
on a provider-agnostic canonical core, served by the mock provider (verified end-to-end).
API direction: **Gemini first, OpenAI later** (ADR-0005). `FRD-101` **complete** — auth on the Gemini
routes via **API keys** (`aira_<prefix>_<secret>`, hashed; `x-goog-api-key`/`?key=`/Bearer) **and**
**OIDC bearer** (Keycloak JWKS; realm `aira` under `deploy/compose/keycloak/realms/`). Gateway has a
SQLAlchemy-async DB layer; CLI mints keys; `auth_required`/`oidc_enabled` toggles. `FRD-102` done —
**use-case attribution**: selector via `/uc/<use-case>` path or `X-AIRA-Use-Case` header (header
wins), OIDC membership authorized from Keycloak **groups** (`/use-cases/<slug>` → `Principal.use_cases`;
403 for non-members), `require_use_case` toggle. Verified end-to-end. Next: `FRD-103` (persistence).
See `docs/DEVLOG.md`.

## 7. Working agreement
- Confirm scope via PRD/FRD before large changes; work phase by phase.
- Read relevant files before editing; follow existing patterns.
- Run tests after changes; never weaken the coverage gate to make tests pass.
- Ask when requirements are ambiguous rather than guessing.
