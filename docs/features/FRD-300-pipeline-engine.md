# FRD-300 — Pre-dispatch pipeline engine (filter · routing · fallback)

> Phase: 3 · Status: **In progress** · Owner: Vadim Scheibe · Last updated: 2026-08-04
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
- Step types: `injection_filter` (`heuristic`|`llm`, action `block`|`flag`), `allow_check` (model
  allow-list), `model_route` (rule-based, incl. cost/length-based rerouting).
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
