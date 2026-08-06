# FRD-114 — What a model can do, and where its limits are

> Phase: 8 (KIRA parity) · Status: **Draft** · Owner: Vadim Scheibe · Last updated: 2026-08-06
> Origin: `kira_api.md` §2.4, §5, programme: `ADR-0010`.
> Extends the `Model` row introduced by `FRD-403`; the **approval** half stays in `FRD-307`.
> Depended on by: `FRD-111`, `FRD-112`, `FRD-113`, `FRD-107`.

## 1. Problem

Three of the parity features are mostly *validation*: is this thinking mode allowed, does this
model do structured output, is this embedding task type supported, what is the largest
`maxOutputTokens` this model accepts. Validation needs something to validate against, and AIRA has
almost nothing.

Today a model is known in two places, neither sufficient:

- **Gateway** — `UpstreamModel(name, version, supported_methods)`. Hard-coded per adapter; the
  Gemini adapter builds it from a comma-separated environment variable and gives every model the
  same three methods.
- **Management** — the `Model` row from `FRD-403`: name, display name, provider, two prices.

The predecessor, by contrast, declares per model: capabilities (`CHAT` / `EMBEDDING`), maximum
output tokens, the permitted thinking modes with their token bounds and a default, a `deprecated`
flag, and for embedding models the vector dimensions, the supported task types and whether lists
are supported.

Without that, `FRD-111` has nothing to reject, `FRD-112` cannot know which fallback candidate is
usable, and `FRD-113` cannot tell a supported task type from a typo.

## 2. Goals & Non-Goals

**Goals**
- One place that answers "what can this model do, and within what bounds", reaching the gateway
  through the path that already carries prices.
- Enough for `FRD-111`/`112`/`113` to validate, and for the SPA to build pickers that offer only
  what works.
- **Adding a model is configuration, not a deployment.**
- A model whose metadata is missing behaves conservatively, and it is obvious which one it is.

**Non-Goals**
- **Approval.** `FRD-307` owns which models may be used; this FRD owns what they can do. Same row,
  two concerns — and they should not be conflated: a model can be approved and deprecated, or
  capable and unapproved.
- Pricing. `FRD-403`, already delivered on the same row.
- Auto-discovery from the provider's own metadata API. Attractive, and a follow-up: providers
  disagree about what they publish, and a wrong auto-import would silently loosen validation.

## 3. User Stories
- As a **Global Administrator**, I want to declare a new model's limits in the UI so that a model
  release is not a code release.
- As an **application developer**, I want a wrong thinking mode refused with a message naming what
  the model does support, so that I can fix it without reading our source.
- As a **use-case administrator**, I want the pipeline builder to offer only models that can do
  what my configuration needs, so that a fallback chain cannot contain a model that will fail.

## 4. Functional Requirements

- **FR-1 Capabilities.** Per model: `generate`, `embed`, `structured_output`, `thinking`,
  `attachments`. A capability absent means the gateway refuses requests that need it. With two
  vendors these genuinely differ rather than being a formality — Anthropic models have no `embed`
  at all, and `structured_output` is reached by a different mechanism (`FRD-119` §5.5), which is
  why the flag says *whether*, never *how*.
- **FR-2 Generation limits.** `max_output_tokens`, and a **default output cap** applied when the
  caller sets none. A request above the maximum is refused (`MAX_TOKENS_EXCEEDS_CAP`) rather than
  passed on for the provider to reject differently. The default is not optional polish: Anthropic
  **requires** `max_tokens` on every request (`FRD-119` §5.3), so without a per-model default every
  caller who omits it — which is most of them, since it is optional today — would receive a vendor
  error about a field they never set. It also sharpens the budget reservation for both vendors.
- **FR-2a Publisher.** Which vendor serves the model (`google`, `anthropic`, …). It selects the
  wire dialect and the endpoint method (`FRD-115` FR-2), and it belongs beside the capabilities
  rather than in adapter configuration, so one row answers "what is this model and how is it
  reached".
- **FR-3 Thinking declaration.** The permitted modes; `min_tokens`/`max_tokens` for `limited`; the
  default setting; and the token budget each abstract level maps to (`FRD-111` §5.2). Validated for
  internal consistency on write: a thinking maximum at or above the model's output cap describes a
  model that can never answer (`FRD-111` FR-3a), and the catalog should refuse to hold it.
- **FR-4 Embedding declaration.** Supported task types, whether batching is supported, and the
  available output dimensionalities with a default. Absent for a publisher that does not embed.
- **FR-4a Attachment declaration.** The media types **this model** accepts, and the token estimate
  per type. `FRD-110` intersects its own allow-list with this one, and `FRD-110` §5.3's reservation
  reads the estimates — the two vendors tokenise images and documents differently, so a global
  figure would be wrong for one of them by construction.
- **FR-5 Deprecation.** A `deprecated` flag. It **warns, it does not block**: requests succeed and
  carry a `Warning` response header, the model is marked in the SPA, and reporting can show who is
  still using it. Blocking is what `FRD-307`'s revocation is for, and conflating the two removes
  the ability to announce a retirement before performing one.
- **FR-6 Numeric alias.** An optional stable integer id per model, for the predecessor's
  `model_id`. **Only meaningful if `FRD-107` is built** (`ADR-0010`); it is otherwise unused and
  should be dropped with it.
- **FR-7 Missing metadata fails closed.** See §5.3.
- **FR-8 Distributed, not queried.** The gateway reads its own read-model, never calls Management
  on the request path. This is the existing pattern and the reason the gateway survives a
  Management outage.

## 5. Design & Architecture

### 5.1 The catalog row grows; the transport does not

`FRD-403` already created the `Model` row, the `aira.models` Kafka topic, the gateway read-model
and the SPA screen. This FRD adds fields to all four and introduces no new machinery.

Metadata is authored where prices are: **Global Administrator only**, on the Models & prices
screen. That restriction is not incidental — §5.4.

### 5.2 The gateway registry stays what it is

`ProviderRegistry` continues to answer "does an adapter exist that can reach this model". It is not
extended with capability metadata, because it is built from adapter configuration and would then be
a second, quieter source of truth for the same question. The dispatch path resolves an adapter from
the registry and a capability declaration from the read-model, and the two are allowed to disagree:
a model in the registry with no declaration is FR-7's case.

### 5.3 A model with no declaration does the baseline, and nothing more

The tempting default is permissive — an undeclared model accepts everything and lets the provider
complain. That is wrong here, for the same reason "unpriced is not free": absence of information is
not permission. A model with no thinking declaration would accept a 32 768-token thinking budget,
which the reservation would then have to estimate against nothing.

So an undeclared model gets `generate` and `embed` (the baseline that works today, so nothing
regresses) and nothing else: no thinking, no structured output, no attachments, no non-default
embedding task type. Each refusal names the missing declaration, so the fix is obvious and is a
catalog edit rather than a support ticket.

The SPA surfaces undeclared models the same way it surfaces unpriced ones — visibly, as something
incomplete rather than something absent.

### 5.4 Metadata is a security control, so it is governed like one

`max_output_tokens`, the thinking maximum and the batch bound are all **cost ceilings**. Someone
who can raise the thinking maximum to a million can make one request cost as much as a month.
That is why authorship is Global-Admin-only and why every change is audited — the same argument
that already applies to prices, with more direct leverage.

### 5.5 The pickers get better for free

`FRD-307`'s builder pickers and the new declarations meet naturally: a fallback chain can offer
only models with the capabilities the rest of the configuration needs, and a model that loses a
capability shows the saved configuration as unavailable rather than dropping it silently — the
behaviour `FRD-307` already specifies for revoked models.

## 6. Data Model

Management `Model` gains (all nullable, so existing rows stay valid):

| Field | Type | Notes |
|---|---|---|
| `capabilities` | string set | `generate`, `embed`, `structured_output`, `thinking`, `attachments` |
| `publisher` | string? | `google`, `anthropic`, … — selects dialect and method (FR-2a) |
| `max_output_tokens` | int? | FR-2 |
| `default_max_output_tokens` | int? | applied when the caller sets none (FR-2) |
| `attachments` | JSON? | accepted media types and their token estimates (FR-4a) |
| `thinking` | JSON? | modes, `min_tokens`, `max_tokens`, default, level→budget map |
| `embedding` | JSON? | `task_types`, `supports_batch`, `dimensions[]`, default |
| `deprecated` | bool | default false |
| `numeric_id` | int? | unique when set; FR-6 |

Gateway read-model `models` table mirrors it (new migration). Kafka `aira.models` payload extends;
the consumer stays idempotent and tolerates the older payload shape during a rolling deploy.

## 7. API / Interface Contract

- Management `GET/POST /api/v1/models/` — extended payload, write restricted to Global Admin.
- Gateway `GET /v1beta/models` — reports capabilities and limits alongside the existing fields, so
  a client can discover them rather than reading our documentation.

## 8. Security & Privacy

§5.4. Additionally: FR-7 means a misconfiguration fails safe, and a *deleted* declaration
immediately narrows what is accepted rather than widening it.

## 9. Observability

The gateway logs, once at consume time, when a declaration arrives or changes — a validation rule
that changed underneath a running system should be findable in a log, not inferred from behaviour.

## 10. Testing & Acceptance Criteria

- **Unit (Management)** — write restricted to Global Admin; read open to any authenticated user;
  the thinking and embedding blocks validated on input (a maximum below a minimum is refused, an
  unknown task type is refused) so the catalog cannot hold a self-contradictory declaration.
- **Unit (gateway)** — an undeclared model refuses thinking, structured output, attachments and a
  non-default task type, each with its own message; a declared model accepts exactly what it
  declares; `max_output_tokens` refused one above the cap.
- **Unit (consumer)** — an older payload without the new fields is applied without error, and does
  not blank the fields it does not carry.
- **Frontend** — the editor renders the blocks, validates before sending, and marks deprecated and
  undeclared models distinctly.
- **Integration** — a declaration authored in Management reaches the gateway and changes what the
  gateway accepts, end to end.
- **Mutation** — FR-7 fails **closed** (mutate the default to permissive and the test must go red);
  `deprecated` warns rather than blocks; the Global-Admin restriction actually restricts.

**Acceptance**
- *Given* a new model added to the catalog with thinking modes and a maximum, *when* a caller
  requests a budget above it, *then* it is refused naming the maximum — with no gateway deployment
  in between.
- *Given* a model marked deprecated, *when* it is called, *then* the request succeeds and the
  response carries a `Warning` header.

## 11. Dependencies & Risks

- **`FRD-403`** (the row and its transport) — delivered. **`FRD-307`** (approval) — adjacent and
  independent.
- **Risk — the metadata becomes wrong.** A declaration that outlives the model it describes
  produces confident, incorrect validation. Mitigation: `GET /v1beta/models` reports what the
  gateway believes, so a mismatch is inspectable; and the integration suite asserts that at least
  the declared *methods* match what the provider actually accepts.
- **Risk — FR-6 exists only for `FRD-107`.** If `ADR-0010` resolves toward moving the clients, this
  field should be removed rather than left as an unused column that later reads as meaningful.

## 12. Rollout / Demo

The seed declares full metadata for the mock models — including a thinking config and two
embedding dimensionalities — so `FRD-111` and `FRD-113` are demonstrable in demo mode. Existing
catalog rows keep working undeclared, which is FR-7's path and should be exercised in the demo too.
