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

**The first answer to this was wrong, and the owner said so:** *it is not practical to run a
command to find out how much budget is left — and it is worse for everybody else, who will either
not bother reading the documentation or never learn such an action exists.* A figure that arrives
only when asked for arrives for the one developer who already knew. `ADR-0033` records the decision
that followed: the display is **shipped**, as a plugin that runs inside the client.

### 5.1 What the installed client can actually do

**Checked against the installed binary** (`opencode 1.18.15`), not against the project's
documentation or its published types — every line below contradicts one of them, and each
contradiction cost a broken client first:

| | |
| --- | --- |
| a status line running a shell command | **no** — an open pull request, not a feature |
| `.opencode/plugin/*.js`, auto-loaded | yes — and **every file** in the directory is loaded as a plugin |
| a plugin's exports | **every export is called as a plugin factory.** `export const id = "…"`, which `PluginModule.id` declares as valid, makes the loader refuse the file: *"Plugin export is not a function"* |
| `tui` vs `server` plugins | a file exporting `tui` is picked up by the **server** loader anyway, called with a plugin's arguments, and the session then hangs. `TuiPluginModule` exists in the types and not in this path |
| the way into the interface | `client.tui.showToast` — the server API the TUI itself serves (`POST /tui/show-toast`) |
| `permission.bash` | glob patterns, and **the last matching rule wins** |

The origin note at the top of this document says "status bar" because that is what the request
assumed. It is wrong for this version, and finding that out is what produced the route that works.

### 5.2 What ships

1. **The plugin** — [`tools/opencode/plugin/aira-budget.js`](../../tools/opencode/plugin/aira-budget.js),
   one file, dropped into `.opencode/plugin/`. It reads this endpoint when it loads, when a session
   starts and after every finished turn, and shows one line as a toast. **Nobody has to know it is
   there**, which is the requirement. Four properties decide whether it is bearable rather than
   merely present:

   - the figure shown is the dimension that will **refuse first** across every budget that binds —
     not the first one in the list, and not an average;
   - `announce` keeps it from speaking every turn: the first reading, every change of severity, and
     otherwise only a tenth of the limit at a time. A counter that went *down* is not news;
   - a new session is shown the figure **unconditionally**, because that is the moment somebody
     starts spending — and because a toast sent while the plugin was loading can be accepted by the
     server and drawn by nobody;
   - a setup it cannot read is said **once**, naming the two places to fix. Silence would look
     exactly like a use case with no budget.

   It carries no credential: the key comes from the `opencode.json` the console generates, or from
   `AIRA_API_KEY`. It guesses no address either — an installed file that falls back to a loopback
   host reports a connection error about a host the reader never named.

2. **The console hands it out.** The API-keys panel offers *Download budget plugin* beside the
   configuration it already generates, because collecting a key is the one moment a developer is
   certainly looking. The copy in the bundle is generated from the file above
   (`tools/opencode_plugin_asset.py`) and a test fails when they disagree — the copy nobody
   regenerates is the one somebody installs, and a plugin a version behind does not announce
   itself, it just shows a figure.

3. **The `/budget` command** — [`tools/opencode/command/budget.md`](../../tools/opencode/command/budget.md),
   a prompt template OpenCode expands (`opencode run --command budget`, or `/budget` in the TUI).
   Kept for anybody who wants to *ask*, and demoted from the main route for a measured reason: it
   depends on the model calling a shell tool. Against this gateway, `qwen3:0.6b` never attempted it
   and `qwen3:4b` declared ten tools and called none — twice, taking 90 seconds each time — while
   the smaller model's first answer **invented** a budget that contradicted the stack, in the
   template's own bullet shape. The template now orders `I could not read the budget.` instead of a
   guess. A transparency feature whose reliability depends on a model's mood is not one; the plugin
   does not involve the model at all.

4. **The `curl` recipe** — in [`tools/opencode/README.md`](../../tools/opencode/README.md), for a
   shell prompt, a tmux status line, or any client that is not OpenCode.

**Deliberately not a way at all:** prepending the figure to the model's answer. `FRD-309`'s notice
mechanism could do it today, and it would put budget text into the agent's context on every
request, where the model reads it and can act on it.

### 5.3 Measured, live

With the plugin installed, a real key, and `qwen3:4b` answering through the gateway:

- the gateway recorded `GET /v1beta/me/budget → 200`, and OpenCode's toast endpoint accepted the
  line with `{"data": true}`;
- the line it computed from the live answer was
  **`AIRA: 489 of 500 requests left · use case, per day · resets 12 Oct`**;
- replayed from a captured pty, the interface drew it:

  ```
  ┃  hi           ┃                                                          ┃
  ┃               ┃  AIRA budget                                             ┃
                  ┃                                                          ┃
     ▣  Build · qw┃  AIRA: 489 of 500 requests left · use case, per day ·    ┃
  ```

- and beside it, `Build · qwen3:4b via AIRA (EUR)` — the session cost half of `FRD-132` §11.7, in
  the same screenshot.

**What OpenCode's own numbers are worth here:** `opencode stats` tallies its *own* sessions and
rounds to two decimals, so a real figure of `0.00021` prints as `$0.00`. `FRD-132` §11 measured the
token figures and found them exact. The *limits* it shows come from its configuration file, so the
only authoritative remaining figure is this endpoint's — the counter that refuses.

## 6. Open

- **Rate limits are not here** (`FRD-405`). *"Am I about to be throttled"* is a fair question and a
  different mechanism; bundling it would have hidden the decision.
- **No installation budget for a caller with no use case.** `FRD-610`'s residual bucket is not
  reported: it is not *theirs*, and a figure a caller cannot influence reads like one they can.
- **Nothing pushes.** A client polls — the plugin polls on its behalf, at session start and after
  each turn. A header on every answer was considered and left out: it is the request path, and with
  enforcement off nothing reads the counters at all.
- **One client is served.** The plugin is OpenCode's because OpenCode is the client that was
  measured (`ADR-0033`). A KIRA client, a plain `curl` user or another assistant still has only the
  endpoint, and the next client gets its own decision rather than a framework.
- **The guards are structural.** Nothing here can notice an OpenCode release changing its plugin
  loader or its toast endpoint; the tests assert what *this* repository ships. All three loader
  rules in §5.1 contradict the published types, so the far end moving is a question of when.

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

**Mutations** `AL1`–`AL8`: the caller's own counter rather than an unbound row, an uncapped
dimension as zero, a clamped negative remainder, a period ending at 23:59, enforcement reported as
always on, the figures taken for the use case instead of the person, the use case resolved by hand
instead of by the surface's own dependency, and the amounts' currency hard-coded to dollars.

### 7.1 The plugin and the command

The plugin is JavaScript running in a third-party client, so neither `pytest` nor `ng test` reaches
it. Its properties are asserted by `node --test` in `tools/opencode/plugin/*.test.mjs` —
**43 of them** — and bridged into the suite that gates this repository by
`tools/tests/test_the_opencode_plugin_holds_its_properties.py`, which also fails when the shipped
directory grows a second file or the file grows a second export (§5.1). `node` is required rather
than skipped: a skipped guard reports success.

The arithmetic half asks what the line may say: an uncapped dimension is not a dimension, a limit
of zero is not one either, a disabled budget is not consulted, the tightest dimension wins across
budgets and units, the unit is the installation's and never a `$`, an unlabelled amount is better
than a wrong label, `period_ends` is formatted in UTC whatever the reader's clock says, spending
past the limit is reported as past the limit, and a limit that stops nothing says so.

The wiring half asks what reaches the screen: the figure appears before anything has been spent;
the key comes from the configuration OpenCode already loaded, or from the environment, which wins;
no key and no address are **different** complaints, each naming where to fix it; a refusal is
reported with the gateway's own message, once, and again if it breaks after working; an unchanged
figure does not interrupt; crossing into the last fifth does, and for longer; a reached limit is an
error; a new session is shown the figure whatever it says; an unknown event is not a crash; and
**loading the plugin cannot keep OpenCode from starting** — the one that drew nothing at all.

**Mutations** `OB1`–`OB10`, each a way the display could be reassuring, unbearable or absent:
the first reading suppressed, an uncapped limit counted, the first dimension shown instead of the
tightest, the warning moved to the refusal, the enforcement label dropped, a complaint repeated
every turn, the request's timeout removed, a second export added, the first reading awaited, and a
new session shown nothing because the figure had not changed.

`tools/tests/test_the_opencode_command_asks_for_a_real_endpoint.py` (`CMD1`, `CMD2`) keeps the
command file from naming a route this gateway does not serve, and from losing the order to refuse
rather than invent. `tools/tests/test_the_console_hands_out_the_plugin_it_ships.py` keeps the
console's generated copy identical to the file it is generated from.

**Acceptance**

- *Given* a use case with a 50 € monthly pot and 3.40 € booked, *when* the key calls
  `/v1beta/me/budget`, *then* it reads `limit_cost: "50.00"`, `used_cost: "3.40"`,
  `remaining_cost: "46.60"` and `period_ends` on the first of next month.
- *Given* `AIRA_ENFORCE_BUDGETS=false`, *then* the same answer carries `enforced: false` and the
  limit unchanged.
- *Given* a use case with no budget at all, *then* `allowances` is empty and `this_period` carries
  the caller's own spend.
- *Given* the plugin installed and the console's configuration, *when* a session starts, *then* the
  interface shows one line naming what is left, whose counter it is and when it resets — measured
  in §5.3.
- *Given* no key and no `AIRA_BASE_URL`, *then* the plugin says which of the two is missing and
  where to put it, once, and nothing else.
