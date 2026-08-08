# FRD-133 — Prompt caching: written down now, built after the agent work

> Phase: 9 · Status: **Draft — deliberately not next** · Owner: Vadim Scheibe · Last updated: 2026-08-08
> Related: `FRD-131` (tool calling), `FRD-132` (the assistant surface), `FRD-403` (cost),
> `FRD-119` §usage (Anthropic cache tokens), `ADR-0012` (capabilities say *whether*, never *how*),
> `ADR-0013` (no conversation state)

## 1. Summary

A coding assistant sends the same large prefix on every turn: a long system prompt, the tool
declarations, and the file context it has gathered. Twenty turns means twenty full-price inputs of
almost identical text. Every major provider now sells a way out of that, and AIRA offers none:
`cachedContent` is **refused by name** at the Gemini surface, and no `cache_control` is ever sent to
any upstream. Cache *tokens* are counted when a provider reports them (`FRD-119`), so the accounting
is already honest — there is simply never anything to count.

This FRD describes carrying a caller's cache directive to the providers that have one, and pricing
the result correctly.

**It is deliberately not built first.** The agent work (`FRD-131`, `FRD-132`) must stand on its own
merits, at full price, and be measured that way. A feature justified by a saving that has not been
measured is a feature justified by a spreadsheet. Once assistants are actually running against the
gateway, the real number — how much of a turn is repeated prefix — comes out of `request_logs`
rather than out of an estimate, and *that* is what should decide the shape and the priority.

## 2. Why the current refusal is defensible, and where it stops being so

`FRD-124` refuses `cachedContent` for two stated reasons: context caching is conversation state
(`ADR-0013`), and ignoring it would mean *"billing at uncached rates while the caller expected
cached ones"*. The second reason is exactly right and is an argument for building this properly, not
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
- Declare the capability in the catalog: `ADR-0012` — flags say *whether*, never *how*.
- Never let a cache decision change the *answer* silently.

**Non-Goals**

- **Provider-side stored context** (Google `cachedContent`, and any equivalent). Refused, and §2
  says why.
- **A cache the gateway operates.** Storing prompts or responses to serve later would put us in the
  answer path, make retention (`FRD-404`) unenforceable against our own store, and produce answers
  no audit row can explain. `ADR-0013`.
- **Automatic cache placement.** Deciding where a prefix ends is the caller's knowledge of their own
  prompt, not ours.

## 4. Functional Requirements (draft — to be revised against the measurement)

- **FR-1** `CanonicalRequest` carries a cache marker at **part** granularity: "everything up to here
  is stable". One flag on a part, not a separate structure — the same lesson as `FRD-110`'s ordered
  parts.
- **FR-2** Catalog capability `prompt_caching`. Undeclared means unsupported; a candidate that
  cannot do it is **not** skipped — unlike attachments or tools, a missing cache changes the *price*
  and not the *answer*, so the correct behaviour is to serve it uncached and **record that it was
  served uncached**. This is the one place in the codebase where a capability gap is not a skip, and
  the reason has to be written beside it or somebody will "fix" it into a skip.
- **FR-3** Each dialect maps it or ignores it explicitly:
  - **Anthropic**: `cache_control: {"type": "ephemeral"}` on the marked block. Usage already
    separates `cache_creation_input_tokens` and `cache_read_input_tokens`.
  - **OpenAI**: prefix caching is automatic and reported in usage; nothing to send, something to
    *count*.
  - **Gemini**: implicit caching where the model supports it; `cachedContent` stays refused.
- **FR-4** `request_logs` records cached-read, cache-write and uncached input tokens **separately**,
  and `FRD-403` prices each at its own rate. A cache write costs *more* than plain input on some
  providers — folding them together would make a cost-control feature quietly wrong in the expensive
  direction.
- **FR-5** Reporting shows the cache hit share. Without it nobody can tell a working cache from a
  broken one, and a silently broken cache looks exactly like an expensive month.
- **FR-6** Budget reservation (`FRD-405`) reserves at the **uncached** rate and settles at the real
  one. Erring high is the safe direction for a spend limit, as it already is for output tokens.

## 5. What must be measured before this is designed further

From real assistant traffic (`FRD-132` stage A onwards), out of `request_logs`:

| Number | Why it decides something |
|---|---|
| Repeated-prefix share per turn | If it is small, this feature is not worth its complexity. |
| Turns per instruction | Determines whether an ephemeral (5-minute) cache window even survives a session. |
| Spend split input/output for assistant traffic | A use case dominated by *output* is not helped by input caching at all. |

**The honest possibility this FRD must leave open: the measurement says don't build it.** A five
minute window against an assistant that pauses while a human reads a diff may hit far less often
than the provider's marketing implies, and the accounting complexity in FR-4 is permanent.

## 6. Security & Privacy

A cache marker is metadata about content we already carry — no new data leaves the boundary. Two
things to state anyway:

- **Cached content is content.** It stays inside `store_payloads` and retention exactly as before;
  a caller must not be able to conclude that a cached prefix is somehow not stored.
- **A cache is a side channel in principle.** Providers scope theirs per credential/organisation.
  Since AIRA holds one credential per platform for many use cases, a shared cache is worth checking
  per provider before enabling — a prefix from one use case must not become a timing signal for
  another. That check belongs in this FRD's design phase and is the reason it is named here.

## 7. Testing & Acceptance Criteria

- **Unit**: the marker survives the canonical round trip; each dialect maps or ignores it
  explicitly; a cache-incapable candidate is served **uncached and recorded as such**, not skipped.
- **Mutation**: FR-2's non-skip (a mutation turning it into a skip must be caught), and the
  three-way token split in pricing.
- **Integration**: two identical requests in succession against a caching-capable provider, with the
  **second** row showing cache-read tokens — asserted in Postgres, since the response is the same
  either way. That is also the only test that can tell a working cache from a well-formed request
  nobody honoured.

**Acceptance**: a cached turn costs less than an uncached one *in the reporting figures*, and the
gateway says which turns were cached. Anything less is a feature that claims a saving nobody can
verify.

## 8. Order

After `FRD-131` and `FRD-132`, on the owner's decision (2026-08-08): the agent work goes ahead
**without assuming any saving**, and this is picked up once there is an assistant running on a host
where Ollama and OpenCode can be exercised together.
