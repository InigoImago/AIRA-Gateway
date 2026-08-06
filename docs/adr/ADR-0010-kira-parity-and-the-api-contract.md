# ADR-0010 — KIRA parity: bring the contract along, or move the clients?

- **Status:** Proposed — **the contract question below needs a decision before `FRD-107` starts.**
  Everything else in the parity programme is contract-independent and may proceed.
- **Date:** 2026-08-06
- **Deciders:** Vadim Scheibe

## Context

AIRA is the successor to **KIA-KIRA-API** (`kira_api.md`, v0.1.2), and is meant to carry all of its
functionality. A review of the predecessor's requirements against the code as it stands produced a
clear split.

**In breadth, AIRA is well ahead.** Use cases with object-level RBAC, self-service API keys,
budgets down to spend, cross-instance rate limits, a configurable pre-dispatch pipeline, Kafka
config distribution, a management UI, per-use-case retention, cost reporting — KIRA has none of
this, and none of it is at risk.

**In the core request path, AIRA is behind.** `CanonicalMessage` carries exactly one field,
`text: str`, and the Gemini surface's `Part` requires `text`, so a request carrying `inlineData` is
not merely unmapped — it is rejected at the door with a 400. KIRA accepts documents and images in
fourteen MIME types, controls the model's thinking budget, and forces JSON output against a
schema. None of that exists here. There is also a substantive difference in *where the model
runs*: KIRA calls Vertex AI on regional endpoints (`europe-west1`, `eu`) with a service-account
credential; AIRA calls the Generative Language API with an API key from an environment variable.

**Answered 2026-08-06**: EU residency does apply, and access will be through the **Gemini
Enterprise platform's Model Garden**, serving **Gemini and Anthropic** models. That settles the
schedule question — `FRD-115` is required, not optional — and adds one this ADR did not
anticipate: Model Garden gives us two vendors under one credential, and **they do not share a wire
format**. Anthropic models are called through `:rawPredict` and speak the Anthropic Messages API,
with a required `max_tokens`, returned thinking blocks, and no `responseSchema` at all. That is a
second dialect (`FRD-119`), not a second base URL, and it is the first genuine test of `FRD-100`'s
claim that the canonical core is provider-agnostic.

Those two facts frame this ADR. The functional gaps are ordinary engineering, and the FRDs below
cover them. The contract is a decision.

### The contract

KIRA's clients call:

```
POST /kira/api/external/chat
{ "request": {...}, "model_id": 1004, "system_instruction": {...}, "thinking": {...} }
```

AIRA serves:

```
POST /v1beta/models/gemini-2.0-flash:generateContent
{ "contents": [...], "systemInstruction": {...}, "generationConfig": {...} }
```

These are different contracts, not different spellings of one. Even after every KIRA *capability*
exists in AIRA, **no existing KIRA client can point at AIRA without a code change.** ADR-0005 chose
the Gemini shape as the first surface and named OpenAI as the second; KIRA is a third.

## Options considered

- **Option A — AIRA serves a KIRA-compatible surface.** A third API surface (`aira_gateway/api/
  kira/`) mapping the KIRA wire format onto the same canonical core the Gemini surface already maps
  onto, including its error-code taxonomy and its integer `model_id` aliases.
  *Pro:* clients migrate by changing a base URL. A migration that needs no coordinated release on
  the consuming teams' side is a migration that can actually be scheduled, and it lets the new
  governance (budgets, limits, attribution) reach existing traffic immediately rather than after
  every consumer has found time. It also gives a clean cut-over and rollback: both systems answer
  the same requests during the overlap.
  *Con:* a third contract to keep working, forever or until it is deliberately retired — surfaces
  are cheap to add and expensive to remove. KIRA's shape carries decisions we would not repeat
  (integer model IDs, a bespoke error vocabulary, `/streaming-chat` as its own path).

- **Option B — clients move to the Gemini surface.** AIRA gains the missing *capabilities* only;
  every consumer is changed to speak Gemini.
  *Pro:* one contract fewer. The Gemini shape is a published, documented, SDK-supported format;
  KIRA's is ours to explain. Consumers end up on something they can also point at Google directly.
  *Con:* every consuming team must schedule work before it can migrate at all, which in practice
  means the slowest team sets the date for decommissioning KIRA. Until then two gateways run, and
  the new controls apply to only part of the traffic.

- **Option C — a compatibility surface with a stated end date.** Option A, plus the surface is
  documented from day one as transitional: deprecation headers, a sunset date, and reporting that
  shows how much traffic still uses it so the decision to remove it is made on evidence.

## Decision

**Recommended: Option C.**

Option B's cost is not the engineering, it is the coupling of our timeline to everyone else's.
The whole point of putting budgets, rate limits and attribution in front of the models is that they
apply to *the traffic*, and traffic that has not migrated is traffic that is not governed. Option A
buys that immediately for the price of a mapper — and the mapper is genuinely thin, because the
canonical core it targets already exists and already has a second surface mapping onto it.

What Option A gets wrong is only ever the ending: a compatibility surface with no stated end date
is a permanent one. So the surface ships with a sunset date and with its own usage visible in
reporting, and the decision to remove it is made against a number rather than a feeling.

**This is a recommendation, not yet a decision.** It is written down so the choice is explicit;
`FRD-107` stays in *Blocked* until it is made.

## Consequences

- Positive: existing KIRA consumers migrate by configuration. The governance layer covers all
  traffic from the cut-over rather than from the last consumer's release. Both gateways can answer
  the same request during the overlap, which makes rollback real rather than theoretical.
- Negative: a third surface. Its error taxonomy and integer model IDs leak KIRA's design decisions
  into our model catalog (`FRD-114` must carry a numeric alias that has no other purpose).
- **The capability work does not depend on this.** Documents, thinking, structured output,
  embedding options, model metadata, Vertex AI and Vault all sit below the surface layer and are
  needed under either option. They are specified independently and can start now.
- Follow-up: if Option B is chosen instead, `FRD-107` is dropped and the integer-alias requirement
  in `FRD-114` goes with it. Nothing else changes.

## The parity programme

| FRD | What | Depends on |
|---|---|---|
| [`FRD-110`](../features/FRD-110-multimodal-content.md) | Documents and images in a request | — |
| [`FRD-111`](../features/FRD-111-thinking-control.md) | Thinking modes and budgets | `FRD-114` |
| [`FRD-112`](../features/FRD-112-structured-output.md) | `responseSchema` — forced JSON output | — |
| [`FRD-113`](../features/FRD-113-embedding-options.md) | Task types, batches, dimensions | `FRD-114` |
| [`FRD-114`](../features/FRD-114-model-capability-metadata.md) | What a model can do and where its limits are | — |
| [`FRD-115`](../features/FRD-115-vertex-ai-upstream.md) | Vertex AI / Model Garden in the EU | **required** — residency confirmed |
| [`FRD-119`](../features/FRD-119-anthropic-on-vertex.md) | Anthropic models: the second dialect | `FRD-115`, `FRD-114` |
| [`FRD-120`](../features/FRD-120-microsoft-foundry.md) | Microsoft Foundry: the third platform *(planned)* | `ADR-0011`, `FRD-115` |
| [`FRD-121`](../features/FRD-121-document-normalisation.md) | Document conversion for models that cannot read documents *(optional)* | `ADR-0012`, `FRD-110` |
| [`FRD-116`](../features/FRD-116-vault-secrets.md) | Secrets actually read from Vault | — |
| [`FRD-117`](../features/FRD-117-diagnostics-and-compatibility.md) | Version info, upstream health, CORS, OpenAPI 3.0, trace header | — |
| [`FRD-118`](../features/FRD-118-federated-identity.md) | Several Keycloak backends, groups from UserInfo | — |
| [`FRD-602`](../features/FRD-602-report-export.md) | CSV export of the usage report | `FRD-601` ✓ |
| [`FRD-107`](../features/FRD-107-kira-compatible-surface.md) | The KIRA wire format itself | **this ADR**, 110–114 |
