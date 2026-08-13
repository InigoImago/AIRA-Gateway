# FRD-307 — Approved-model catalog + model pickers

> **Partially delivered by [FRD-403](FRD-403-cost-budgets.md)**: the Management
> `Model` record and its Kafka distribution exist now, carrying prices. Approval, the candidate
> list and the builder pickers described below are still open and build on the same row.

> Phase: 3 · Status: **Approval delivered; candidate lists and builder pickers still open** · Owner: Vadim Scheibe
> Origin: user feature request — "an overview of all models, and pick them in the injection filter /
> allowed-model config." Refined: **model availability is governed — only models a Global
> Administrator has approved are usable; the raw gateway model list is not the same as the usable set.**

## 1. Problem
The builder asks operators to type model names as free text, and there is no notion of which models
are *allowed*. Two gaps:
1. **No visibility** of available models → names are guessed, typos cause runtime 404s.
2. **No governance** → today any name can be typed, but the business rule is that only **Global-Admin-
   approved** models may be used across use cases.

## 2. Goal
- A **Global Administrator approves models** into an **approved catalog** (the raw gateway registry is
  just the candidate pool).
- Everywhere the builder picks a model (injection-filter classifier, allow-check list, routing
  classifier/category/default models, fallback chain), operators **choose from the approved catalog**
  only — no free-typing of un-approved models.
- A **Models overview** page: everyone sees the approved catalog; the Global Admin additionally sees
  the raw gateway list with an **approve / revoke** toggle.

## 3. Design
### 3.1 Catalog (source of truth for *usable* models)
- Management `Model` table: `{name, display_name?, provider?, enabled, approved_by, approved_at}`.
- The **candidate pool** comes from the gateway's `GET /v1beta/models` (built from `ProviderRegistry`);
  the Global Admin promotes candidates into the approved catalog. A model can also be pre-seeded.
- `GET /api/v1/models` → **approved** models (any authenticated user, for the pickers).
- `GET /api/v1/models/candidates` → raw gateway list + approved flag (**Global Admin only**), for the
  approve UI. (Management proxies `/gw/v1beta/models`.)
- `POST /api/v1/models` / `DELETE /api/v1/models/{name}` → approve / revoke (**Global Admin only**),
  via the existing role-permission classes.

### 3.2 Enforcement (security by default)
- UI restriction alone is not enough. The **approved set is distributed to the gateway** over Kafka
  (`model.approved` / `model.revoked` → read-model), and the gateway **rejects requests for a model
  not in the approved catalog** (the global gate; the per-use-case one is `FRD-308`). This closes
  the bypass where a key holder calls a non-approved model directly. (May be phased: catalog + pickers
  first, gateway enforcement second.)

### 3.3 UI
- **Models overview** (`/models`, lazy + nav item): approved table for all; Global Admin gets the
  candidate list with approve/revoke.
- **Builder pickers**: `<select>`/checklist populated from `GET /api/v1/models` (approved). Because a
  saved config may reference a now-revoked model, show such values as an "unavailable" chip rather
  than dropping them silently.

## 4. RBAC
- **Read approved catalog**: any authenticated user (pickers, overview).
- **Approve / revoke / manage**: **Global Administrator** only (`IsGlobalAdmin`).

## 5. Non-goals
Per-model pricing/context/health metadata (later); registering upstream providers from the UI
(providers come from gateway config / Vault).

## 6. Testing & Acceptance
- Management: catalog CRUD scoped to Global Admin; `GET /api/v1/models` returns approved only;
  candidates endpoint lists gateway models with approved flags. Gateway (if enforcement in scope):
  request for a non-approved model is rejected; approved passes.
- Frontend: `ModelService` unit test; builder pickers populated from approved catalog; revoked model
  shown as unavailable; Models overview renders; Global-Admin-only approve controls.
- Acceptance: as Global Admin, approve `gemini-2.0-flash`; as a use-case admin, the builder offers it
  in every model picker (and *only* approved models); dry-run/live request confirms; revoke removes it
  from the pickers (and, with enforcement, the gateway then rejects it).

## 7. Dependencies
`/v1beta/models` (FRD-100), RBAC roles + permission classes (FRD-201), Kafka distribution pattern
(FRD-204) for enforcement, pipeline builder (FRD-303/306).

## Approval, delivered 2026-08-09

> *"Modelle dürfen nur durch Global Administrator freigegeben werden."*

The catalog was already Global-Admin-only to **write**. What was missing is approval as a *state*:
a model could be declared, priced and immediately callable, so **a model appearing on an upstream
implied a decision nobody had made**.

- `Model.approved`, **default false**. The default is the decision: a new declaration is not a
  release.
- Distributed on the existing `aira.models` event and enforced in the gateway as a **dispatch
  condition** (`ModelApproved`), beside residency, media types and capabilities — so it is checked
  at **every hop** of a fallback chain. A chain that routed around an unapproved model would be
  honouring the letter and losing the point.
- The refusal **names who releases a model**, because an operator reading "not approved" needs to
  know whose decision it is.

### Two defaults that point in opposite directions, deliberately

| | default | why |
|---|---|---|
| Management (`catalog_model.approved`) | **false** | the decision is made here, and a new declaration has not had one |
| Gateway read-model | **true** | fed by events; an event from an older Management carries no such field, and reading its absence as "not approved" would retire every model the moment one plane is upgraded before the other |

The migration sets every **existing** row to approved. A governance improvement delivered as an
outage is not an improvement, and nobody decided anything about the old models by installing an
update.

### The gap was closed the same day

The first version left a model with **no catalog row** ungated, on `FRD-114` FR-7's reasoning that
an undeclared model gets the baseline. The owner closed it:

> *"Es dürfen nur die Modelle verwendet werden, die im Katalog stehen und explizit von einem
> globalen Admin angelegt wurden."*

So the baseline for a model nobody catalogued is **nothing**. That matters beyond tidiness: the
first version could be defeated by *deleting* a declaration, which meant approval was removable by
removing the thing that carried it.

**This narrows `FRD-114` FR-7 and that FRD says so.** "Absence of information is not permission"
now extends from *what a model may do* to *whether it may be used at all*.

Two refusals, kept apart, because they need different actions:

| | message | what to do |
|---|---|---|
| no catalog row | *"is not in the model catalog"* | add the model |
| row, not approved | *"has not been approved for use"* | release it |

### The one exemption, and why it is not a hole

A **test double** — `MockProvider` and the stub upstreams in the suite — is not governed. Its
answers are deterministic fiction, they cost nothing, and approving them would be theatre.

The exemption is bounded by something stronger than a flag: the mock is **no longer registered
outside `local`**. It used to be registered everywhere, which meant a fake model could serve real
traffic and be billed as free — worse than an ungoverned real one, and a hole `FRD-307` made
impossible to keep ignoring.

### The blast radius, measured rather than estimated

Turning the rule on failed **58** hermetic tests. Not one of them was a defect: every one used an
invented model with no catalog row. Marking the stub providers as the doubles they are took it to
zero, which is the honest fix — the alternative, cataloguing invented models in fifty test files,
would have taught the suite to lie about what the policy is.

### Still open from the original FRD

The candidate lists and builder pickers: a use case's allow-list and the pipeline builder still
take a typed model name rather than choosing from the approved set.
