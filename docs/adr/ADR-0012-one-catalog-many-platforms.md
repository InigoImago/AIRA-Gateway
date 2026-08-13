# ADR-0012 — One catalog over many platforms, and what "supports documents" then means

- **Status:** Accepted
- **Date:** 2026-08-06
- **Deciders:** Vadim Scheibe
- Builds on `ADR-0011` (transport × dialect × identity). Governs `FRD-110`, `FRD-114`, `FRD-115`,
  `FRD-119`, `FRD-120`, `FRD-121`.

## Context

The target is one gateway in front of two clouds and at least four model families:

| Family | Reached via | Addressing | Dialect | Hosting |
|---|---|---|---|---|
| Gemini | Vertex / Model Garden | `publishers/google/models/…` | Gemini | managed |
| Claude | Vertex / Model Garden | `publishers/anthropic/models/…` | Anthropic | managed |
| GPT (+ Phi, MAI) | Microsoft Foundry | resource + deployment | OpenAI | managed |
| Nemotron | Vertex / Model Garden | **a numeric endpoint id** | OpenAI (NIM) | **self-deployed** |

Two things fall out of that table immediately, and both were unknowns when `ADR-0011` was written.

**The transport × dialect grid is a real matrix, not a diagonal.** Nemotron on Model Garden runs as
an NVIDIA NIM container exposing an OpenAI-compatible API — so the **OpenAI dialect is needed on the
Vertex transport**, not only on Foundry. `ADR-0011`'s separation stops being a tidiness argument and
starts paying for itself before the third platform is even built.

**A fourth addressing mode appears.** Self-deployed models are not `publisher/model`; they are an
endpoint id in a project, backed by GPU capacity we rent. `ADR-0011` rule 2 (the caller names a
model, the catalog holds the addressing) turns out to have been necessary rather than merely
prudent.

And then the requirement that started the whole programme:

**Callers of the compatibility surface send documents.** "Here is a PDF, answer questions about
it" is a substantial share of the traffic AIRA has to carry (`FRD-110`). Across the four families that capability is
**genuinely not uniform** — Gemini and Claude read PDFs natively, including their visual layout;
a text-only chat model and a NIM-hosted Nemotron cannot see a PDF at all.

So the same request is servable by two of the four families and not by the other two. That is not a
plumbing difference to be smoothed over. It changes the *answer*.

## The governing principle

**Hide the plumbing. Declare the semantics.**

A caller must not have to know which cloud, which credential, which URL shape, which streaming event
vocabulary or which structured-output mechanism is involved. Those are plumbing and the canonical
core exists to absorb them.

A caller — and much more importantly, a **use-case administrator configuring a fallback chain** —
must know when a routing decision would change *what they get back*. Attachments a model cannot see,
a thinking mode it cannot honour, a schema it cannot enforce: these are semantics, and hiding them
produces confident wrong answers rather than errors.

Everything below follows from that one line.

## Decision

### 1. One namespace, one catalog, four addressing modes

Callers name models (`gemini-2.5-pro`, `claude-sonnet-4-5`, `gpt-5`, `nemotron-…`). The catalog
(`FRD-114`) resolves each to transport, dialect, addressing and the underlying model for pricing.
Addressing is opaque to everything above `upstreams/`: a publisher path, a resource + deployment, or
an endpoint id are all just "how this transport finds it".

No use-case configuration, pipeline step or API request ever contains a deployment name, a publisher
path or an endpoint id.

### 2. The capability vocabulary is the contract

One vocabulary across every vendor, declared per model, distributed with the catalog:

```
generate · embed · attachments[media types] · thinking[modes,bounds] ·
structured_output · streaming
```

Flags say **whether**, never **how** (`ADR-0011` rule 3). Three vendors already produce structured
output by three unrelated mechanisms.

Two rules make the vocabulary trustworthy rather than decorative:

- **Undeclared means unsupported** (`FRD-114` FR-7). Absence of information is not permission.
- **A declaration is verified, not assumed.** Vendor capabilities move — a family that cannot take
  a PDF today may take one next quarter. So the matrix is never hard-coded in an adapter; it is a
  catalog entry, and the integration suite asserts that at least the declared *methods* are the ones
  the provider actually accepts.

### 3. Documents: capability-gated, and a chain that cannot degrade silently

This is the decision that use case depends on.

**A request carrying attachments may only be dispatched to a model that declares those media
types.** Checked against the model *about to be dispatched to* — after `model_route`, and at every
hop of the fallback chain (`FRD-110`, `FRD-112` §5.3).

**A fallback chain must be capability-homogeneous for the request at hand.** If the primary can read
the PDF and the fallback cannot, the fallback is **skipped**, not used with the document dropped.
And if no candidate qualifies, the request **fails**.

The reasoning is worth stating plainly, because the tempting behaviour is the opposite. Falling back
to a text-only model with the attachment stripped does not produce an error. It produces a fluent,
confident answer about a document the model never saw, returned with a 200, indistinguishable from a
correct answer to everyone including the caller. Compared with that, an error is a good outcome:
**failing is recoverable, being quietly wrong is not.**

**Implemented 2026-08-06**, before the documents that motivated it: `dispatch_with_fallback` takes
conditions, a candidate that fails one is skipped with its reason kept, and an exhausted chain
raises `NoCapableModel` — a **400 FAILED_PRECONDITION**, not the 502 it used to be, because "every
candidate was excluded" is something an operator can fix and an outage is not. Residency is the
first condition; media types are the second (`FRD-110`) and the schema capability the third
(`FRD-112`). They share the mechanism rather than each inventing one, which is the point of
building it while there is only one of them.

Two present-day defects fell out of it. A model no provider serves used to be a silent `continue`,
so a typo in a fallback chain was invisible — the chain behaved as though the entry were not there.
And the candidates a chain passed over are now on the audit row, which is what somebody actually
needs when they ask why an answer came from the model it did.

The practical consequence for operators: a use case that accepts documents needs a chain of
document-capable models (today: Gemini and Claude — conveniently both on the same transport and
credential), and a chain mixing capable and incapable models degrades to the capable subset for
document requests while still using the full chain for text ones. The builder must show this rather
than leave it to be discovered.

### 4. Conversion is possible, out-of-process, and never implicit

There is a legitimate wish to send a PDF to *any* model — extract its text, or render its pages as
images, and pass that on.

It is a separate feature with its own risks (`FRD-121`), and three constraints are decided here:

- **Never the default, never silent.** Enabled per use case, and every converted request records
  that it was converted, on the audit row and in the response's provenance.
- **Never in the gateway process.** A PDF parser is a large, historically vulnerable attack surface
  on caller-supplied bytes; `FRD-110` deliberately keeps the gateway from parsing what it forwards.
  Conversion belongs behind a boundary — a cloud document service or an isolated worker.
- **Quality is not equivalent and must not be presented as such.** A model that reads a PDF natively
  sees layout, tables and figures; extracted text does not have them. That is a different answer,
  not the same answer by another route.

### 5. Managed and self-deployed fail differently, so they are declared differently

Nemotron introduces hosting as a property that leaks into behaviour, and treating it as an
implementation detail would produce two operational surprises:

- **Cold start.** A self-deployed endpoint scaled to zero can take minutes to serve its first
  request. Consequences we must handle rather than discover: a budget reservation held open for that
  whole time (`FRD-405`), a rate-limit token already taken, and a fallback chain that spends its
  primary timeout waiting instead of failing over. Self-deployed models therefore carry their own
  timeout, and the readiness probe (`FRD-117`) **must not wake a scaled-to-zero endpoint** — probing
  it would cost GPU minutes to answer a question about availability.
- **429 means something different.** On a managed model it is quota: retry later, fall back now. On
  a self-deployed one it is capacity: no replica is free, and retrying *this* endpoint will not help
  until it scales. Same status code, different correct response.

So `hosting: managed | self_deployed` is a declared catalog property, and the dispatch chain, the
timeouts and the probe policy read it.

### 6. Two clouds means one mechanism and two legal positions

Residency is enforced by one allowed-region list across every transport (`aira_gateway.residency`,
`AIRA_ALLOWED_REGIONS`) — one control to implement and audit rather than one per vendor. The list
holds vendor-specific *names* (`eu`, `europe-west1`, `westeurope`) because that is the vocabulary
regions come in; what is shared is the policy, not the spelling. Implemented 2026-08-06, after a
first attempt scoped it to Vertex and would have made the first Azure model fail a check named
after Google. But an EU region on GCP and an EU region
on Azure are two different contractual and data-protection positions, and self-deployed models add a
third (our own capacity in our own project).

The gateway's job is to make *where each request went* an evidenced fact — provider, publisher,
region and hosting on every audit row, breakable down in reporting. Which of those positions is
acceptable is not an engineering decision, and the architecture's contribution is to make the
question answerable per request instead of per deployment.

## Consequences

- Positive: a use-case administrator reasons in one vocabulary. "This chain can handle documents;
  that one cannot" is visible in the builder rather than learned from a wrong answer.
- Positive: the transport × dialect matrix has four filled cells across three transports and three
  dialects before any of it is built. `ADR-0011` is validated rather than hypothetical.
- Positive: adding a fifth family is a catalog entry plus, at most, a dialect.
- Negative: capability declarations become load-bearing, so a wrong declaration is a wrong routing
  decision. Mitigated by `FRD-114` FR-7 (undeclared = unsupported, so mistakes fail closed) and by
  integration assertions against the real providers.
- Negative: document-capable routing is narrower than the model list suggests. That is the honest
  state of the world, and the alternative is worse.
- Follow-ups: `FRD-121` (opt-in conversion); `FRD-110` gains the homogeneity rule; `FRD-114` gains
  `hosting` and the addressing modes; `FRD-115` gains self-deployed endpoints and the OpenAI dialect
  on the Vertex transport.

## What this deliberately does not do

- **No lowest-common-denominator API.** We do not remove Gemini's native PDF understanding so that
  every model can be treated alike.
- **No silent substitution.** Not of a model, not of a modality, not of a schema guarantee.
- **No emulation of a capability a model lacks** inside the gateway, other than through `FRD-121`'s
  explicit, recorded, opt-in path.
