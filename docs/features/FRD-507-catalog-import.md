# FRD-507 — Importing what the adapters already serve

> Phase: 6 · Status: **Built (2026-08-10)** · Owner: Vadim Scheibe · Last updated: 2026-08-10

## 1. Problem

A model has to be named twice: once in the gateway's configuration, so an adapter reaches it, and
once in the catalog, so `FRD-307` permits it. The second is a **decision** and belongs in the
console. The first is a **fact** the gateway already knows and the administrator was retyping.

Asked directly: *"macht es denn überhaupt sinn, wenn die Modelle in der Oberfläche angelegt
werden?"* — and the answer is that the decision does and the transcription does not.

The evidence sits in the same session. A key issued for Google AI Studio listed **50 models**, 36 of
them able to generate. Nobody had approved any of them, and one that the listing offered —
`gemini-2.5-flash` — answered `404: no longer available to new users` on the first request. So:

- a catalog that simply mirrored the endpoint would release 36 models nobody chose;
- an administrator typing names by hand gets a typo in one of two places, and the two refusals that
  follow (`not in the model catalog`, `has not been approved`) are correct and unhelpful about which
  string was wrong;
- and *listed* does not mean *usable*, which is exactly what `FRD-506` already split into three
  facts: **declared · served · reachable**.

## 2. Goals & Non-Goals

**Goals**

- Show a Global Administrator what the configured adapters actually serve, and which of those the
  catalog does not know.
- Let one of them become a **draft** catalog entry with its provenance filled in.

**Non-Goals**

- Creating or approving anything automatically. `FRD-307` is the owner's rule: only a model a Global
  Administrator has catalogued and released may be used, and absence of information is not
  permission (`FRD-114` FR-7).
- Importing **capabilities**. A vendor's flag is a claim, not evidence — `FRD-131` recorded a model
  that lists `tools` in its own metadata and returns the JSON as prose. What a model can do is a
  measurement, and the catalog is where measurements are kept.
- Importing **prices**. No endpoint publishes them in a usable form, and an invented price is worse
  than an absent one: `FRD-403`'s rule is that unpriced traffic is counted apart, never as zero.

## 3. Functional Requirements

- **FR-1** The gateway's model listing carries each model's **provenance** — provider, publisher,
  region — because that is what an import may faithfully copy. It already carried capabilities and
  the declared flag (`FRD-114` §7).
- **FR-2** The catalog screen offers **Discover**: the models the gateway serves, marked as already
  catalogued or not.
- **FR-3** Choosing an undeclared one opens the ordinary model editor **pre-filled** with name,
  provider, publisher and region. Price, capabilities and the release checkbox are untouched.
- **FR-4** Nothing is written until the administrator saves, and `approved` still defaults to false
  (`FRD-307`).
- **FR-5** A model the catalog already has is shown and **not** offered for import, so the list
  answers "what is missing" rather than "what exists".

## 4. Design

### 4.1 The gateway is the only thing that knows

Management holds the catalog; the gateway holds the adapters. Which models are reachable is a
property of *configuration the gateway was given*, so the console asks the gateway through the same
`/gw` proxy that the dry-run, usage and traces views already use. No new endpoint: `/v1beta/models`
answers this question and only lacked the provenance fields.

### 4.2 What may be copied, and what may not

The split is the whole design. **Where a model lives** is a fact the adapter has: it built the
`UpstreamModel` from its own configuration, and provider/publisher/region reach the audit row from
there already. **What a model can do and what it costs** are not facts the endpoint can be trusted
for, and the two rules that say so were both paid for:

- `FRD-131`: `qwen2.5-coder:7b` lists `tools` and cannot call one.
- `FRD-403`: a price nobody set is not zero, and the report says so.

So an import fills in the first and leaves the second blank — which also means the editor still
*asks*, and an administrator who does not know the price has not accidentally declared it free.

### 4.3 Listed is not usable

`gemini-2.5-flash` is in the listing and refuses to answer. Discovery therefore says what the
gateway **serves** — it does not claim the model works. That is `FRD-506`'s third fact, and the
`:check` action on a catalogued model is where it is answered, deliberately by a listing rather
than a generation so that a health question cannot wake a scaled-to-zero model or cost money.

## 5. Testing

- The listing carries provenance for a model whose adapter declares it.
- Discovery separates catalogued from not-catalogued, in both directions.
- Choosing a model pre-fills exactly the provenance and **nothing else** — asserted on the price and
  capability fields being untouched, because that is the property an eager implementation breaks.
- Each shown to fail first.

## 6. Risks

- **Read as a release.** A screen that lists 36 models next to an "Add" button invites bulk
  approval. There is no bulk action, and the wording says the endpoint offers them rather than that
  they are ready.
- **A stale configured list.** `AIRA_GEMINI_MODELS` defaulted to `gemini-2.0-flash,gemini-1.5-flash`
  — models a new API key can no longer use. A default that names something unusable is a trap; it
  now names nothing, and discovery is how you find out what your key actually offers.
