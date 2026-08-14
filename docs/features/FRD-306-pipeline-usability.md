# FRD-306 — Pipeline rework: LLM routing, explainable filter, dry-run

> Phase: 3 · Status: **Done** · Owner: Vadim Scheibe
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
- `POST /v1beta/pipeline:dryRun` — builder utility that evaluates an inline pipeline against a
  sample system+user prompt (runs real steps incl. LLM; no generation, no stored data).
  **Authenticated** since ADR-0007 (it reaches the configured providers with caller-supplied
  prompts) and bounded: 8 000 chars per sample field, 32 steps.
- **Governed like a request since 2026-08-11** (`FRD-308`). Those bounds were described here as
  meaning "a single call cannot be turned into a free LLM relay", and it was measured that they
  did not: a caller posted a pipeline naming **any** model as its classifier and the gateway called
  it — no use case, no release check, no budget, no rate limit and no audit row. It now takes a
  **required** `use_case`, refuses a caller who may not act on it (`use_case_refusal`), and refuses
  any model that use case has not been released. The model it *infers* when a pipeline names none
  comes from the release too.

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
Authenticated dry-run — **done** in ADR-0007 (the SPA sends its Keycloak bearer to `/gw`, so the
gateway needs `AIRA_OIDC_ENABLED` pointed at the same realm). Remaining: parallel branches;
category routing cache; richer live simulation (model-aware allow-check preview).


## The dry run had the permission controls and not the spending ones

Asked after the access round: *"can you check the same for budgets and rate limits?"* — the same
question, of a different rule: **which paths does it actually reach.**

`pipeline:dryRun` was rewritten once already, because a caller could post a pipeline naming any
model and have the gateway call it — *"no use case, no release check, no approval check, no budget,
no rate limit, and no audit row."* The rewrite restored authorisation, the release check and the
audit row. It did not restore the other two, and nothing noticed: the two it fixed are about
**permission**, the two it left are about **spending**, and the module's own docstring lists all
four while the code implemented half.

Measured by removing the fix again: a use case with `limit_requests: 0`, a rate limit of one per
minute, and an outright suspension by IT Security each let a dry run through and call a model.

`guard_before_work` — a suspension, the rate limits, and whether the budget is already exhausted —
is taken before the engine runs. One call rather than three: the bundle exists so that the order is
not a call site's to assemble (`FRD-126`), and it is the same bundle the served path takes before
its pipeline for the same reason (`FRD-405`: refusing *after* the classifier ran cost 72 400 tokens
across seven refusals in one measurement).

The guard is structural as well as behavioural. `test_every_spender_takes_the_gate.py` reads every
module under `api/` and fails any that reaches a provider without taking the gate — over the
category rather than over the file that was wrong, with three exemptions named and reasoned
(`serving.py` **is** the layer; `providers.py` reads an in-process property; `incidents.py` is
`FRD-506`'s reachability check, which is never a generation and has no use case to charge).

What the audit did **not** find, checked in the same pass: budgets and rate limits carry the same
fields on both planes; `upserted` and `deleted` are emitted, routed and applied for both, in both
directions (`test_outbox_routing.py`); both scopes are evaluated together on every verb, because
the gate is taken once before the verb branch; and `each_member` needs no membership list at all —
the caller is the key — so it behaves identically whether somebody is a member by group or by name.
