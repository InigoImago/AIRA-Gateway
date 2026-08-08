# ADR-0013 — The gateway provides auditable model access, not agents

- **Status:** Accepted
- **Date:** 2026-08-06
- **Deciders:** Vadim Scheibe
- Settles the open question in `ADR-0012` / `FRD-115` §11. Governs what gets built next.

## Context

Two platforms are in scope — the Gemini Enterprise platform's Model Garden and Microsoft Foundry —
and both offer more than model inference. Model Garden sits inside an **agent** platform (data
stores, grounding, orchestration, server-side conversation state); Foundry has agent, evaluation and
fine-tuning surfaces of its own.

So a question that has been implicit through the whole parity programme becomes explicit: does AIRA
consume those surfaces, or only the models underneath them?

**Decided: direct model access.** And with it a sentence that is worth more than the decision,
because it settles the next twenty questions as well:

> The gateway's job is to provide **auditable brains** for AI use cases.

That is a scope statement, and a sharp one. It says what AIRA *is* — a governed, evidenced path to
model inference — and by omission it says what AIRA is not.

## The test

Every future feature request meets one question:

> **Does this make model access better governed and better evidenced, or does it make the gateway
> think for the use case?**

The first is in scope. The second belongs in the use case, where the domain knowledge is.

The reason to write the test down rather than rely on judgement: the second kind of request always
arrives disguised as the first, and always with a good argument. "Just let the gateway keep the
conversation history — every team is reimplementing it." "Just let it retrieve from our documents —
it already sees the prompt." Each is individually reasonable and collectively turns a control point
into an application platform, with the control point's uptime requirements and none of its focus.

## In scope — the "auditable" half

These are the things that make model access governed and evidenced, and they are what AIRA already
is or is becoming:

- **Who** — attribution to a use case and a subject (`FRD-102`), from a token or a bound key.
- **Whether** — allow-lists, prompt-injection screening, rate limits, budgets, spend ceilings
  (`FRD-300`, `FRD-401`, `FRD-405`, `FRD-403`).
- **Which brain, and where it ran** — routing, fallback, and the *evidence* of what actually
  answered: model, vendor, region, hosting (`ADR-0011`, `ADR-0012`, `FRD-115`).
- **What it cost** — priced per request, unpriced traffic counted apart (`FRD-403`).
- **What happened** — request log, tracing, reporting, retention, redaction (`FRD-103`, `FRD-105`,
  `FRD-601`, `FRD-404`, `FRD-406`).
- **One shape across vendors** — the canonical core, so a use case is not rewritten when a model
  changes (`FRD-100`, `ADR-0011`).

## Out of scope — the "not agents" half

- **The platforms' agent APIs.** Grounded answers, data stores, server-side conversation state.
  These produce answers we did not route and cannot fully account for: the tokens, the retrieved
  content and the intermediate model calls happen inside a service we do not see. Consuming them
  would mean recording "an agent answered" — which is precisely the loss of evidence the gateway
  exists to prevent.
- **Retrieval, embeddings storage, vector search.** We *serve* embeddings (`FRD-113`); we do not
  store or search them.
- **Conversation state.** Every request is complete in itself. History is the caller's, which is
  also what keeps retention (`FRD-404`) meaningful — we cannot promise to delete what we also keep
  as state.

  **The same distinction applies to caching (2026-08-08, `FRD-133`).** A *cache handle* — Google's
  `cachedContent` — is content the provider stores on our behalf and we later refer to: that is
  server-side state, and it stays refused. A *cache marker* on content the caller sends in full
  every time — Anthropic's `cache_control` — leaves the request complete in itself and is therefore
  not conversation state at all. One is a boundary, the other is a price.
- **Tool and function execution.** The gateway may pass a tool definition through (and uses one
  internally for structured output on Anthropic, `FRD-119` §5.5) but never *executes* anything.
  Executing a caller's tool would make the gateway a code-execution service inside the credential
  boundary.

  **Clarified 2026-08-08.** The implementation refused `tools` outright and cited *this ADR* as the
  reason, which reads — to a reader arriving at that error message — as "function calling is closed
  by decision". It is not: **carrying a declaration is in scope, executing anything is not**, and
  the paragraph above always said so. The real reason for the refusal was that `CanonicalRequest`
  had nowhere to put one, which is a capability gap rather than a boundary. Refusing rather than
  silently ignoring was still right (`FRD-124`). `FRD-131` builds the capability and corrects the
  message. Keeping the two apart matters: a capability gap gets closed, a boundary does not.
- **Prompt authoring, chaining, workflow orchestration.** The pipeline (`FRD-300`) is a
  *governance* pipeline — filter, allow-check, route — not a workflow engine. That distinction is
  the one most likely to erode, because each new step looks like the last one.
- **Content understanding.** `FRD-121`'s document conversion sits on the far side of this line and
  is marked accordingly; its own recommendation is to not build it first, and this ADR is the
  reason.

## Consequences

- Positive: a clear answer to "can't the gateway also…?", and a reason that does not depend on
  effort estimates.
- Positive: `FRD-115` §11 and `FRD-119` §11 are closed. The Vertex publisher endpoints are the
  target; the agent surface is not.
- Positive: the requirement list gets shorter, not longer, as vendors add features — most of what
  they add is above our line.
- Negative: use cases needing retrieval or memory build or buy it themselves. That is the correct
  trade: they have the domain knowledge, and a generic implementation here would serve none of them
  well while making the gateway everyone's critical path for everything.
- **Follow-up, and it is a real one:** "auditable" is a claim, and a review on 2026-08-06 found four
  places where the current audit trail does not support it — most importantly that a **refused**
  request leaves no record at all. `FRD-122` closes them. A gateway that only records what it
  served is not auditable; it is merely instrumented.
