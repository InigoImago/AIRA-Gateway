# FRD-300 — Pre-dispatch pipeline engine (filter · routing · fallback)

> Phase: 3 · Status: **Done** (engine + distribution + builder UI) · Owner: Vadim Scheibe
> Related: `docs/PRD.md` (pre-dispatch pipeline); `docs/ROADMAP.md` Phase 3; builds on FRD-100/102/103/304.
> Companion FRDs: `FRD-301` (routing/rerouting details), `FRD-302` (fallback), `FRD-303` (builder UI).

## 1. Summary
A **per-use-case, config-driven pipeline** runs in the gateway on every request **before dispatch**:
ordered steps can **filter** (prompt-injection detection — heuristic or **LLM-backed**), **allow-check**
the requested model, and **route/re-route** to a cheaper or different model; dispatch then follows a
**fallback** chain when an upstream fails. Config is authored in Management (FRD-303) and distributed
to the gateway read-model over Kafka (like use cases, FRD-204). Default (no config) = pass-through,
so existing behavior is unchanged.

## 2. Goals & Non-Goals
**Goals**
- Deterministic engine over the canonical request: `steps[]` + a dispatch `fallback_models[]`.
- Step types: `injection_filter` (`heuristic`|`llm`, action `block`|`flag`), `model_route`
  (rule-based, incl. cost/length-based rerouting).
- **`allow_check` was a third step type and was removed on 2026-08-11** (`FRD-308`). It ran once,
  before routing, against the model the *caller* named — measured, a `model_route` step and a
  fallback chain both reached a forbidden model and were served 200. Which models a use case may
  call is now a property of the use case, checked at every hop like every other dispatch
  condition.
- LLM filter uses the provider abstraction (hermetic in tests via the mock/stub provider).
- Wired into `generateContent`/`streamGenerateContent`; decisions recorded on the request log +
  trace (`aira.pipeline.*`). Blocked requests return a shaped Gemini error.
- Gateway read-model table `pipeline_configs`; idempotent consumer applies `pipeline.upserted/deleted`.

**Non-Goals (later)**
- Management CRUD + graph builder UI (`FRD-303`). Parallel step execution (initial engine is ordered/
  sequential; the config models a graph the UI renders, executed as an ordered walk). Embeddings
  filtering.

## 3. Functional Requirements
- **FR-1**: Load the pipeline for the request's use case; none → pass-through.
- **FR-2 Filter**: classify the last user message; `block` → reject (Gemini `INVALID_ARGUMENT`), `flag`
  → annotate + continue. Heuristic (patterns) and LLM (classification prompt to a provider).
- **FR-3 Allow-check**: reject models outside the allow-list (`PERMISSION_DENIED`).
- **FR-4 Route**: first matching rule overrides the model (unconditional override or `if_under_chars`
  cost rerouting).
- **FR-5 Fallback**: `generateContent` tries `[model, *fallback_models]` until one succeeds; streaming
  uses the routed model (no mid-stream fallback).
- **FR-6**: The effective model + pipeline decisions are attributed on the request log and trace.
- **FR-7 Start model** (`ADR-0020`, 2026-08-16): a pipeline may declare where a request **enters**
  it when the caller names no model. Configuration about the pipeline rather than a step in it — the
  same shape `FRD-308` settled for `allowed_models` — and validated like every other model a
  pipeline names: released to the use case, or the save is refused. Blank is a real state, meaning
  *only a caller who names a model enters here*, and is what every pipeline written before this
  requirement is in. It is deliberately **not** part of `Pipeline.is_empty`: a pipeline with a start
  model and no steps still runs nothing, and treating it as non-empty would put every request
  through an engine with nothing to do. Two readers: the question catalogue (`FRD-504`), which
  cannot run a use case without one, and the dry run, which now prefers a declaration to the three
  guesses `_model_the_pipeline_is_about` had been making — each one wrong in production and each
  reported back as `effective_model`, where a builder reads it as a decision somebody made.

## 4. Design
- `pipeline/config.py` — `Pipeline`, `PipelineStep`, `StepType`; `Pipeline.from_dict` parses read-model JSON.
- `pipeline/classifiers.py` — `InjectionClassifier` protocol; `HeuristicInjectionClassifier`,
  `LlmInjectionClassifier(provider, model)`.
- `pipeline/engine.py` — `PipelineEngine(registry)`; `run(request, ctx) -> PipelineOutcome` or raises
  `PipelineRejected`.
- `pipeline/store.py` — loads a `Pipeline` from `pipeline_configs` by use case.
- Dispatch: `dispatch_with_fallback(registry, request, fallbacks)` for non-stream generate.
- Distribution: `aira.pipelines` topic; `pipeline.upserted/deleted` → `PipelineConfigRead`.

## 5. Testing & Acceptance
- Hermetic unit tests for each step + classifier, engine orchestration, fallback, and the consumer.
- Route-level: a configured pipeline blocks an injection prompt, reroutes a short prompt to the cheap
  model, and falls back when the primary upstream errors. Coverage gate stays green.
