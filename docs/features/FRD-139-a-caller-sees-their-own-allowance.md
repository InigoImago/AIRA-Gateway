# FRD-139 — A caller sees their own allowance

> Phase: 1 · Status: **Built** · Owner: Vadim Scheibe
> Origin: the owner's agentic-coding use cases run OpenCode, which can show a budget in its status
> bar. *"Gäbe es die Möglichkeit, für solche Use Cases unsere Budgetlimits zu zeigen, so dass die
> einzelnen Benutzer wissen, was sie verwendet haben und was für Budget übrig geblieben ist?"*
> Related: [`FRD-401`](FRD-401-budget-enforcement.md) (what a budget is and how it refuses),
> [`FRD-402`](FRD-402-budget-ui.md) (the console's view of the same counters),
> [`FRD-403`](FRD-403-cost-budgets.md) (money), [`FRD-606`](FRD-606-per-person-consumption.md)
> (one person, whichever credential), [`FRD-610`](FRD-610-nothing-spends-outside-a-bucket.md) §3.3
> (enforcement can be off), [`FRD-132`](FRD-132-coding-assistant-surface.md) §11 (what a coding
> assistant shows and where it gets it)

## 1. Summary

A developer in an agentic loop spends money without watching it. The figure that matters to them is
the one that will **refuse** them, and until now the only way to read it was the console: a browser,
a login, and a permission an API key does not have.

`GET /v1beta/me/budget` answers from the credential that asked: what the limits are, what has been
used, what is left, when the period rolls over, and whether limits are enforced at all. No use case
in the path — an agent was configured with a URL and a key and does not know a slug — and no
permission beyond being authenticated, because it is the caller's own figure.

**Only the endpoint.** How it is displayed is deliberately not decided here: the same answer serves
a shell prompt, a tmux status line, an OpenCode plugin or an MCP tool, and §5 records what each of
those costs. Picking one would have put an untestable integration in this repository.

## 2. Goals & Non-Goals

**Goals**
- One request answers *what am I allowed, what have I used, what is left, when does it reset*.
- Reachable with the credential the agent already holds; no console role, no second system.
- Honest about the three ways the answer can be nothing: no budget, a dimension that is not
  capped, and a limit that exists but is not enforced.
- The caller's own figures only — never a colleague's, never the use case's sum.

**Non-Goals**
- **A display.** No plugin, no status-line daemon, no MCP server here (§5).
- **Rate limits.** `FRD-405`'s buckets answer a different question (*how fast*, not *how much*) and
  are the obvious next thing to expose, deliberately left out rather than bundled.
- **A second number nobody enforces.** The endpoint reports counters and the audit trail, each
  labelled; it does not compute a third figure out of them.

## 3. Functional Requirements

- **FR-1 — The credential decides.** The use case comes from the request's attribution, the person
  from `scopes.person`: an API key's subject is its owner's username and a token's is a directory
  id, so a key and a login land on **one** allowance (`FRD-606`).
- **FR-2 — `allowances`**: one entry per budget that binds this caller — their use case's shared pot
  and, where the use case gives each member one, **their own** counter. A colleague's counter is
  never among them. Each entry carries `scope`, `period`, `period_ends`, `enabled`, `limit_cost`,
  `limit_tokens`, `limit_requests`, the matching `used_*` and `remaining_*`, and
  `unpriced_requests`.
- **FR-3 — Not capped is `None`, never a number.** A budget that limits requests and not tokens
  answers `limit_tokens: null` and `remaining_tokens: null`. A large number would be rendered as a
  full bar — a limit nobody set.
- **FR-4 — A negative remainder is reported.** Enforcement refuses *at* the limit, so going past it
  takes a settle correcting a reservation upward. "Spent to the last cent" and "spent more than
  allowed" are different facts; a display may clamp, the figure does not.
- **FR-5 — `period_ends` is the first moment of the next period**, in UTC — the moment the counter
  resets, which is the second question a refused caller has. Computed beside `period_key`
  (`budgets/keys.py`), so the end and the beginning of a month cannot drift apart.
- **FR-6 — `this_period`**: what this caller spent in the current calendar month, from the **audit
  trail**, whether or not a budget exists. A use case nobody capped still answers *what have I
  used* (owner's choice, 2026-10-11) — with no limit beside it, because `limit: null` and
  "limit not reached" are different answers.
- **FR-7 — Two sources, labelled apart.** `allowances` are the counters enforcement reads (reset per
  period, the number that refuses); `this_period` is the record. They can differ — a released
  reservation, a row retained past a counter reset — and collapsing them into one figure would make
  the difference impossible to see.
- **FR-8 — `enforced`** says whether `AIRA_ENFORCE_BUDGETS` is on. With it off a limit is a figure
  and not a stop, and a client drawing a bar that implies otherwise is wrong in the direction that
  matters (`FRD-610` §3.3).
- **FR-9 — Answered, not refused.** A credential bound to no use case gets an empty `allowances`
  and a shaped `this_period`: a status line polling this must not have to tell a configuration
  mistake from an outage.
- **FR-10 — The caller's own, on the figure side too.** `this_period` is narrowed to the person
  asking, so this endpoint cannot route around a use case that withholds other people's figures
  (`FRD-622` FR-1c).

## 4. Design

`api/usage.py` holds both sides of one question: `/v1beta/usage/{use_case}` for the console
(`FRD-402`) and `/v1beta/me/budget` for the caller. The arithmetic of *what is left* lives once, in
`BudgetService.usage`, which both read — the console gained the limits in the same change rather
than joining them from Management, which is what it did before.

```
GET /v1beta/me/budget
   ├── attribution → use case          (the credential's, never a parameter)
   ├── scopes.person → whose counter   (one human, whichever credential)
   ├── BudgetService.usage(use_case, subject=person)   → allowances (counters + limits)
   └── ReportingService.report(scope=(use_case,), person=person) → this_period (the record)
```

## 5. How it gets in front of a developer

Three ways, none of which needs a change here, recorded with what each costs:

1. **The terminal.** `curl` with the key the agent already uses, in a shell prompt, a tmux status
   line or an alias. Costs nothing, lives next to the assistant rather than inside it. The recipe is
   in [`tools/opencode/README.md`](../../tools/opencode/README.md).
2. **An OpenCode plugin.** OpenCode has a plugin mechanism and this exact pattern already exists in
   the wild (`opencode-opper-budget` shows a personal budget in the status bar). It is the only way
   to get the figure **into** the assistant's own chrome. Not written here: it could not be run
   against a real OpenCode in this repository, and an untested integration is worse than a
   documented `curl`.
3. **An MCP tool.** OpenCode speaks MCP (`mcp` block in `opencode.json`, local or remote), so the
   agent itself could be asked *"how much budget is left"*. It is the plugin-free way to have it in
   the session, and it is a second surface with its own authentication — an ADR, not a patch.

**Deliberately not a fourth way:** prepending the figure to the model's answer. `FRD-309`'s notice
mechanism could do it today, and it would put budget text into the agent's context on every
request, where the model reads it and can act on it.

**What OpenCode's own numbers are worth here:** `opencode stats` tallies its *own* sessions, and for
a custom OpenAI-compatible provider its cost display does not work at all (two open issues at the
time of writing). `FRD-132` §11 measured the token figures and found them exact; the *limits* it
shows come from its configuration file, not from any API. So the only authoritative figure is this
one — the counter that refuses.

## 6. Open

- **Rate limits are not here** (`FRD-405`). *"Am I about to be throttled"* is a fair question and a
  different mechanism; bundling it would have hidden the decision.
- **No installation budget for a caller with no use case.** `FRD-610`'s residual bucket is not
  reported: it is not *theirs*, and a figure a caller cannot influence reads like one they can.
- **Nothing pushes.** A client polls. A header on every answer was considered and left out: it is
  the request path, and with enforcement off nothing reads the counters at all.

## 7. Testing & Acceptance Criteria

`gateway/tests/test_a_caller_sees_their_own_allowance.py` — a key is told its limit and what is
left; an uncapped dimension is `null` rather than a number; a use case with no budget still says
what was used; a per-member budget answers with the caller's own counter and a colleague's asking
gets theirs; a key and a login land on the same allowance; the answer says when a limit is not
enforced; a day budget's period ends at the next UTC midnight; it needs a credential and no
permission beyond it; a credential bound to no use case is answered rather than refused; the
figures beside the allowance are the caller's own and not the use case's.

And the two that go through the **counters** rather than the audit trail, because the cases above
would all pass with the two wired to different keys: booking through `guard` then `record` moves
`remaining_*` and leaves a colleague's counter untouched, and a settle past the limit is reported as
a negative remainder.

`gateway/tests/test_budget_service.py` asserts the usage row **whole**, field for field — which is
how the limits arrived in it: that test failed, which is the notification a contract is supposed to
send.

**Mutations** `AL1`–`AL6`: the caller's own counter rather than an unbound row, an uncapped
dimension as zero, a clamped negative remainder, a period ending at 23:59, enforcement reported as
always on, and the figures taken for the use case instead of the person.

**Acceptance**

- *Given* a use case with a 50 € monthly pot and 3.40 € booked, *when* the key calls
  `/v1beta/me/budget`, *then* it reads `limit_cost: "50.00"`, `used_cost: "3.40"`,
  `remaining_cost: "46.60"` and `period_ends` on the first of next month.
- *Given* `AIRA_ENFORCE_BUDGETS=false`, *then* the same answer carries `enforced: false` and the
  limit unchanged.
- *Given* a use case with no budget at all, *then* `allowances` is empty and `this_period` carries
  the caller's own spend.
