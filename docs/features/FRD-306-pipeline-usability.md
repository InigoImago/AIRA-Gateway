# FRD-306 — Pipeline rework: LLM routing, explainable filter, dry-run

> Phase: 3 · Status: **Done** · Owner: Vadim Scheibe · Last updated: 2026-08-04
> Supersedes the length-based routing in FRD-300/301. Driven by user feedback that the builder was
> opaque and routing was too simplistic.

## 1. Problem
- Routing decided only on `if_under_chars` (last user message length) — ignored the system prompt and
  actual content.
- The heuristic filter's patterns were hidden; operators couldn't see or add patterns.
- The LLM classifier was unexplained and unconfigurable in the UI.
- No way to see what a pipeline does for a concrete request.

## 2. Changes
**Engine (gateway)**
- `model_route` → **LLM-classifier routing**: the classifier reads **system + user** text, picks one
  of the configured `categories` (`{name, description, model}`), and routes to its model;
  `default_model` when unmatched; fails open.
- `injection_filter`: heuristic exposes `BUILTIN_INJECTION_PATTERNS` and accepts custom `patterns`
  (invalid regex → literal), `use_builtins` toggle, and `scope` (`user` | `system_user`); LLM mode
  takes a configurable `model` + `instruction`.
- `engine.dry_run()` → full per-step **trace** (passed/flagged/blocked/allowed/rejected/rerouted/
  unchanged) without dispatching.
- `POST /v1beta/pipeline:dryRun` — unauthenticated builder utility that evaluates an inline pipeline
  against a sample system+user prompt (runs real steps incl. LLM; no generation, no stored data).

**Builder (frontend)**
- Inspector redesigned per step with **inline help**, visible built-in patterns + custom patterns,
  scope, LLM model/instruction, and a **categories** editor for routing.
- **Test panel**: sample system + user prompt → **live client-side preview** of deterministic steps
  (heuristic patterns) + a **Dry-run** button hitting the gateway for the full trace (incl. LLM).
- Dev proxy `/gw` → gateway `:8001`.

## 3. Testing & Acceptance
- Gateway: engine (LLM routing, scope, custom patterns, dry-run) + endpoint tests; ~100% on pipeline
  modules. Frontend: service dry-run + live-preview component tests. Gates green.
- Acceptance: in the builder, add an LLM router with categories + a filter, test with a sample prompt
  (see the live preview + dry-run trace), save, and the gateway enforces it after propagation.

## 4. Later / hardening
Authenticated dry-run for production; parallel branches; category routing cache; richer live
simulation (model-aware allow-check preview).
