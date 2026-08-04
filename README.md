# AIRA Gateway — AI REST API

**AIRA Gateway** is an enterprise-grade AI gateway: a single, unified, provider-agnostic REST API
in front of multiple upstream LLM platforms, with centralized authentication, authorization,
routing, governance, observability, and security.

It consists of two self-developed components plus open-source infrastructure:

1. **Gateway API** (FastAPI) — the data plane: unified API (**Gemini-compatible first**, OpenAI
   later), auth, attribution, routing/fallback, pipeline enforcement, request/response persistence,
   tracing.
2. **Management & Monitoring** (Angular + Django REST Framework) — the control plane: use-case
   self-service, pipeline builder, budgets, anomaly rules, IT Security console, governance views.

Infrastructure: **PostgreSQL**, **Keycloak** (SSO), **Apache Kafka** (event bus), **HashiCorp
Vault** (secrets). Runs locally via **Docker Compose**; Kubernetes/Helm planned.

## Documentation
- [`docs/PRD.md`](docs/PRD.md) — Project Requirements Document
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — Phased delivery plan
- [`docs/features/`](docs/features/) — Feature Requirement Documents (FRDs)

## Status
Planning phase — see the roadmap. Documentation and code are written in **English**.
