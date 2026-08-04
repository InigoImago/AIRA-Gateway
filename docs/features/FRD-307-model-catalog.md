# FRD-307 — Model catalog + model pickers in the builder

> Phase: 3 · Status: **Requested (backlog)** · Owner: Vadim Scheibe · Last updated: 2026-08-04
> Origin: user feature request — "an overview of all models, and be able to pick them in the
> injection filter / allowed-model config; currently you have to type names blindly."

## 1. Problem
The pipeline builder asks operators to type model names as free text (classifier model, allow-list,
routing category targets, fallback chain). There is no visible list of which models the gateway can
actually serve, so names are guessed and typos silently produce mis-routing or 404s at runtime.

## 2. Goal
Surface the gateway's real model list and let operators **pick** models instead of typing them:
- A **Models overview** (all available models + supported methods + provider).
- **Model pickers** wherever a model is chosen in the builder: `injection_filter` LLM classifier
  model, `allow_check` allow-list, `model_route` classifier/category/default models, fallback chain.
- Keep free-text entry as a fallback (a model may be configured later than the UI is refreshed).

## 3. Design
- **Source of truth**: the gateway already exposes `GET /v1beta/models` (built from
  `ProviderRegistry`). The builder fetches it via the existing `/gw` proxy → a typed
  `ModelService.list()` returning `{name, supportedMethods}[]`.
  - Optional: a management passthrough `GET /api/v1/models` (proxying the gateway) so the SPA has a
    single origin and it works without the `/gw` dev proxy in prod. Decide during implementation.
- **Models overview**: a lazy route `/models` + nav item; a table of name / methods / provider.
  Governance/admin visible; read-only.
- **Builder pickers**:
  - `allow_check.models`: a checklist of available models (multi-select) with a free-text add.
  - `injection_filter.model` (llm), `model_route.model`, category `model`, `default_model`,
    `fallback_models`: `<select>`/combobox populated from the catalog, each allowing a custom value.
- Model list is fetched once on builder load and cached; a "refresh" affordance re-fetches.

## 4. Non-goals
Per-model metadata beyond name/methods (pricing, context window, health) — later. Editing/registering
models from the UI (models come from gateway provider config / Vault) — out of scope.

## 5. Testing & Acceptance
- `ModelService.list()` unit test (hits `/gw/v1beta/models`).
- Builder renders dropdowns populated from the catalog; selecting a model writes the name into config;
  custom values still accepted. Models overview renders the list. Gates green.
- Acceptance: open the builder, pick the classifier/allow/route models from dropdowns (no typing),
  save, dry-run confirms the chosen models — and the Models page lists everything the gateway serves.

## 6. Notes
Small, well-scoped, and unblocks correct pipeline authoring. Depends only on the existing
`/v1beta/models` endpoint (FRD-100) and the pipeline builder (FRD-303/306).
