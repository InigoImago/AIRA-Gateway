# ADR-0003 — Toolchain & runtime versions

- **Status:** Accepted
- **Date:** 2026-08-04
- **Deciders:** Vadim Scheibe

## Context
We want to start on current, well-supported versions to maximize the useful lifetime of the project
and avoid an early upgrade treadmill. The two application runtimes are Python (Gateway + Django
backend) and Node (Angular frontend + build tooling), plus the container/infra tooling.

## Options considered
- **Conservative LTS** (e.g. Python 3.12, Node LTS 22) — maximum ecosystem/library compatibility,
  but older from day one.
- **Latest stable** (Python 3.14, Node 26) — newest language/runtime features and longest runway;
  small risk that some libraries lag behind the newest interpreter/runtime.

## Decision
Start on **latest stable**:
- **Python 3.14** as the interpreter for the Gateway (FastAPI) and Management backend (Django/DRF).
- **uv** as the Python package/three-project manager and virtual-env tool (fast, lockfile-based).
- **Node 26** for the Angular frontend and its build/test tooling.
- **Docker + Docker Compose** for local orchestration (host provides current versions).

Pin exact versions in each service's project files (`pyproject.toml` `requires-python`,
`.python-version`, `package.json` `engines`, `.nvmrc`) and in container base images, so the
toolchain is reproducible.

## Consequences
- Positive: longest support runway; newest features; uv gives fast, reproducible Python envs.
- Negative / trade-offs: a dependency may not yet publish wheels/support for Python 3.14 or declare
  Node 26 compatibility → mitigate by pinning known-good versions and, if blocked, temporarily
  relaxing a single package rather than downgrading the whole runtime. Revisit via a new ADR if a
  hard blocker appears.
- Follow-ups: record chosen infra image tags (Postgres/Keycloak/Kafka/Vault/SigNoz) in
  `deploy/compose` and reference them from `FRD-000`.
