# FRD-111 — Thinking control

> Phase: 8 (KIRA parity) · Status: **Done (2026-08-06)** · Owner: Vadim Scheibe · Last updated: 2026-08-06
> Origin: `kira_api.md` §4.3 (`ThinkingSetting` / `ThinkingConfig`), programme: `ADR-0010`.
> Depends on: `FRD-114` (a model must declare what it allows). Touches `FRD-401`/`FRD-403`/`FRD-405`.

## 1. Problem

Reasoning models spend a variable, caller-controllable amount of effort before answering. The
predecessor exposes that as a first-class request field with seven modes — `disabled`, `limited`
(with an explicit token budget), `auto`, and the abstract levels `high`/`medium`/`low`/`minimal` —
validated against what each model actually supports, with a per-model default when the caller says
nothing.

AIRA has no notion of it. A caller cannot ask for less thinking on a cheap classification, or for
more on a hard question, and every request gets whatever the model does by default.

The reason this matters more than it first appears is **money**. Thinking tokens are billed as
output tokens, and the predecessor's own configuration allows budgets up to 32 768 of them — an
order of magnitude more than a typical answer. A gateway that enforces spend limits (`FRD-403`)
and reserves against them before dispatch (`FRD-405`) cannot treat the most expensive knob on the
request as invisible.

## 2. Goals & Non-Goals

**Goals**
- A caller chooses a thinking mode, and where the mode takes a budget, a token count.
- What each model permits is **declared by the model**, and a request outside it is refused with a
  reason that says which bound it broke.
- Absent a setting, the model's declared default applies — not the provider's, and not none.
- Budget reservation and cost accounting include the thinking budget, because the bill does.

**Non-Goals**
- **Returning the model's thoughts.** Google can emit them (`includeThoughts`); the predecessor
  does not return them and neither will we. With Anthropic this stops being a matter of not asking:
  thinking blocks come back in the response by default, so the adapter must **drop** them and the
  obligation becomes active rather than passive — `FRD-119` §5.4, with its own test asserting they
  reach no response, log, span or audit row. Chain-of-thought is the least reviewed text a model
  produces and the most likely to contain the input restated; putting it in a response the gateway
  also persists is a data-protection decision, not a feature toggle. Out of scope, deliberately.
- **Per-use-case policy on thinking** (e.g. "this use case may not exceed `low`"). A natural
  follow-up through the pipeline config; not needed for parity.
- Inventing modes a provider does not have.

## 3. User Stories
- As an **application developer**, I want to cap thinking on a high-volume classification so that
  it stays cheap, and raise it on an analytical request so that it is right.
- As a **use-case administrator**, I want a request that asks for a large thinking budget to be
  reserved against my budget accordingly, so that one expensive caller cannot exhaust the month
  before the counter notices.

## 4. Functional Requirements

- **FR-1 Request field.** An optional thinking setting: a mode, and a token count that is required
  when the mode is `limited` and rejected otherwise.
- **FR-2 Modes.** `disabled`, `limited`, `auto`, `high`, `medium`, `low`, `minimal` — the
  predecessor's set. Which of them a given model accepts comes from `FRD-114`.
- **FR-3 Validated against the model.** A mode the model does not declare is refused. A `limited`
  budget below the model's minimum or above its maximum is refused. Each is a distinct error so a
  client can tell them apart (`INVALID_THINKING_MODE`, `MISSING_THINKING_TOKEN_COUNT`,
  `THINKING_TOKEN_COUNT_TOO_LOW`, `THINKING_TOKEN_COUNT_TOO_HIGH`).
- **FR-3a The budget must fit inside the output allowance.** Anthropic draws thinking tokens from
  `max_tokens`, so a budget at or above it produces a request that can never succeed. Validated
  before dispatch, and `FRD-114`'s declaration is checked for the same consistency when it is
  authored — the catalog must not be able to hold a combination that cannot work.
- **FR-4 Default.** No setting in the request → the model's declared default, which may itself be
  `disabled`. A model that declares no thinking support at all with a request that asks for it is
  FR-3's first case.
- **FR-5 Reservation includes the thinking budget.** See §5.3.
- **FR-6 Cost accounting is unchanged in mechanism.** Thinking tokens arrive inside the upstream's
  reported output token count, so `FRD-403`'s pricing needs no change — but this must be
  *verified* against the real upstream rather than assumed, because if the provider reported them
  separately the recorded cost would be understated (§10).
- **FR-7 Not streamed as content.** A model that is thinking produces no output deltas; the stream
  simply has a longer time to first token. No synthetic progress events (see §5.4).

## 5. Design & Architecture

### 5.1 Canonical, not Gemini-shaped

The setting lives on `CanonicalRequest`:

```python
class ThinkingMode(StrEnum):
    DISABLED = "disabled"
    LIMITED = "limited"
    AUTO = "auto"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    MINIMAL = "minimal"


class Thinking(BaseModel):
    mode: ThinkingMode
    tokens: int | None = None
```

`mode` + `tokens` is the shape both the predecessor's clients and Google's newer models use, and
keeping it canonical means the KIRA surface (`FRD-107`) and the Gemini surface map onto one
concept rather than two.

### 5.2 The abstract levels are a provider concern

`high`/`medium`/`low`/`minimal` mean nothing to an HTTP call. Some providers take a token budget,
some take a level, and the mapping between them differs per model. That translation therefore
belongs in the **upstream adapter**, alongside every other provider-specific decision:

- Gemini: `generationConfig.thinkingConfig.thinkingBudget` — `disabled` → `0`, `auto` → `-1`,
  `limited` → the caller's count, an abstract level → the model's declared budget for that level.
- Anthropic (`FRD-119`): `thinking{type:"enabled",budget_tokens}` — no `auto` equivalent, so that
  mode resolves to the model's declared default budget, and every budget is bounded by FR-3a.
- Azure OpenAI (`FRD-120`): `reasoning_effort` — an **abstract level with no token budget at all**,
  so `limited` has no equivalent and is refused by capability rather than approximated. Worth
  noting because it validates the canonical shape: `mode` + optional `tokens` was taken from the
  predecessor's vocabulary and covers a vendor it was not written for.
- Mock: honours the setting deterministically so the mode is observable in tests without a cloud.

Putting the level→budget table in `FRD-114`'s model metadata rather than in code is what keeps a
new model from being a code change.

### 5.3 The reservation must see the budget

`_estimate` currently reserves `maxOutputTokens or <default>` at the output rate. With thinking,
the tokens a request can consume are **the answer plus the thinking budget**, and the thinking
budget is the larger of the two whenever anyone uses this feature seriously.

So the estimate becomes `output_estimate + resolved_thinking_budget`, where the resolved budget is
the number after FR-4's defaulting and FR-3's validation — i.e. the same number that will be sent
upstream. For `auto` and the abstract levels, where no explicit count exists, the model's declared
maximum for that level is used: conservative in the safe direction, and settled against the real
figure the moment the response returns, exactly as the output estimate already is.

A `disabled` request reserves what it does today. Nothing gets more expensive by accident.

### 5.4 Why there are no progress events

The predecessor's `/streaming-chat` emits `UpdateEvent`s during the thinking phase — a status line
so a UI can show something is happening. That exists because the wait would otherwise be silent.

We are not adding it. Server-sent events that carry no model output are a second thing for every
client to parse, and the honest signal for "still working" is the connection being open. If the
KIRA surface (`FRD-107`) is built and a consumer depends on those events, it can synthesise a
heartbeat at that surface — a compatibility concern belongs in the compatibility layer, not in the
canonical core.

## 6. Data Model

None in the gateway. The per-model declaration lives in `FRD-114`'s catalog and reaches the
gateway over the existing Kafka read-model path.

## 7. API / Interface Contract

Gemini surface — Google's own shape, so no invention:

```json
{ "contents": [...],
  "generationConfig": { "thinkingConfig": { "thinkingBudget": 4096 } } }
```

AIRA additionally accepts the canonical `{"mode": "...", "tokens": ...}` form under
`generationConfig.thinkingConfig` so that the abstract levels are reachable from the Gemini
surface too; a request carrying both is a 400 rather than a silent precedence rule.

Errors: the four codes in FR-3, as `400 INVALID_ARGUMENT` on the Gemini surface with a message
that names the bound and its value.

## 8. Security & Privacy

- **Thoughts are never requested, returned, logged or persisted** (§2 Non-Goals). Should
  `includeThoughts` ever be wanted, it is a new decision with a data-protection review, not a
  parameter.
- A large thinking budget is a cost-amplification vector — one request, thirty thousand billed
  output tokens. FR-5 is what makes it a bounded one, and the per-model maximum (`FRD-114`) is the
  hard ceiling above the caller's own request.

## 9. Observability

`aira.thinking.mode` and `aira.thinking.budget` as span attributes, and the resolved budget on the
audit row — otherwise "why did this month cost twice as much" is unanswerable from the data.

## 10. Testing & Acceptance Criteria

- **Unit** — each mode maps to the expected upstream value; `limited` without `tokens` is refused;
  a budget one below the minimum and one above the maximum are each refused with their own code;
  an unsupported mode is refused; no setting resolves to the model's default; a model that
  declares no thinking refuses a request that asks for it.
- **Unit (budgets)** — a request with a 20 000-token thinking budget reserves materially more than
  the same request without one, and settles to the real usage afterwards.
- **Integration** — against the real upstream: a `disabled` and a large-budget request are both
  dispatched, and **the recorded output token count for the large-budget one is visibly higher**.
  This is FR-6's verification and the reason it is an integration test: it is the only way to find
  out whether the provider folds thinking into output tokens or reports it apart, and the cost
  figures are wrong if we guessed.
- **Mutation** — the validation actually bounds (min and max separately); the default is actually
  applied; the reservation actually includes the budget.

**Acceptance**
- *Given* a model declaring `high|medium|low|minimal` with default `medium`, *when* a caller sends
  no thinking setting, *then* the upstream receives the budget for `medium`.
- *Given* the same model, *when* a caller asks for `limited` with 999 999 tokens, *then* the
  request is refused with `THINKING_TOKEN_COUNT_TOO_HIGH` naming the maximum, and nothing was
  dispatched or reserved.

## 10a. What was built (2026-08-06)

`aira_gateway/thinking.py` resolves and validates; both surfaces call it, and the four codes are
the predecessor's. The Gemini surface accepts Google's `thinkingBudget` **and** the canonical
`mode`/`tokens` (both at once is a 400, not a precedence rule); the KIRA surface accepts the
predecessor's `ThinkingSetting`. Adapters translate: Gemini a budget with `0`/`-1` as the two
sentinels, Anthropic a `thinking{type:"enabled",budget_tokens}` block bounded by FR-3a.

Three decisions worth keeping:

- **`None` and `disabled` are different answers.** The first means the model was never going to
  think; the second means it *would have* and this request is switching it off. Collapsing them
  lets a model whose declared default is `auto` silently ignore a caller asking for none.
- **Resolution happens after routing, before the reservation.** The number sent upstream and the
  number reserved against are then the same one. Resolving before the pipeline would validate a
  budget against a model that never sees the request.
- **A candidate that cannot honour the resolved setting is skipped** (`ThinkingHonoured`), for the
  same reason a chain may not drop an attachment: less reasoning than was asked for is not an
  error, it is a worse answer with a 200 on it.

FR-6's verification is still owed — whether the provider folds thinking into the reported output
tokens or reports it apart can only be learned against the real upstream, and the recorded cost is
understated if we guessed. The mock reports them as output tokens, which is the assumption.

Mutations **T5–T10**; unit and route tests including the reservation pair (a 20 000-token budget
reserves exactly 20 000 more than the same request without one).

## 11. Dependencies & Risks

- **Hard dependency on `FRD-114`.** Without per-model declarations there is nothing to validate
  against, and validation is most of this feature.
- **Risk** — the level→budget mapping is provider-specific and will drift as models change. It is
  configuration (`FRD-114`), which is the mitigation.
- **Open** — whether any consumer uses `auto`. It costs nothing to support and maps to Google's
  `-1`, so it stays.

## 12. Rollout / Demo

The mock declares a full thinking config and reports the resolved budget in its deterministic
answer, so the whole validation matrix is demonstrable without cloud access.
