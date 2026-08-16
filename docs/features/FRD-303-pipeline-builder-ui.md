# FRD-303 — Pipeline builder UI (clickable graph)

> Phase: 3 · Status: **Done** · Owner: Vadim Scheibe
> Related: `docs/ROADMAP.md` Phase 3; builds on FRD-300 (engine) + FRD-303 backend CRUD.

## 1. Summary
An Angular screen to author a use case's pre-dispatch pipeline as a **clickable graph**: nodes flow
`Request in → steps… → Dispatch → fallback…`. Clicking a node opens an inspector to configure that
step; steps can be added/reordered/removed. Saving `PUT`s the config, which Management distributes to
the gateway (FRD-300/303 backend).

## 2. Scope
- Route `use-cases/:slug/pipeline` (lazy). Loads via `UseCaseService.getPipeline`, saves via
  `savePipeline`.
- Graph column: endpoint/step/dispatch/fallback nodes with connectors; selected-node highlight;
  per-step up/down/remove; add-step buttons per type.
- Inspector (right, sticky) per step type:
  - `injection_filter`: mode (heuristic|llm), on-detection (block|flag), optional classifier model.
  - `model_route`: ordered rules (`under chars` + target model), add/remove.
  - **Every model field is a dropdown over the use case's released models** (`FRD-308`, 2026-08-11).
    It was free text, which offered exactly what the server refuses and invited naming a model the
    use case has no right to. The fallback chain is a multi-select, because it is several in order.
  - fallback node: fallback models (comma list, ordered).
- **The dry run asks where to enter** (2026-08-16). It used to send no model, so the gateway
  inferred one — and `_model_the_pipeline_is_about`'s own comments record three wrong guesses in a
  row, each reported back as `effective_model` where a builder reads it as a decision somebody
  made. A picker over the released models, defaulting to *let the gateway choose* because a
  pipeline whose requests name their own model is the ordinary case. Blank omits the field rather
  than sending an empty one, which the gateway would have to refuse as a model name.
- Zoneless-safe: state in signals; edits are immutable `update()`s (`structuredClone` + `set`).
- Entry point: “Edit pipeline →” from the use-case detail.

## 3. Testing & Acceptance
- Vitest: service get/save; component renders graph endpoints, renders a loaded config, and
  add-step→save builds the expected pipeline JSON. `ng build` + Prettier gates green.
- Acceptance: build a filter+route pipeline in the UI, save, and see the gateway enforce it
  (blocks an injection prompt, reroutes a short prompt) after Kafka propagation.

## 4. Later
Drag-and-drop reordering, parallel branches (the engine is ordered today), live validation against
the provider's model list, and a dry-run/preview against a sample prompt.
