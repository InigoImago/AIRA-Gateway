# FRD-300 — Pre-dispatch pipeline engine (filter · routing · fallback)

> Phase: 3 · Status: **Done** (engine + distribution + builder UI) · Owner: Vadim Scheibe
> Related: `docs/PRD.md` (pre-dispatch pipeline); `docs/ROADMAP.md` Phase 3; builds on FRD-100/102/103/304.
> Companion FRDs: [`FRD-302`](FRD-302-model-fallback.md) (the fallback chains — the use case's, a stream's and each step's own), [`FRD-303`](FRD-303-pipeline-builder-ui.md) (builder UI). `FRD-301` was never written: routing is specified here, in FR-4 and §4.

## 1. Summary
A **per-use-case, config-driven pipeline** runs in the gateway on every request **before dispatch**:
ordered steps can **filter** (prompt-injection detection — heuristic or **LLM-backed**), **allow-check**
the requested model, and **route/re-route** to a cheaper or different model; dispatch then follows a
**fallback** chain when an upstream fails. Config is authored in Management (FRD-303) and distributed
to the gateway read-model over Kafka (like use cases, FRD-204). Default (no config) = pass-through,
so existing behavior is unchanged. *As built, the step types are `injection_filter`, `model_route`
and `pii_filter` (`FRD-309`); allow-check is gone (§2).*

## 2. Goals & Non-Goals
**Goals**
- Deterministic engine over the canonical request: `steps[]` + a dispatch `fallback_models[]`.
- Step types: `injection_filter` (`heuristic`|`llm`, action `block`|`flag`), `model_route`
  (rule-based, incl. cost/length-based rerouting). *As built, `model_route` is an LLM classifier
  over named categories with a default model (`FRD-306`); the rule-based and length-based rerouting
  is gone.*
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
  filtering. *As built: the builder is `FRD-303`/`FRD-306`, and an embedding batch runs through the
  steps (`pipeline/engine/texts.py`). Parallel execution stays a non-goal — the engine is an
  ordered walk, and every guarantee about what a step sees rests on that order.*

## 3. Functional Requirements
- **FR-1**: Load the pipeline for the request's use case; none → pass-through.
- **FR-2 Filter**: classify the last user message; `block` → reject (Gemini `INVALID_ARGUMENT`), `flag`
  → annotate + continue. Heuristic (patterns) and LLM (classification prompt to a provider).
- ~~**FR-3 Allow-check**: reject models outside the allow-list (`PERMISSION_DENIED`).~~ Removed
  with the step (§2): the use case's release is checked at every hop instead (`FRD-308`).
- **FR-4 Route**: first matching rule overrides the model (unconditional override or `if_under_chars`
  cost rerouting). *Superseded by `FRD-306`:* a classifier picks one of the configured categories,
  each naming a model, and `default_model` serves when none matches; `if_under_chars` no longer
  exists.
- **FR-5 Fallback**: `generateContent` tries `[model, *fallback_models]` until one succeeds; streaming
  uses the routed model (no mid-stream fallback). *Superseded by `FRD-302` and `ADR-0032`:* every
  model access is a chain — a stream falls back until its first chunk is on the wire
  (`stream_with_fallback`), and each step has a chain of its own. Only a fallback mid-answer is
  not offered.
- **FR-6**: The effective model + pipeline decisions are attributed on the request log and trace.

## 4. Design
- `pipeline/config.py` — `Pipeline`, `PipelineStep`, `StepType`; `Pipeline.from_dict` parses read-model JSON.
- `pipeline/classifiers/` — one module per question a step asks a model: `injection`
  (`InjectionClassifier` protocol; `HeuristicInjectionClassifier`, `LlmInjectionClassifier`),
  `routing` (`LlmCategoryRouter`), `redaction` (`LlmRedactor`), and `prompts` (instructions,
  markers, output allowances).
- `pipeline/engine/` — `runner.PipelineEngine(registry)`; `run(request, ctx) -> PipelineOutcome` or
  raises `PipelineRejected`. `steps.StepEvaluator` evaluates one step; `texts` runs the steps over
  an embedding's batch; `outcomes` holds the result types.
- `pipeline/store.py` — loads a `Pipeline` from `pipeline_configs` by use case.
- Dispatch: `dispatch_with_fallback(registry, request, fallbacks)` for non-stream generate.
  *As built, `stream_with_fallback` beside it for streams (`pipeline/dispatch.py`, `FRD-302`).*
- Distribution: `aira.pipelines` topic; `pipeline.upserted/deleted` → `PipelineConfigRead`.

## 5. Testing & Acceptance
- Hermetic unit tests for each step + classifier, engine orchestration, fallback, and the consumer.
- Route-level: a configured pipeline blocks an injection prompt, reroutes a short prompt to the cheap
  model, and falls back when the primary upstream errors. Coverage gate stays green.
