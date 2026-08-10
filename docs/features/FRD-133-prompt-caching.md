# FRD-133 — Prompt caching: written down now, built after the agent work

> Phase: 9 · Status: **Built (2026-08-10)** — both stages · Owner: Vadim Scheibe · Last updated: 2026-08-10
> Related: `FRD-131` (tool calling), `FRD-132` (the assistant surface), `FRD-403` (cost),
> `FRD-119` §usage (Anthropic cache tokens), `ADR-0012` (capabilities say _whether_, never _how_),
> `ADR-0013` (no conversation state)

## 1. Summary

A coding assistant sends the same large prefix on every turn: a long system prompt, the tool
declarations, and the file context it has gathered. Twenty turns means twenty full-price inputs of
almost identical text. Every major provider now sells a way out of that, and AIRA offers none:
`cachedContent` is **refused by name** at the Gemini surface, and no `cache_control` is ever sent to
any upstream. Cache _tokens_ are counted when a provider reports them (`FRD-119`), so the accounting
is already honest — there is simply never anything to count.

This FRD describes carrying a caller's cache directive to the providers that have one, and pricing
the result correctly.

**It is deliberately not built first.** The agent work (`FRD-131`, `FRD-132`) must stand on its own
merits, at full price, and be measured that way. A feature justified by a saving that has not been
measured is a feature justified by a spreadsheet. Once assistants are actually running against the
gateway, the real number — how much of a turn is repeated prefix — comes out of `request_logs`
rather than out of an estimate, and _that_ is what should decide the shape and the priority.

## 2. Why the current refusal is defensible, and where it stops being so

`FRD-124` refuses `cachedContent` for two stated reasons: context caching is conversation state
(`ADR-0013`), and ignoring it would mean _"billing at uncached rates while the caller expected
cached ones"_. The second reason is exactly right and is an argument for building this properly, not
against building it.

The first reason needs splitting, and the distinction is the whole design:

- **Google's `cachedContent`** is a handle to content **stored on the provider's side**, created by
  a separate API call and referred to later. That is server-side state we did not route and cannot
  fully account for — `ADR-0013`'s "no conversation state" applies squarely, and the refusal stands.
- **Anthropic's `cache_control`** and the OpenAI-side automatic prefix caching are **markers on
  content the caller sends every time**. Nothing is stored on our behalf, nothing is referred to
  later, the request remains complete in itself. That is not conversation state; it is a hint about
  a request we are already carrying in full.

So the rule this FRD proposes: **a cache directive that keeps the request self-contained is
carried; a cache handle that makes the provider hold state for us is refused.** `ADR-0013` needs one
sentence added to say that, because as written it reads as though both are closed.

## 3. Goals & Non-Goals

**Goals**

- Carry an ephemeral cache marker to the dialects that support one.
- Price cached and uncached input **apart**, and report them apart.
- Declare the capability in the catalog: `ADR-0012` — flags say _whether_, never _how_.
- Never let a cache decision change the _answer_ silently.

**Non-Goals**

- **Provider-side stored context** (Google `cachedContent`, and any equivalent). Refused, and §2
  says why.
- **A cache the gateway operates.** Storing prompts or responses to serve later would put us in the
  answer path, make retention (`FRD-404`) unenforceable against our own store, and produce answers
  no audit row can explain. `ADR-0013`.
- ~~**Automatic cache placement.**~~ **Reversed by the measurement (2026-08-10).** This said
  deciding where a prefix ends is the caller's knowledge, not ours. §5.1 shows the boundary is not
  a judgement at all: the stable content is the **tool declarations and the system instruction**,
  which are separate fields in every dialect's request and are stable _by construction_. Marking
  them is not guessing where somebody's prompt stops; it is marking two boundaries the API itself
  draws. Nothing marks anything inside `contents`, where a guess would be needed — and nothing is
  marked at all unless the use case opts in (FR-7).

## 4a. What each provider actually does (from vendor documentation, 2026-08-10)

Checked against primary sources rather than recalled, because every requirement below depends on
one of these cells.

| Provider                     | Mechanism                                                                                                                                               | Caller sends | Reports                                                                                                                             | Price                                               |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------ | ----------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------- |
| **Anthropic** (incl. Vertex) | `cache_control: {"type":"ephemeral"}` on blocks of `tools`, `system`, `messages`; **max 4 breakpoints**; prefix hierarchy **tools → system → messages** | **yes**      | `cache_creation_input_tokens`, `cache_read_input_tokens`, `input_tokens`; `cache_creation.{ephemeral_5m,ephemeral_1h}_input_tokens` | write **1.25×** (5 m) / **2×** (1 h); read **0.1×** |
| **Gemini**                   | implicit, on by default for 2.5+                                                                                                                        | nothing      | `cachedContentTokenCount`                                                                                                           | savings passed on automatically                     |
| **Azure OpenAI / Foundry**   | automatic prefix caching from 1024 tokens, then 128-token increments                                                                                    | nothing      | `prompt_tokens_details.cached_tokens`                                                                                               | discounted read; writes chargeable on GPT-5.6+      |
| **Ollama (self-hosted)**     | KV prefix reuse in the runtime                                                                                                                          | nothing      | **nothing** — verified against the running server                                                                                   | no billing at all                                   |

Three consequences the requirements below are built on:

- **Three of the four report cached tokens and only one takes a marker.** So the accounting is the
  broadly useful half and the marker is Anthropic-specific — the opposite of the intuition that the
  marker is the feature.
- **Minimum lengths decide whether anything is cached at all**: Anthropic 1024–4096 by model,
  Gemini 2048–4096, Azure 1024. The measured assistant turns report **2050** prompt tokens, which is
  _above_ some minimums and _below_ others. On a model with a 4096 minimum nothing is ever cached
  and no error is returned — "enabled and never hit" must therefore be visible, or it reads as a
  defect in the gateway.
- **On Ollama there is nothing to count and nothing to bill.** Caching there buys latency. Saying so
  is part of the feature: a reporting screen that shows 0 % cached for a self-hosted model is
  correct, not broken.

## 4b. Cache isolation, which §6 asked to check

**Answered for Vertex: organisation-level isolation only.** Anthropic isolates caches per workspace
on the Claude API, AWS and Microsoft Foundry, and **per organisation on Bedrock and Google Cloud**.
AIRA holds one credential per platform for many use cases, so on Vertex every use case shares one
cache scope.

Content is addressed by hash, so nothing is _readable_ across the boundary — but a hit is faster
than a miss, so in principle one use case can learn that another recently sent an identical prefix.
For the traffic this feature targets — a public tool schema and a published assistant system prompt
— that is not a secret worth protecting. For a use case whose _system prompt itself_ is
confidential it might be. Hence FR-7.

## 4. Functional Requirements (revised against the measurement, 2026-08-10)

- **FR-1** `CanonicalRequest` carries a cache marker at **part** granularity: "everything up to here
  is stable". One flag on a part, not a separate structure — the same lesson as `FRD-110`'s ordered
  parts.
- **FR-2** Catalog capability `prompt_caching`. Undeclared means unsupported; a candidate that
  cannot do it is **not** skipped — unlike attachments or tools, a missing cache changes the _price_
  and not the _answer_, so the correct behaviour is to serve it uncached and **record that it was
  served uncached**. This is the one place in the codebase where a capability gap is not a skip, and
  the reason has to be written beside it or somebody will "fix" it into a skip.
- **FR-3** Each dialect maps it or ignores it explicitly:
  - **Anthropic**: `cache_control: {"type": "ephemeral"}` on the marked block. Usage already
    separates `cache_creation_input_tokens` and `cache_read_input_tokens`.
  - **OpenAI**: prefix caching is automatic and reported in usage; nothing to send, something to
    _count_.
  - **Gemini**: implicit caching where the model supports it; `cachedContent` stays refused.
- **FR-4** `request_logs` records cached-read, cache-write and uncached input tokens **separately**,
  and `FRD-403` prices each at its own rate. A cache write costs _more_ than plain input on some
  providers — folding them together would make a cost-control feature quietly wrong in the expensive
  direction.
- **FR-5** Reporting shows the cache hit share. Without it nobody can tell a working cache from a
  broken one, and a silently broken cache looks exactly like an expensive month.
- **FR-6** Budget reservation (`FRD-405`) reserves at the **uncached** rate and settles at the real
  one. Erring high is the safe direction for a spend limit, as it already is for output tokens.
- **FR-7** Caching is **per use case, default off**, like `tools_enabled` (`FRD-131`). Least
  privilege is the lesser reason; the real one is §4b — on Vertex the cache scope is the whole
  organisation, and a use case whose system prompt is confidential should not be opted in by
  somebody else's cost decision.
- **FR-8** A model's catalog entry declares `prompt_caching` **and its minimum**, because "enabled
  and never hit" and "not supported" look identical in a report and have different fixes. The
  minimum is a number the vendor publishes per model, so it belongs beside the capability rather
  than in code — `FRD-114`'s rule: a capability belongs to a model, not to a family or a runtime.
- **FR-9** The use case's administrator owns both settings from the console: the switch, and the
  **lifetime**. Nothing else is exposed, because nothing else has a trade-off the caller can
  resolve — and each control states, in the console, what it costs rather than what it does.
- **FR-10** The cache share is visible **where the spend is**, so the effect of a change can be
  observed without going looking for it. A saving nobody can see is `FRD-125`'s absent control
  wearing a present one's badge, one field over.

## 5. The measurement (2026-08-10)

Taken from `request_logs` — 26 served turns of real OpenCode traffic in `coding-assistant`, not an
estimate. All three numbers point the same way, so the possibility this section was written to
leave open — _the measurement says don't build it_ — did not happen.

| Number                    | Result                                                | What it decides                               |
| ------------------------- | ----------------------------------------------------- | --------------------------------------------- |
| Repeated content per turn | **99.1 %** median on the large turns (97.6 % overall) | Worth building                                |
| Gap between turns         | median **41 s**; 13 of 14 within five minutes         | An ephemeral window survives comfortably      |
| Input share of tokens     | **93.3 %** (26,610 prompt / 1,924 completion)         | Input caching reaches nearly all of the spend |

### 5.1 It is not the conversation

The assumption this feature is usually justified by — _the whole conversation is resent every turn_
— is **wrong here**, and the correction changes where the marker goes:

```
tools              ~21.5 KB   69 %   tool declarations, byte-identical every turn
systemInstruction   ~9.7 KB   31 %   the assistant's system prompt, identical every turn
contents           47–1633 B  0.1–5 %  the actual conversation
```

The conversation is the _smallest_ part. What repeats is the tool declarations and the system
prompt — which is precisely Anthropic's cache hierarchy (`tools` → `system` → `messages`), so two
breakpoints capture the 99 %.

### 5.2 A measurement that measured the wrong thing first

The first attempt compared the **common string prefix** of consecutive stored payloads and reported
**0.5 %** — which would have killed the feature. It was wrong: the serialisation puts `contents`
(the part that varies) _before_ `systemInstruction` (the 30 KB that does not), so it measured JSON
key order rather than repeated content. JSON object order carries no meaning to a provider.

Worth recording because the wrong number was the plausible one, and it pointed at "do not build".

## 6. Security & Privacy

A cache marker is metadata about content we already carry — no new data leaves the boundary. Two
things to state anyway:

- **Cached content is content.** It stays inside `store_payloads` and retention exactly as before;
  a caller must not be able to conclude that a cached prefix is somehow not stored.
- **A cache is a side channel in principle.** Providers scope theirs per credential/organisation.
  Since AIRA holds one credential per platform for many use cases, a shared cache is worth checking
  per provider before enabling — a prefix from one use case must not become a timing signal for
  another. That check belongs in this FRD's design phase and is the reason it is named here.

## 6a. As built (2026-08-10)

**Stage A — the accounting.** `CanonicalUsage` carries `cached_input_tokens` and
`cache_write_tokens` as **subsets** of `prompt_tokens`, so every existing budget, report and index
keeps meaning what it meant. All four dialects read what their provider actually sends; the catalog
holds a cached-read and a cache-write price per model, and an undeclared one falls back to the
ordinary input rate — never to zero, `FRD-403`'s rule one field in. Reporting sums the cached
tokens. Migration `0025`.

**Stage B — the marker.** `CanonicalRequest.cache_prefix`, set by the layer from the use case's
configuration after routing (so it is decided per hop, like every other capability question), and
mapped by the Anthropic dialect onto **two** breakpoints: the system block and the **last** tool.
Not one per tool — a breakpoint means "everything up to here", and four is the limit, so one per
tool would exhaust the budget on the fourth function and cache almost nothing. Five-minute
lifetime, because the measured gap between turns is 41 seconds and an hour costs double.
Migrations `0026` and Django `0007`.

**Stage C — the console, and the one parameter worth tuning.** Both `prompt_caching_enabled` and
`tools_enabled` are switches on the use case's settings now. `tools_enabled` had existed only in
the API since `FRD-131` — `FRD-206`'s defect inverted: not a control that refuses when used, which
at least complains, but a capability with no way in, which nobody notices because nothing fails.

The **lifetime** (`prompt_cache_ttl`, `5m` or `1h`) is the only parameter with a genuine trade-off,
and it is the only one exposed. Everything else about caching is either fixed by the vendor (where
a breakpoint may sit, how many there are, the minimum prefix length) or already decided by the
measurement (which parts of a request are stable). Offering knobs for those would be offering
choices with one correct answer, which is a form of the `FRD-206` complaint: a control that cannot
usefully be used.

The lifetime is different because **only the caller's own traffic settles it**. An hour costs
about twice the ordinary input price to write against roughly a quarter extra for five minutes, so
it pays only where the gap between turns regularly exceeds five minutes. §5 measured 41 seconds
for OpenCode, with 13 of 14 gaps inside five minutes — but a use case whose turns are a human
reading between them has the opposite profile, and no default can know which one it is. So: five
minutes by default, an hour available, and the explanation beside it says what each costs rather
than that one is longer than the other.

**Every parameter carries its explanation** (`FRD-207`'s `InfoHint`), including the two price
fields in the model editor, where the direction is the thing that surprises: cached input is
_cheaper_ (a tenth on Anthropic) and a cache write is _more expensive_ (1.25x or 2x). A field
labelled only "cached input price" invites somebody to type the ordinary rate into it, which is
exactly the fallback that already applies when it is empty — and then the figure looks deliberate.

**Measuring the effect is on the same page as the setting.** The consumption panel on the use
case's overview shows a **Cached** share beside spend, requests and tokens. That is what makes the
tuning empirical rather than a matter of opinion: change the lifetime, send traffic, watch the
share and the spend. The hint says what 0 % means, because there are four different reasons for it
and they have four different fixes — the prefix changes between turns, the gap exceeds the
lifetime, the model does not cache, or the provider reports nothing (every self-hosted one).

## 7. Testing & Acceptance Criteria

- **Unit**: the marker survives the canonical round trip; each dialect maps or ignores it
  explicitly; a cache-incapable candidate is served **uncached and recorded as such**, not skipped.
- **Mutation**: FR-2's non-skip (a mutation turning it into a skip must be caught), the three-way
  token split in pricing, and the **journey** each console setting makes (`U7`-`U9`) — a checkbox
  becomes an event, a read-model row and a post-routing lookup, and a setting dropped at any of
  those hops produces a served request that looks exactly like one nobody asked to cache.
- **Wiring, not only requirement** (`FRD-124`'s lesson): the mapping tests prove the marker is
  built correctly and cannot see whether the configuration ever reaches it. So the mock **says
  what it was asked** — `[cache:1h]` — the way it already does for thinking and attachments, and
  three tests drive the whole path through the route. The mock reports no cache _hit_, deliberately:
  fabricating one would make every "caching saves money" assertion true by construction.
- **Integration**: two identical requests in succession against a caching-capable provider, with the
  **second** row showing cache-read tokens — asserted in Postgres, since the response is the same
  either way. That is also the only test that can tell a working cache from a well-formed request
  nobody honoured.

**Acceptance**: a cached turn costs less than an uncached one _in the reporting figures_, and the
gateway says which turns were cached. Anything less is a feature that claims a saving nobody can
verify.

## 8. Order

After `FRD-131` and `FRD-132`, on the owner's decision (2026-08-08): the agent work went ahead
without assuming any saving, and §5 is that measurement.

Built in two stages, because they have different reach and only one of them needs a provider:

- **Stage A — the accounting.** Cached-read, cache-write and uncached input recorded and priced
  apart, and reported. Provider-independent, changes no request, and benefits three of the four
  providers immediately because they already report the numbers. It is also what makes the saving
  _verifiable_, which §7's acceptance criterion requires: without it a cached turn and an uncached
  one cost the same in AIRA's own figures.
- **Stage B — the marker.** The canonical cache marker, the catalog capability, and the Anthropic
  mapping. This is where a saving is _created_ rather than merely observed.

Doing B first would produce a real saving at the provider that is invisible — and partly
mis-priced — in the reporting the platform exists to provide.
