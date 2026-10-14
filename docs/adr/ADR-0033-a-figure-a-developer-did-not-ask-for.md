# ADR-0033 — The budget reaches a developer through code we ship into their client

- **Status:** Accepted
- **Date:** 2026-10-11
- **Deciders:** Vadim Scheibe
- **Note (2026-10-11):** the follow-up below about the console is done — both budget screens say
  when `AIRA_ENFORCE_BUDGETS` is off (`FRD-610` §3.3).

## Context

`FRD-139` built `GET /v1beta/me/budget`: one request, answered from the credential that asked,
saying what the limits are and what is left. The display was deliberately left out — a `curl`
recipe and, later, a `/budget` prompt template for OpenCode.

The owner read that and rejected it, in the words that decide this ADR: *it is not practical to run
a command to find out how much budget is left — and it is worse for everybody else, who will either
not bother reading the documentation or never learn such an action exists.*

That is a correct objection, and it is not about convenience. **A control nobody meets is not a
control.** The figure's whole purpose is to arrive *before* the refusal; a figure that arrives only
when asked for arrives for the one developer who already knew. The same argument the project makes
about a permission nobody can reach (`FRD-206`) and a badge on an inactive control
(`LESSONS.md` §1) applies to a number nobody sees.

An endpoint cannot fix this by itself. Nothing in the request path can put a figure in front of
somebody who has not asked — not without putting it into the model's context, which is refused
outright (`FRD-139` §5: `FRD-309`'s notice mechanism could do it, and the model would then read and
act on budget text on every request).

So the question is where the display lives.

## Options considered

- **A — leave it at the endpoint, document the `curl`.** No new artefact, nothing to maintain, and
  the figure reaches exactly those who read `tools/opencode/README.md`. This is what was built, and
  it is what the owner rejected.
- **B — a prompt template (`/budget`).** Shipped, tiny, no code in anybody else's process. But it
  is still *asked for*, and it depends on the model being willing to call a shell tool: measured
  against `qwen3:0.6b` and `qwen3:4b` through this gateway, both declared ten tools and called
  none, one of them inventing a budget that contradicted the stack. A transparency feature whose
  reliability depends on a model's mood is not one.
- **C — the gateway announces it in the answer.** Either a header on every response or
  `FRD-309`-style text. The header reaches no display in any client we know of; the text goes into
  the agent's context. Refused for the reason above.
- **D — a plugin we write, shipped into the developer's client.** OpenCode loads JavaScript from
  `.opencode/plugin/`; a plugin can read the budget and push it into the interface. The figure then
  appears without anybody knowing it exists, which is the requirement. The cost is real: we
  maintain code that runs inside somebody else's process, against an API whose stability is theirs
  to decide, and the console has to hand the file out.
- **E — an MCP tool.** The agent could be asked; still asked, plus a second authenticated surface.
  Rejected for B's reason and its own.

## Decision

**D, with B kept.** The plugin (`tools/opencode/plugin/aira-budget.js`) is the route the feature is
delivered by: it reads `GET /v1beta/me/budget` when it loads, when a session starts, and after
every finished turn, and shows one line as a toast in OpenCode's own chrome. The console offers it
for download beside the `opencode.json` it already generates, because the moment somebody collects
their API key is the one moment they are certain to be looking. The `/budget` command stays for
anybody who wants to ask, and the `curl` recipe stays for everything that is not OpenCode.

Three rules, each one bought with a broken client, hold the artefact together and are enforced by
`tools/tests/test_the_opencode_plugin_holds_its_properties.py`:

1. **One file.** OpenCode loads *every* file in the plugin directory as a plugin, so a helper
   module beside the plugin is handed to the plugin API and takes the client down.
2. **One export, and it is a function.** The loader calls every export as a plugin factory. An
   `export const id = "…"` — which the published types declare as valid — makes it refuse the file,
   and the plugin then silently does nothing.
3. **Loading may not block.** Awaiting the first reading blocks OpenCode's bootstrap on both the
   gateway and the TUI's own server, and the interface draws nothing at all.

**What the plugin may not become.** It displays and it reads the caller's own figure. It does not
authenticate on anybody's behalf, does not carry a credential (the key comes from the configuration
OpenCode already loaded, or from `AIRA_API_KEY`), does not modify a request, and holds no state. If
it ever needs to do more than read and show, that is a different decision.

## Consequences

- **Positive:** the figure arrives unasked, including the warning before the limit — measured
  live: `AIRA: 489 of 500 requests left · use case, per day · resets 12 Oct`, drawn in the TUI. The
  display is independent of the model, which the `/budget` route is not. Nothing goes into the
  agent's context.
- **Negative:** we now maintain JavaScript for a third-party client. Its plugin API is
  undocumented in the places that matter (all three rules above contradict the published types), so
  an OpenCode release can break the display, and the guard tests are structural — they cannot
  notice a change at the far end. The console carries a generated copy of the file, which is a
  second copy kept honest by a test rather than by construction.
- **Negative:** one client is served and others are not. KIRA clients, plain `curl` users and other
  assistants still have only the endpoint. That is deliberate: the plugin exists because a *measured*
  client was in front of us, and the next one gets its own decision rather than a framework.
- **Follow-ups:** rate limits are not in the figure (`FRD-405`, `FRD-139` §6); the console still
  says nothing when `AIRA_ENFORCE_BUDGETS` is off (`FRD-610` §3.3), although the plugin now does.
