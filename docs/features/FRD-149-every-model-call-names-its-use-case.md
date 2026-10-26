# FRD-149 — Every model call names its use case, and the console says how to connect

> Phase: 6 (governance) · Status: **Built** · Owner: Vadim Scheibe
>
> Origin: the owner: a person who is in only one use case could call it without naming the use case
> in the URL; the user should always have to enter the use case — asked whether that covers API keys
> as well, the owner decided it does. And the description of the connection on the use case's
> overview should be current, understandable, and show every way to connect.
> Decision: `ADR-0045`. Related: `FRD-102` (attribution and the selector), `FRD-107` §5.3 (the KIRA
> surface's attribution), `FRD-205` (a key bound to a use case), `ADR-0015` (the exemptions),
> `FRD-206` (the connection panel), `FRD-132`/`FRD-144` (OpenCode and its budget plugin),
> `FRD-139` (`/v1beta/me/budget`).

## 1. Summary

A request got its use case three ways: a selector (`/uc/<slug>` or `X-AIRA-Use-Case`), a bound API
key's own use case where it named none, and — on the KIRA surface — a person's only membership. The
last two made a request's meaning depend on the state of its credential: a client worked until its
user was given a second use case, and a person with keys for two use cases billed whichever a tool
happened to be configured with, with nothing in the request saying which.

Now **a model call names its use case, on both surfaces, whatever the credential.** A key's binding
checks the name and no longer supplies it. Readings name none. And the console's *Connecting a
client* panel is rewritten around that: every address it shows carries the use case, and it has a
request for every way in.

## 2. Goals & Non-Goals

**Goals**
- One rule for every credential and both surfaces, in one place (`must_name_a_use_case`).
- The refusal is the fix: it names the prefix and the header, and which use case to name — a key's
  own, or those the person reaches.
- Everything the product hands out names the use case: the console's OpenCode configuration and
  examples, the showcase's configuration, its demo traffic and printed examples, the load test.
- The connection panel: the address of each interface for this use case, both credentials, the
  models by name and by KIRA id, and a ready request for curl, Python, TypeScript, OpenCode and a
  KIRA client — generation, streaming, embeddings, speech, ranking and the budget check, each only
  where a released model can do it.

**Non-Goals**
- **Readings.** `GET`, `HEAD`, `OPTIONS` spend nothing and name no use case; a bound key's reading
  is still its own use case's, so `/v1beta/me/budget` answers a client that has not named one.
- **The exemptions of `ADR-0015`**: the unbound break-glass key and demo mode.
- **A setting to keep the old behaviour.** One rule; a switch would be two (`ADR-0045`).

## 3. User Stories
- As **IT Steuerung**, I want what a request is billed to in the request itself, so that a key
  configured in the wrong tool cannot bill the wrong use case unnoticed.
- As a **developer in two use cases**, I want my client's requests to mean the same whether I am in
  one use case or, from tomorrow, two.
- As **somebody wiring up a client**, I want one address to copy, and a request for the client I
  have, that works when pasted.

## 4. Functional Requirements
- **FR-1** — A model call (any method but `GET`, `HEAD`, `OPTIONS`) by an authenticated caller names
  its use case, with `AIRA_REQUIRE_USE_CASE` on (the default, and the only value outside `local`) —
  an API key bound to a use case included. Exempt: the unbound break-glass key, demo mode.
- **FR-2** — A key may name only its own use case (`403`, as before).
- **FR-3** — A call that names none is refused: Gemini `400 INVALID_ARGUMENT`, KIRA `403
  STANDARD_USER_PERMISSION_REQUIRED`. One message for both: *"Missing use case: every model call
  names one, with the /uc/<use-case> path prefix or the X-AIRA-Use-Case header."*, followed for a
  key by *"This key belongs to '<slug>': put /uc/<slug> in front of the path."* and for a person by
  the use cases they reach. Marked `unattributed` on its span as before.
- **FR-4** — The KIRA surface takes no use case from a person's only membership.
- **FR-5** — A reading by a bound key is attributed to its use case; with `AIRA_REQUIRE_USE_CASE`
  off, a bound key's model call is too.
- **FR-6** — The console's OpenCode configuration, the showcase's (`opencode.showcase.json`), the
  hand-editable one in `tools/opencode`, the demo traffic, `make showcase`'s printed examples and the
  load test name the use case.
- **FR-7** — *Connecting a client*: the summary, outside the fold, says that every model call names
  the use case and gives this use case's Gemini address. Inside, four steps: **where to send it**
  (an address per interface — Gemini, KIRA, and ranking where a released model ranks — the
  console-proxy note, why the prefix is there and the header instead); **who is calling** (the key's
  three forms and where keys come from, the Keycloak token, the grant delay); **which models** (name,
  KIRA id, what each serves); **a request from your client** (tabs: curl — generation, streaming,
  embeddings, speech, ranking, the budget check; Python with `google-genai`; TypeScript with the AI
  SDK; OpenCode, pointing at the file the *API keys* tab writes and saying when the use case has
  tool calling switched off, which refuses a coding assistant's first turn; a KIRA client — chat,
  embeddings, reranking). An example appears only where a released model can serve it, and every
  example is built from an address that names the use case. Each address sits on its own line with
  its copy button, which stays whole at a phone's width.

## 5. Design & Architecture
- `auth/dependencies.py`: `must_name_a_use_case` is unchanged in what it decides — a bound key was
  never exempt from it, it simply never reached it, because `require_attribution` filled in the
  key's use case first. That filling-in moved into `implied_use_case`, which supplies a key's own
  use case **only where no name is required**. `missing_use_case` words the refusal for both
  surfaces.
- `api/kira/attribution.py`: the selector, else `implied_use_case`, else the refusal — the same
  sequence as the Gemini surface, in KIRA's envelope.
- The ranking path is mounted under `require_attribution` and follows without a change of its own.
- Console: `connection-panel.ts` builds every address from `/uc/<slug>` under the console's own base
  (`gatewayBase()`), so nothing in it can name another use case or a gateway address the operator
  did not publish. `api-keys-panel.ts` writes the OpenCode configuration's `baseURL` with the prefix;
  the budget plugin derives its `/v1beta/me/budget` address from that `baseURL` and needs no change.

## 6. Data Model
None.

## 7. API / Interface Contract
- Refusals in FR-3. Every other answer is unchanged.
- **Behaviour change for running clients**: a client with a key and a base URL without
  `/uc/<slug>` (or the header) is refused from this version on. The release notes say it under
  *what you have to do*.

## 8. Security & Privacy
No new processing: the use case's slug in a URL is the use case's name, not a person's. A selector
still grants nothing.

## 9. Observability
Unchanged: a call refused for naming no use case is marked `aira.outcome=unattributed` with its
status on the span, and writes no audit row.

## 10. Testing & Acceptance Criteria
- Gateway: `test_attribution.py` (a key's model call names its use case, its reading does not, and
  with the requirement off it goes to its own), `test_apikey_binding.py`,
  `test_kira_use_case_selector.py` (a person in one use case names it too, on both surfaces; a bound
  key on both; unattributed without the requirement on both), `test_one_person_one_allowance.py`.
- Console: `connection-panel.spec.ts` — every address and every example names the use case; the
  summary; each client tab; examples only for what a released model does; every copy button copies
  its text. `api-keys-panel.spec.ts` — the OpenCode `baseURL`.
- Browser: `connection-block.spec.ts` — the addresses, and **the generation example is served as
  written** with a key issued on the page, while the same address without the prefix is refused
  with the prefix to add.
- Live suites: every model call names its use case.
- Mutations `NU1`–`NU8`.

## 11. Dependencies & Risks
- Every client in the field configured with a key and a plain base URL breaks at the upgrade until
  its configuration carries the prefix — OpenCode configurations rolled out by an installation, and
  migrated KIRA clients. The fix is one path segment; the refusal names it.

## 12. Rollout / Demo
Images only. `make showcase` writes and prints addresses with the prefix. Before upgrading, an
installation updates the base URLs of its key-based clients — or sends the header from them.
