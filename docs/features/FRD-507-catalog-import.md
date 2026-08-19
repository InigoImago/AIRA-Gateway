# FRD-507 — Importing what the adapters already serve

> Phase: 6 · Status: **Built** · Owner: Vadim Scheibe

## 1. Problem

A model has to be named twice: once in the gateway's configuration, so an adapter reaches it, and
once in the catalog, so `FRD-307` permits it. The second is a **decision** and belongs in the
console. The first is a **fact** the gateway already knows and the administrator was retyping.

Asked directly: _"macht es denn überhaupt sinn, wenn die Modelle in der Oberfläche angelegt
werden?"_ — and the answer is that the decision does and the transcription does not.

The evidence sits in the same session. A key issued for Google AI Studio listed **50 models**, 36 of
them able to generate. Nobody had approved any of them, and one that the listing offered —
`gemini-2.5-flash` — answered `404: no longer available to new users` on the first request. So:

- a catalog that simply mirrored the endpoint would release 36 models nobody chose;
- an administrator typing names by hand gets a typo in one of two places, and the two refusals that
  follow (`not in the model catalog`, `has not been approved`) are correct and unhelpful about which
  string was wrong;
- and _listed_ does not mean _usable_, which is exactly what `FRD-506` already split into three
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
property of _configuration the gateway was given_, so the console asks the gateway through the same
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
_asks_, and an administrator who does not know the price has not accidentally declared it free.

### 4.3 Listed is not usable

`gemini-2.5-flash` is in the listing and refuses to answer. Discovery therefore says what the
gateway **serves** — it does not claim the model works. That is `FRD-506`'s third fact, and the
`:check` action on a catalogued model is where it is answered, deliberately by a listing rather
than a generation so that a health question cannot wake a scaled-to-zero model or cost money.

## 4.4 The second list, removed (stage B)

A model had to be named **twice**: in the adapter's configuration so it would be offered, and in the
catalog so `FRD-307` would permit it. Which makes the first version of this FRD circular — you type
a name into configuration, and "discovery" reads it back to you. That is an echo, not an import.

The catalog is already the authority on _what may be served_. A row names its **provider**, and
that is enough to know _who_ serves it: an adapter owns a provider name (`serves_provider`), and
`ProviderRegistry.provider_for(model, provider)` falls back to it. Configured models resolve
exactly as before; a catalogued one now resolves too, with no configuration entry and no restart.

Two adapters claiming one provider **refuse to boot** — `ADR-0011`'s ambiguous routing table one
level down, since registration order would otherwise decide a model's region and credential.

**This was built, reverted, and rebuilt on the same day.** The working version wrote `provider` and
`region` **empty** onto the audit row: provenance is read from the registry, and a catalogue-
resolved model has no entry there. An empty residency column is worse than the second list this
removes — `FRD-115`'s point is that "the configuration says EU" is a claim and "this request went
to `eu`" is evidence, and blank is neither. The adapter that owns the provider answers instead, and
states its provenance once so an empty configured list still produces a complete row.

## 4.5 Ask the vendor, not the configuration (stage C)

Stage B removed the second list and left a circle: discovery reads back the models somebody typed
into the gateway's configuration. That is an echo. The list nobody had was the **vendor's**.

    the vendor offers   — what a credential can reach.  `GET /v1beta/providers/{name}/offerings`
    the gateway serves  — what an adapter is wired for.  `GET /v1beta/models`
    the catalog permits — what may actually be used.     `FRD-307`

Three lists, and the design is mostly about keeping them apart: collapsing any two produces a
screen that is confidently wrong rather than empty.

**FR-6** The provider field is a **choice**, not a string. `GET /v1beta/providers` answers what
_this installation_ is configured with — a hard-coded vocabulary would describe what the product
supports, which is a different question and offers providers no credential reaches. A provider not
on the list may still be typed: declaring a model before its platform is configured is the ordinary
order of work, so an unreachable gateway degrades to the box it replaced rather than locking an
administrator out of their own catalog.

**FR-7** Each provider carries two facts, because getting either wrong produces a catalog entry
that looks complete and does not work:

- `canEnumerate` — whether the platform can be **asked**. Stated, never discovered by trying: a
  platform without a listing is not broken, and an error beside it reports a capability gap as a
  fault. Those send a reader to two different systems.
- `cataloguedIsEnough` — whether declaring the model suffices to reach it, or whether it must also
  be named in the gateway's configuration. True exactly where the model name is the whole
  addressing (§4.4).

**FR-8** Where a provider can be asked, choosing it lists what it offers, marking what the catalog
already has. Nothing is filtered out: "nothing left to import" and "this credential reaches
nothing" must not be the same empty list.

**FR-8a** That list is a **window of its own** — not a dropdown inside the editor. One real key
answered with **50 models**, and a select of fifty inside a form that already has eighteen fields
is a control somebody scrolls past. The window lists, searches, marks and scrolls; it ends by
handing exactly one model to the editor, and closes, since two open windows leave a reader unsure
which one their next click belongs to. *(Stage D, §4.6: it was reached from a button beside
_+ Add model_; it is now the only entrance, and `+ Add a model` opens it.)*

**FR-8b** A model the catalog already has opens its **existing declaration**, never a blank form
carrying the vendor's answer — a measured capability or an entered price must not be replaced by a
claim.

**FR-9** Choosing one fills in **what the vendor stated and nothing else**, and the console names
both halves.

**FR-9a** A provider carries a **label** as well as a name, and the adapter states it. The name is
an identifier — it goes into the catalog, onto every audit row and into routing, so it stays
`generative-language` — and a picker showing only that, beside a self-hosted server called `local`,
names neither vendor. It was reported from the running console exactly that way. The label comes
from the adapter rather than from a map in the SPA, because a second vocabulary restated in
TypeScript is the drift `FRD-206` and the capability list both paid for.

### What may be copied, revisited

§4.2 said provenance may be copied and capabilities may not. Stage C sharpens that, because the
vendor's answer contains two different kinds of thing:

| The vendor's answer         | Copied? | Why                                                     |
| --------------------------- | ------- | ------------------------------------------------------- |
| name, display name          | yes     | identity                                                |
| `outputTokenLimit`          | yes     | the API refuses a larger request — an interface fact     |
| `supportedGenerationMethods`| yes     | exhaustive; the API answers 404 for a method not in it   |
| `thinking: true`            | **no**  | `FRD-114` needs modes and budgets; no listing has them   |
| tools, structured output    | **no**  | a claim, not evidence (`FRD-131`)                        |
| price                       | **no**  | a price nobody set is not zero (`FRD-403`)               |

`createCachedContent` is how prompt caching appears in that method list. The word "caching" is
nowhere in the response, so an implementation reading the obvious field declares no caching for a
model that has it (`FRD-133`).

**FR-10** Every capability is **three-valued**. `null` means the vendor said nothing — an
OpenAI-compatible listing publishes bare ids and answers no capability question at all — and it is
a different answer from `false`, which is a statement. Serialising the first as the second
pre-fills a form somebody is about to save with a declaration nobody made: `FRD-114` FR-7 at the
one moment it is hardest to notice, because a half-full form looks like a working feature either
way. A capability is therefore **added** from a vendor's statement and never removed by its
silence.

**FR-11** A provider name does not identify one adapter. An EU Vertex deployment registers two
(Gemini and Anthropic — one platform, one credential, two dialects) and both stamp `vertex`. They
are one entry that **cannot be asked**: a listing answering for one dialect while claiming to
answer for the platform is `ADR-0011`'s ambiguous routing table in a read-only costume.

**FR-12** Picking from a listing the vendor answered a moment ago satisfies `FRD-506`'s
"you have to look" gate — but only where `cataloguedIsEnough`. Where it is not, the model still
needs a configuration entry and the reachability check is exactly what says so.

### Which platforms can be asked

| Platform                     | Asked | Cataloguing is enough | Why |
| ---------------------------- | ----- | --------------------- | --- |
| Google AI Studio             | yes   | yes                   | paged `ListModels`; the id is the model name |
| OpenAI-compatible (Ollama, …)| yes   | yes                   | `/v1/models`; ids are names, and nothing else |
| Microsoft Foundry (Azure)    | no    | no                    | the listing names models needing a **deployment** first |
| Vertex (Gemini + Anthropic)  | no    | no                    | two adapters under one provider name |
| Mock (local only)            | yes   | yes                   | so the flow is demonstrable without a credential |

Azure is what the distinction was written for. `/openai/models` answers "which models could this
resource run"; each needs a deployment created before any request reaches it, and the deployment
name is the addressing. An import from there is catalogued, priced, approved — and answers 404 on
its first request, with the catalog vouching for it. So `names_models()` lives on the **routing
axis** rather than in the dialect, and Foundry, which builds the very same adapter class, claims no
provider name and offers no listing.

## 5. Testing

- The listing carries provenance for a model whose adapter declares it.
- Discovery separates catalogued from not-catalogued, in both directions.
- Choosing a model pre-fills exactly the provenance and **nothing else** — asserted on the price and
  capability fields being untouched, because that is the property an eager implementation breaks.
- Stage D adds seven: one entrance and it opens the provider window; provenance summarised when
  known and asked when not; the block latching open while in use; **Change** revealing the fields;
  the manual route clearing the last verdict; and a gateway with no provider still having a way in.
- Each shown to fail first. The stage-D seven are frontend properties, so `make mutants` (pytest
  only) does not reach them: each was broken by hand in the component and confirmed red.

## 6. Risks

- **Read as a release.** A screen that lists 36 models next to an "Add" button invites bulk
  approval. There is no bulk action, and the wording says the endpoint offers them rather than that
  they are ready.
- **A stale configured list.** `AIRA_GEMINI_MODELS` defaulted to `gemini-2.0-flash,gemini-1.5-flash`
  — models a new API key can no longer use. A default that names something unusable is a trap; it
  now names nothing, and discovery is how you find out what your key actually offers.

## 4.6 One entrance, and the questions it stops asking (stage D)

Stages A–C added a way in beside the one that existed. Two buttons stood on the models page —
_"Add from a provider…"_ and _"+ Add model"_ — and the owner's verdict on the pair, after using it
to declare a real Vertex model, was two complaints that turn out to be one:

> _"I find the options for adding a model too complex by now, it is very confusing, too many
> options where you do not know exactly what you are doing. If somebody other than me is to add a
> model, that person will not understand the screen. A further point is the Add model button, where
> you have to type everything in yourself — that is by now unnecessary, since we have adding from a
> provider."_

Measured before changing anything: the editor renders **61 controls** with every capability ticked,
and its identity tab asks **eight questions**. Five of them — provider, publisher, platform,
hosting, the KIRA id — are answered by choosing the provider, one screen earlier. The empty form
asked them anyway, of a reader who had no way to know that the answer was already known.

That is the defect, and it is not "two buttons instead of one". **A screen that offers "type it all
yourself" beside "let the software fill it in" is not offering a choice; it is offering a way to
get it wrong** — and the failure it produces is the one `FRD-506` exists to catch, a model declared
under provenance that reaches nothing.

Three changes, all of them subtraction:

1. **One entrance.** `+ Add a model` opens the provider window. Every model lives somewhere, so
   every path starts by saying where.
2. **Provenance is stated, not asked.** When something filled those five fields in — a listing, the
   gateway's served models, a row being corrected — the editor shows one sentence
   (_"Lives on **Google Vertex AI (vertex)**, speaking the google dialect, on the vertex
   platform."_) and a **Change** button.
   Nothing is concealed: the facts are on screen without a click, and only the four boxes are gone.
   The block opens by itself when the provider is empty, which is the one case where the reader
   really does have to answer.
3. **The empty form survives where it is the honest option**, not the tempting one: a gateway with
   no upstream configured offers _"Declare a model by name"_, and a provider that publishes a list
   offers _"Not listed? Name it yourself"_ beside it.

The identity tab now asks **two** questions on the common path — the model id and the region.

Point 3's second half is a gap the change created and the same pass closed. Removing the page-level
button left a provider that _can_ be listed with no way to name a model the listing does not carry:
a deployment of your own on an OpenAI-compatible server, a model too new for the vendor's index, a
name only the gateway's configuration knows. The old button covered that by accident, from outside
the flow. It is now covered on purpose, from inside it, with the provider's facts still filled in.

**Two defects the tests for this found**, both of the shape this project keeps recording:

- **The block collapsed under the reader.** With provenance shown whenever a provider is known, the
  empty form opened the fields, the reader picked a provider from the select — and _"a provider is
  now known"_ took the select out of the DOM under the pointer that had just used it. A rule about
  what a form knows, applied while somebody is telling it. The block now latches open.
- **One model's verdict on another model's form.** `add()` clears the reachability verdict and
  carries a comment saying why; the manual route opened the same window beside it and did not.
  Checking a row, then adding a model, showed a green badge about a different model. Two entrances
  to one window and only one of them swept the floor — which is also the argument for there being
  one entrance.

**Three more the owner found by looking**, after all of the above was green:

- **The fields stopped standing under one another.** `.form-inline` is a wrapping flex row, which
  suits eighteen fields and not two: the model id and the region shared a line and each stretched
  to half the dialog. It is the note-becomes-a-column defect from §4.4 seen from the other end —
  flex decides how to fill a row, and a form is not trying to fill one.

  **Fixed twice, and the first attempt is the lesson.** A class on each field (`.field--own-row`)
  reached the identity tab and left the other two alone, and the report came back the same day:
  *"the window is still stretched."* A rule applied per field is a decision that has to be
  remembered at every field added afterwards, and it had already been forgotten at two tabs out of
  three before anybody looked. It is one rule scoped to the window now —
  `.modal--steady .modal__body > form.form-inline > .field` — and the width goes with it: 880
  pixels was chosen for a listing of fifty models, and a stack of fields in it is a form using the
  left half of a window. The editor is 620. Only the form's *own* children, because the
  capabilities tab nests a `.form-inline` inside a fieldset for the per-media-type and per-level
  token fields, and fifteen of those in a column is a page of scrolling where a row of small
  numbers is right.

  The `layout.spec.ts` guard followed the property rather than being deleted: *do the controls on a
  line start at the same height* had **no line with two controls on it** left to check, and would
  have gone on passing by finding nothing. It now asserts the editor **stacks** — every top-level
  field on the same left edge, no two sharing a line — **on all three tabs**, which is exactly the
  hole the per-field fix fell into. Broken on purpose (`flex-basis: auto`) and seen to fail, naming
  the tab.
- **The sentence had spaces before its punctuation**: `Lives on **mock** , speaking the aira
  dialect .` The markup was already correct; `.field__summary` was a flex row with `gap: 0.5rem`,
  and a `<strong>` mid-sentence is a flex item. The unit test that asserts the sentence character
  for character **passed** — a flex gap is not a character, and `textContent` cannot see it. Where
  the defect is the layout, only a picture or a measured box is evidence.
- **The import note repeated the sentence.** It opened with _"Filled in from mock: provider,
  dialect"_ one line under a sentence saying exactly that. Stating a fact somewhere new means
  deleting the old statement; the note now carries only what the sentence cannot — what the import
  deliberately left for you.
