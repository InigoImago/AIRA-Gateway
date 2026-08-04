# ADR-0001 — Management UI: Angular + Django REST Framework

- **Status:** Accepted
- **Date:** 2026-08-04
- **Deciders:** Vadim Scheibe

## Context
The Management & Monitoring component needs heavy role-based access control (5 roles, object-level
per-use-case scoping), rich interactivity (a pre-dispatch **pipeline builder** with sequential/
parallel branches and fallback, security dashboards), and enterprise standardization. A stated
preference was "as much Python as possible", but this conflicts with the desire for an
enterprise-grade, highly interactive UI.

## Options considered
- **Django + HTMX/Alpine (pure Python, server-rendered)** — fastest to build, all Python, Django
  admin for free. Weak spot: a truly graphical pipeline/node editor and complex SPA interactions.
- **Reflex / NiceGUI (pure Python reactive)** — keeps Python, decent dashboards, smaller ecosystem
  and less proven for complex enterprise RBAC surfaces.
- **Streamlit** — great for demos/monitoring, poor for complex RBAC/multi-page/stateful editors.
- **Angular (TypeScript SPA) + Django REST Framework backend** — enterprise-standard frontend;
  Angular CDK + a flow/graph lib enable a real graphical pipeline builder; Django keeps ORM,
  migrations, `django-guardian` object-level RBAC, and admin as an internal backoffice.

## Decision
Use **Angular + Django REST Framework**. Django becomes a REST API backend (not template-rendered);
Angular is the SPA frontend, authenticating via OIDC against Keycloak; Django validates JWTs and
enforces object-level RBAC.

## Consequences
- Positive: enterprise-standard, strongly typed frontend; graphical pipeline builder is feasible
  (Angular CDK drag&drop, `rete.js`/`ngx-graph`); Django RBAC/ORM/admin retained; scales to teams.
- Negative / trade-offs: introduces a second language (TypeScript) — breaks the "all Python" wish;
  separate frontend build/deploy; more upfront UI work (no "admin templates for free" for the main
  surface). The Gateway stays FastAPI, so the codebase spans FastAPI + Django + Angular.
- Follow-ups: define the DRF ↔ Angular contract (OpenAPI), OIDC flow, and pick the graph library
  in the Phase 3 pipeline-builder FRD.
