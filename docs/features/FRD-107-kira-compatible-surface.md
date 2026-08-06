# FRD-107 — A KIRA-compatible API surface

> Phase: 8 (KIRA parity) · Status: **Done — Stage A + B (2026-08-06)** · Owner: Vadim Scheibe
> Last updated: 2026-08-06
> Origin: `kira_api.md` §2, §6, §12. Depends on `FRD-110`–`FRD-114`.

> **`ADR-0010` is decided (Option C).** The owner's feature definition names KIRA-API compatibility
> as a central feature (PRD §1.1, item 3), so this surface is built — **with a sunset date and its
> usage visible in reporting** (§5.6), which is the half of the decision that keeps a compatibility
> layer from becoming permanent. `FRD-114`'s numeric alias stays.

## 1. Problem

Even with every capability in place, a KIRA client cannot call AIRA. The paths differ, the request
body differs, the error vocabulary differs, and models are identified by integer rather than by
name. Migration would mean a code change in every consuming application, scheduled by every
consuming team — and the predecessor cannot be decommissioned until the last of them is done.

This FRD is the alternative: AIRA answers the predecessor's requests, so a consumer migrates by
changing a base URL.

## 2. Goals & Non-Goals

**Goals**
- The predecessor's endpoints, request and response shapes and error codes, served by AIRA.
- Every AIRA control applies to that traffic: attribution, budgets, rate limits, the pipeline,
  persistence, reporting. **That is the entire point** — traffic that has not migrated is traffic
  that is not governed.
- The surface announces that it is transitional, and its usage is measurable, so the decision to
  remove it is made against a number (`ADR-0010` Option C).

**Non-Goals**
- Bug-compatibility. Where the predecessor's behaviour is a mistake, we do the right thing and
  document the difference (§5.5). Two are already identified.
- Preserving its internal shapes beyond the wire contract.
- Serving it forever. §5.6.

## 3. User Stories
- As an **application owner**, I want to point my existing client at AIRA by changing a URL, so
  that migration is a configuration change I can schedule this quarter.
- As **IT Steuerung**, I want migrated traffic under budgets and limits from day one.
- As an **operator**, I want to see how much traffic still uses this surface, so that retiring it
  is a decision rather than an argument.

## 4. Functional Requirements

- **FR-1 Endpoints** under `/kira/api/external`: `POST /chat`, `POST /streaming-chat`,
  `POST /embed`, `GET /models`, `GET /health`, `GET /version-info`, `GET /ki-usage`.
- **FR-2 Request and response shapes** exactly as `kira_api.md` §2 and §4, including the camelCase
  aliases (`maxTokens`, `responseSchema`, `thinkingConfig`, …) and `populate_by_name`.
- **FR-2a Staged, and never silent** (§5.2). A field the gateway cannot yet honour is **refused**
  with the predecessor's error vocabulary and a message naming it. No field is ever accepted and
  ignored, at any stage.
- **FR-3 Error vocabulary** as §6.2: `{code, message, details?}` with the predecessor's codes and
  status mapping.
- **FR-4 Integer model ids** resolved through `FRD-114`'s numeric alias; an unknown id is
  `404 MODEL_NOT_FOUND`.
- **FR-5 SSE events** as §2.2: `{status: "update"|"completed", data}`, terminating in a
  `CompletedEvent` carrying the full response and usage.
- **FR-6 Attribution** resolved per §5.3 — this is the requirement with no counterpart in the
  predecessor and the one that decides whether the surface is governable at all.
- **FR-7 Authorization levels.** Standard-user endpoints and the admin-only `/ki-usage`, mapped
  onto AIRA's roles rather than onto a group name from a YAML file (§5.4).
- **FR-8 Deprecation is declared.** `Deprecation` and `Sunset` headers (RFC 8594) on every
  response, and the surface is a dimension in reporting (§5.6).

## 5. Design & Architecture

### 5.1 A third mapper, not a third gateway

`aira_gateway/api/kira/` sits beside `api/gemini/`: schemas, a mapper to and from the canonical
core, and a router. It shares the pre-dispatch gate, the pipeline, the dispatch chain, the audit
writer and the reporting service — everything below the surface.

If this FRD ends up touching anything outside `api/kira/` and `FRD-114`'s alias, the canonical core
was not as provider-agnostic as `FRD-100` claimed, and that is worth knowing.

### 5.2 The capabilities must exist first — so the surface ships in two stages

`/chat` carries documents (`FRD-110`), thinking (`FRD-111`) and `responseSchema` (`FRD-112`);
`/embed` carries task types and batching (`FRD-113`); `/models` reports capabilities and limits
(`FRD-114`). A surface built before them would accept fields it silently ignores, which is worse
than refusing them — a caller cannot tell that their thinking budget was dropped.

That is an argument against *ignoring*, not against shipping. The owner's priority (PRD §1.3) puts
KIRA compatibility first while its dependencies sit third, and the resolution is a stage boundary
rather than a wait:

**Stage A — the text contract, honestly bounded.** `/chat`, `/streaming-chat`, `/embed`, `/models`,
`/health`, `/version-info`, `/ki-usage`, the error vocabulary, the integer model ids, attribution
(§5.3) and the deprecation headers. A request carrying `request.parts[].mime_type`, `thinking` or
`responseSchema` is **refused with the predecessor's own error code**, naming the field and saying
it is not yet available on this gateway.

**Stage B — the fields, as their capabilities land.** Each moves from refused to honoured with no
change to the contract, because refusing was always the correct behaviour for a field we could not
serve.

The value of the boundary is concrete: every consumer that sends plain text — the majority — can
migrate as soon as Stage A exists, months before the ones that send PDFs. What makes it safe is that
a client is never misled. "Not yet supported here" is information a team can act on; a silently
dropped field is not.

> The one thing Stage A must not do is *approximate*. KIRA applies a model's default thinking when
> the caller sends none (`kira_api.md` §4.3); Stage A must either apply the real default or refuse
> — never quietly send no thinking at all and let the answer be different for reasons nobody can
> see. If the model catalog cannot yet express the default, that model is not in Stage A.

### 5.3 Attribution: the one thing the predecessor does not have

Every AIRA control is scoped to a **use case**. The predecessor has no such concept: a caller is a
user in a group, and that is all. So a KIRA request arrives with no selector, and without one it
cannot be budgeted, limited, priced or reported.

The resolution, in order:

1. **Exactly one use case in the caller's Keycloak groups** → that one. `FRD-102` already derives
   `Principal.use_cases` from `/use-cases/<slug>` groups, so for the common case — an application
   that belongs to one use case — attribution is automatic and the client changes nothing.
2. **An explicit `X-AIRA-Use-Case` header** → that one, if the caller is a member.
3. **Several memberships and no header** → **403 naming the candidates.** Not a guess, and not a
   fallback to an "unattributed" bucket: an unattributed bucket is a hole in every control at once,
   and it would be the path of least resistance for every caller.

This is the one place a migrating client may need a change — one header — and only when its
identity belongs to several use cases. That trade is worth stating in the migration guide up front,
because discovering it during a cut-over is expensive.

### 5.4 Two permission levels onto five roles

The predecessor's "standard user" and "admin" come from configured group names. Ours come from
realm roles (`ADR-0009`). The mapping: standard-user endpoints need a valid principal with a
resolved use case (§5.3); `/ki-usage` needs a governance role — the same rule `FRD-601` already
applies, reusing `visible_scope` rather than a second decision.

`/ki-usage` then becomes a projection of `FRD-601`'s report onto the predecessor's columns
(`user_id`, `model_id`, `entry_count`, `token_input_sum`, `token_output_sum`), with its CSV
negotiation coming from `FRD-602`.

### 5.5 Where we deliberately differ

Two, both security-relevant, both to be listed in the migration guide:

- **`GET /models` requires authentication.** The predecessor's is open. Our catalog reveals which
  models an organisation has approved and what their limits are; that is not public.
- **CORS is not `*` with credentials** (`FRD-117` §5.4).

Anything else discovered during implementation is added here rather than being absorbed silently —
a compatibility surface with undocumented differences is worse than no compatibility surface,
because it is trusted.

### 5.6 A surface with an ending

Per `ADR-0010` Option C: `Deprecation: true` and a `Sunset` date on every response from day one,
the date in the migration guide, and **`surface` as a dimension in the request log** so reporting
can answer "how much traffic still uses this" per use case. Retirement is then a conversation about
a number.

## 6. Data Model

`request_logs.api` already exists and already distinguishes surfaces; it gains the `kira` value.
No migration.

## 7. API / Interface Contract

`kira_api.md` §2 and §4 are the contract; this FRD does not restate them. The mapping table from
KIRA error codes to canonical failures belongs in the implementation and is asserted by tests, one
per code.

## 8. Security & Privacy

- **§5.3 is the security requirement.** An unattributed request would bypass budgets, rate limits
  and per-use-case retention simultaneously. The 403 in case 3 is deliberate and must not be
  softened into a default.
- §5.5: two places we do not copy the predecessor.
- Everything else — key handling, payload storage, retention, redaction — is inherited unchanged,
  because the surface sits above all of it.

## 9. Observability

`aira.api.surface = "kira"` on spans and audit rows; reporting can break down by it (§5.6).

## 10. Testing & Acceptance Criteria

- **Contract tests** — one per endpoint, asserting the response shape field by field against
  `kira_api.md`, and **one per error code** in §6.2 asserting the code and the status. These are
  the tests that make "compatible" a fact rather than a claim.
- **Unit (attribution)** — one membership resolves automatically; a header selects among several; a
  header naming a non-membership is 403; several memberships with no header is **403 naming the
  candidates** and is written to fail first against an implementation that picks one.
- **Unit (controls)** — a KIRA request is rate-limited, budgeted, priced and logged exactly as the
  equivalent Gemini request. Asserted by running both through and comparing the resulting audit
  rows, which is the only way to be sure the surface did not skip a step.
- **Integration** — a real KIRA-shaped request end to end, including SSE, with the audit row
  showing `api = kira`.
- **Mutation** — the unattributed path actually refuses; the deprecation headers are actually
  present; the model-id lookup actually validates.

**Acceptance**
- *Given* a client written against the predecessor, *when* only its base URL is changed, *then* its
  chat, streaming, embedding and model-list calls succeed with identical response shapes, and the
  traffic appears in AIRA's reporting under its use case.
- *Given* a caller whose identity belongs to two use cases and who sends no header, *when* they
  call `/chat`, *then* they receive a 403 naming both, and nothing was dispatched.

## 10a. What Stage A actually shipped (2026-08-06)

`/kira/api/external` with `chat`, `streaming-chat`, `embed`, `models`, `health`, `version-info` and
`ki-usage`; the predecessor's `{code, message, details?}` envelope and its error codes; integer
model ids resolved through `FRD-114`'s alias; attribution per §5.3; `Deprecation` on every response
and `Sunset` where configured.

**Stage A carries documents.** The plan had attachments arriving in Stage B, and `FRD-110` landed
first — refusing a capability we have would be silly. Only `thinking` and `responseSchema` are
refused, plus one case §5.2 singled out and which turned out to be real: **a model whose catalog
entry declares a non-`disabled` default thinking mode is refused**, because the predecessor applies
that default and serving the model with no thinking at all would answer differently for a reason
nobody could see. A model with no thinking declaration, or one whose default is `disabled`, is
unaffected — sending nothing *is* what it asked for.

`/embed` refuses a list and a `task_type` by name rather than approximating: embedding a batch one
at a time would silently cost N requests of quota against a limit of one, and the wrong
optimisation type produces vectors that retrieve measurably worse with nothing in the response to
show it. Both arrive with `FRD-113`.

### The extraction the FRD asked for

§5.1 says the surface shares *"the pre-dispatch gate, the pipeline, the dispatch chain, the audit
writer"*. Sharing them means **extracting** them, and that is `api/serving.py`: the controls now
live outside any surface and both routers use them. Duplicating instead would have been the
`:embedContent` failure in a larger costume — a gate that lived inside one branch rather than on
the path every branch takes, except the branch is now a whole API.

`test_a_kira_request_is_audited_exactly_like_a_gemini_one` is what holds it: it sends one request
through each surface and compares the resulting audit rows, which is the only way to be sure no
step was skipped rather than merely present.

Coverage: 28 contract tests (shape, error codes and refusals, field by field against
`kira_api.md`), 5 integration tests, mutations **K1–K8**, each verified to be caught. Seven existing
mutation anchors followed their functions into `api/serving.py` and were repaired — a mutation whose
anchor has moved protects nothing, which is why the harness reports them rather than skipping.

### Not in Stage A

`ki-usage` reports per **user**, with a model id of `0`. The predecessor keys usage by (user,
model); `FRD-601` aggregates the two dimensions separately, and inventing a cross-tabulation would
be a fabricated figure. Stated in the migration guide rather than approximated. CSV negotiation
arrives with `FRD-602`.

## 10b. Stage B (2026-08-06) — the refusals become service

`FRD-111`, `FRD-112` and `FRD-113` landed, and every field Stage A refused by name is now honoured.
**The wire format did not change**, which is the whole point of having staged it: a client written
against Stage A keeps working and simply stops receiving `NOT_YET_SUPPORTED`.

- `thinking` — validated against the model, refused with the predecessor's own codes
  (`INVALID_THINKING_MODE`, `MISSING_THINKING_TOKEN_COUNT`, `THINKING_TOKEN_COUNT_TOO_LOW`/`_HIGH`).
- A model whose declared default thinking is not `disabled` — the case Stage A singled out and
  refused — now has that default **applied**, which is what closes the difference from the
  predecessor rather than papering over it.
- `responseSchema` — served, and refused with a named field when the schema is one we do not
  understand, or with a capability refusal when no dispatchable model can honour it.
- `/embed` — lists, task types (defaulting to `RETRIEVAL_QUERY` as the predecessor does, where the
  model declares it), and per-model dimensionality.

Two things Stage B does *not* pretend to have settled, both stated in the wire format rather than
in a comment: a batch answers under **`vectors`** (`FRD-113` §11's open question), and `ki-usage`
still reports per user with a model id of `0` (`FRD-601` aggregates the two dimensions separately;
a cross-tabulation would be a fabricated figure).

The refusal rule survives the change and only moves. "Not built yet" is gone; **"this model cannot"
is not**, and it fails exactly as loudly.

## 11. Dependencies & Risks

- **`ADR-0010`** (blocking), then `FRD-110`–`FRD-114`.
- **Risk — the surface outlives its purpose.** The mitigation is §5.6 and nothing else; a sunset
  date with no measurement is a wish.
- **Risk — `kira_api.md` is a description, not the source.** Details will differ from the running
  predecessor. Contract tests should be validated against **captured real traffic** where possible,
  not only against the document. The embedding aggregation question in `FRD-113` §11 is a known
  instance and will not be the only one.

## 12. Rollout / Demo

A migration guide covering the base-URL change, the `X-AIRA-Use-Case` header (§5.3), the two
deliberate differences (§5.5) and the sunset date. Demo mode serves the surface against the mock
so a consumer can test their client before any cloud credentials exist.
