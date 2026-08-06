# ADR-0011 — Upstreams: platform, dialect, and what a model name means

- **Status:** Accepted
- **Date:** 2026-08-06
- **Deciders:** Vadim Scheibe

## Context

Three vendor platforms are now in scope:

| Platform | Models | Wire format | Credential |
|---|---|---|---|
| Generative Language API | Gemini | Gemini | API key (dev only) |
| Vertex AI / Model Garden | Gemini, **Anthropic** | Gemini **and** Anthropic Messages | GCP service account |
| **Microsoft Foundry** | Azure OpenAI, Microsoft's own (Phi/MAI), others | **OpenAI Chat Completions** | Entra ID / managed identity, or key |

`FRD-304` built the first as a single class: URL, authentication, body mapping and model list in one
adapter. That was right for one vendor. `FRD-115`/`FRD-119` split it once — a Vertex *transport*
with a Gemini and an Anthropic *dialect* above it — because the two vendors share a platform but not
a format.

Foundry is the case that decides whether that split was a local fix or the actual shape. It brings
three things the first two did not:

- **A third wire format** (OpenAI), which is also the format `FRD-106` would need if the deferred
  OpenAI *surface* is ever built. The mapper arrives either way, from the other direction.
- **A third credential mechanism** (Entra ID / managed identity), after an API key and a GCP service
  account.
- **A different notion of what a model is.** Azure addresses a *deployment* — a customer-named
  instance of a model in a resource in a region. Two Azure resources can both have a deployment
  called `gpt-4o` pointing at different versions. The name a caller uses and the name the platform
  uses are no longer the same string.

With two vendors any of this can be absorbed by a conditional. With three it has to be a structure,
or the fourth rewrites the third.

## Options considered

- **Option A — one adapter per platform.** Today's shape extended: `FoundryAdapter` alongside
  `VertexAdapter`. *Pro:* nothing to design. *Con:* the OpenAI mapping is welded to Azure, so a
  second OpenAI-speaking platform (OpenAI directly, an on-prem gateway, another cloud) rewrites it;
  and three token-acquisition implementations drift.
- **Option B — one adapter per (platform, dialect) pair.** *Pro:* explicit. *Con:* six classes for
  three platforms and three formats, most of them near-duplicates.
- **Option C — separate the platform from the dialect, and make model identity explicit.**
  A *transport* owns everything that is about reaching the vendor's cloud (endpoint, credential,
  retries, quota errors). A *dialect* owns everything that is about the vendor's API shape
  (request and response bodies, streaming events, usage fields, capability mechanisms). An
  *upstream* is a composition of the two, plus a resolver from the caller's model name to the
  platform's addressing.

## Decision

**Option C.** Three concepts, each with one responsibility:

```
Upstream = Transport × Dialect × ModelResolver

Transport   Vertex        │ Foundry            │ GenerativeLanguage
            OAuth (SA)    │ Entra ID / key     │ API key
            EU regions    │ Azure regions      │ global

Dialect     Gemini        │ Anthropic          │ OpenAI
            contents[]    │ messages+system    │ messages[]
            responseSchema│ forced tool call   │ response_format json_schema
            thinkingBudget│ thinking.budget    │ reasoning_effort

Resolver    name → (endpoint, addressing, underlying model for pricing)
```

Vertex composes {Vertex transport} × {Gemini dialect, Anthropic dialect}. Foundry composes
{Foundry transport} × {OpenAI dialect}. The OpenAI dialect is then reusable by any platform that
speaks it, which is the point.

Three rules follow, and they are the substance of this ADR:

**1. Credential acquisition is one abstraction with three implementations.**
All three platforms need the same behaviour — obtain a token, cache it, refresh ahead of expiry,
collapse concurrent refreshes into one, keep serving a valid token through a failed refresh. Only
the acquisition differs. `FRD-115` §5.2 specifies that behaviour; it becomes a shared
`TokenSource` with a Google, an Entra and a static-key implementation. Writing it three times means
getting the refresh race right three times.

**2. A caller names a model. The platform's addressing is catalog configuration.**
A use case's pipeline configuration must never contain an Azure deployment name or a Vertex
publisher path. The caller says `gpt-5`; the catalog (`FRD-114`) says that is the Foundry transport,
resource *X*, deployment *Y*, priced as underlying model *Z*. Two consequences:

- The **caller-facing name is stable across a redeployment** on the vendor's side. An Azure
  deployment renamed does not invalidate every use case's configuration.
- **Pricing follows the underlying model, not the deployment.** `FRD-403` prices by model name;
  without this separation a deployment called `production` would be unpriceable, and unpriced
  traffic is counted apart rather than as free — so the mistake would be quiet.

**3. Capability declarations say *whether*, never *how*.**
Three vendors now produce structured output by three unrelated mechanisms — a schema parameter, a
forced tool call, a `json_schema` response format — and control reasoning by two shapes (a token
budget, an effort level). `FRD-114`'s flags stay booleans; the mechanism lives in the dialect. This
was already the rule (`FRD-119` §5.5); Foundry is what makes it non-negotiable, because the third
mechanism is where a "how" leaking into the catalog would have become permanent.

## Consequences

- Positive: a fourth platform is a transport plus, usually, an existing dialect. A second
  OpenAI-speaking platform is a transport alone.
- Positive: **the OpenAI dialect arrives as an upstream regardless of `FRD-106`.** Once
  canonical ⇄ OpenAI exists in one direction, the deferred OpenAI *surface* is a much smaller piece
  of work than when it was deferred. That is a planning fact worth revisiting then, not now.
- Positive: the residency mechanism generalises. `FRD-115`'s allowed-region check is not
  Google-specific; Azure regions carry the same requirement and use the same allow-list.
- Negative: one more indirection than the code needs today. Justified by three platforms, not by
  two — and this ADR exists so the indirection is not later mistaken for accidental.
- Negative: the model resolver adds a lookup between "what the caller said" and "what we call". It
  is a read-model lookup already on the path for pricing, so the cost is a dictionary access.
- Follow-up: `FRD-120` specifies Foundry. `FRD-115` gains the shared `TokenSource`. `FRD-114`
  gains platform addressing and the underlying-model reference. `ADR-0005`'s "OpenAI later" should
  be re-read once the dialect exists.
