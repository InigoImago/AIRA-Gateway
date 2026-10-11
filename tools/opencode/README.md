# Pointing OpenCode at AIRA (`FRD-132` stage A)

This is a **measurement harness**, not a supported integration. The question it exists to answer:
*can a real coding assistant be served by the surface AIRA already has, or does it need a new one?*
Choosing that from documentation is how a contract gets built on a guess and maintained forever —
so the answer comes from a client that actually sends requests.

## Why the Gemini surface, and why `@ai-sdk/google`

AIRA serves `/v1beta/models/{model}:generateContent`, which is Google's shape. OpenCode's providers
are AI SDK packages with an overridable `baseURL`, so `@ai-sdk/google` pointed at the gateway is the
cheapest thing that could possibly work. If it does, no new surface is needed at all (`FRD-132` B1).
If it does not, what breaks — and exactly where — is the evidence that decides between an
OpenAI-compatible surface (B2, reviving the withdrawn `FRD-106`) and an Anthropic-shaped one (B3).

## The console generates this file now (2026-08-08)

Since `FRD-132` §10, the API-keys panel offers **Copy OpenCode config** and **Download
opencode.json** on a key it has just issued — built at that moment because the plaintext exists for
exactly that moment, and naming only models whose catalog entry **declares** `tools`.

Use that if you want a working assistant. This directory stays as the **harness**: it is what
re-runs the `FRD-132` measurement against a changed gateway, and it is hand-editable in ways a
generated file deliberately is not.

## Running it

```bash
# 1. A use case and a key. Keys are always bounded (ADR-0015); 30 days is the default.
#    Issue one in the console, or:
curl -sX POST http://localhost:8002/api/v1/use-cases/coding-assistant/api-keys/ \
     -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
     -d '{"label":"opencode","expires_in_days":30}'

export AIRA_API_KEY=aira_...

# 2. Point OpenCode at this config and ask it something small.
cd <a scratch project>
OPENCODE_CONFIG=/path/to/AIRA/tools/opencode/opencode.json opencode run "list the files here"
```

The key is read from the environment on purpose — a credential in a file that lives in the
repository is the thing `FRD-406` masks out of stored payloads, and it should not be here either.

## What to write down

Per `FRD-132` §3, and **the output rather than a summary of it**: which request shape the client
sends, which auth header, where the first failure lands and what it says, how many requests one
instruction produces, and how many tokens. The last two calibrate `FRD-405` limits and `FRD-400`
budgets for a use-case shape that makes many model calls per human instruction.

## What is expected to fail, today

Tool calling. `tools` is refused with **400** at the surface (`FRD-131` builds it, per use case and
off by default). Reaching that refusal is a successful stage-A run: it means the path, the auth and
the streaming all worked, and the one missing capability is the one already written up.

## Showing a developer what they are spending (`FRD-139`, `FRD-132` §11.7)

Two different figures with two different owners, and both were broken in their own way:

| Question | Who knows the answer | Where it comes from |
| --- | --- | --- |
| *what did this session cost?* | **OpenCode** | `cost` in its configuration file — never an API |
| *what is left of my budget?* | **AIRA** | `GET /v1beta/me/budget` |

### What this version of OpenCode actually has

Checked against the installed binary (`opencode 1.18.15`) rather than the project's documentation,
because the two disagree:

| | |
| --- | --- |
| `plugin: [...]` | yes — a module that hooks the session |
| `command/*.md` | yes — prompt templates, run as `opencode run --command <name>` or `/<name>` |
| `mcp: {...}` | yes — local or remote tool servers |
| `permission.bash` | yes — glob patterns, and **the last matching rule wins** |
| a status line running a shell command | **no** — an open pull request, not a feature |
| what a plugin may export | **one function, and nothing else** — every export is called as a plugin factory, and a string `id` makes the loader refuse the file |
| `tui` plugins from that directory | **no** — the server loader takes the file anyway and the session hangs; the way in is `client.tui.showToast` |

The status-line row is why there is no one-liner that paints a budget into the chrome: it was
assumed to exist, and the assumption is what made this section wrong before. The two plugin rows
are why §3's file looks the way it does — both were found by watching the client refuse to work.

### 1. The session cost: prices in the configuration

Without a `cost` block OpenCode prints **`$0.00` forever**, whatever was spent. The console's
generated configuration now always writes the catalog's prices and puts the currency in the model's
**name** (`qwen3:0.6b via AIRA (EUR)`), since the name is the one field it controls. Nothing is
converted — an exchange rate needs a date per booking (`FRD-403`).

The harness config here carries the same block for the one model this installation catalogues, so
a run reproduces the figure:

```json
"qwen3:0.6b": { "cost": { "input": 0.1, "output": 0.4 } }
```

`qwen2.5:3b` deliberately has none: there is no catalog entry to take it from, and a price invented
in a harness is the same wrong number as a price invented in the console.

Measured, same instruction twice, read out of OpenCode's own store
(`~/.local/share/opencode/opencode.db`): `cost: 0` with no block against `cost: 0.0002102` with it,
against `0.0002` in AIRA's audit row. `opencode stats` still shows `$0.00` — it rounds to two
decimals, so a fifth of a thousandth of a euro has nowhere to go.

### 2. The remaining budget: one endpoint, one `curl`

The figure a developer actually needs is the one that will **refuse** them, which is AIRA's counter:

```bash
# The same key the assistant uses. No console login, no permission beyond the key.
curl -s http://localhost:8001/v1beta/me/budget -H "x-goog-api-key: $AIRA_API_KEY" | jq -r '
  (.allowances[] | select(.limit_cost != null)
    | "\(.remaining_cost) of \(.limit_cost) left (resets \(.period_ends[0:10]))")
  // "spent \(.this_period.cost) this month"'
```

Against the live stack that prints `5.00 of 5.00 left (resets 2026-11-01)`. Put it in a shell
prompt, a tmux `status-right`, or an alias beside the editor. It lives *next to* OpenCode, which is
why it needs nothing from OpenCode.

Three things the answer says that a tally cannot:

- `remaining_*` is `null` where that dimension is **not capped** — not a large number, so a bar
  drawn from it cannot invent a limit nobody set;
- `period_ends` is when the counter resets, which is the question that follows a refusal;
- `enforced: false` means the limits are **figures and not stops** in this installation
  (`AIRA_ENFORCE_BUDGETS`), so a full-looking bar would be a lie.

### 3. Inside the session, unasked: the budget plugin

[`plugin/aira-budget.js`](plugin/aira-budget.js) is **the route this feature is delivered by**
(`ADR-0033`). Everything above still has to be *asked for*, and a figure nobody asks for is a
figure nobody sees:

```bash
mkdir -p .opencode/plugin
cp /path/to/AIRA/tools/opencode/plugin/aira-budget.js .opencode/plugin/
#   …or ~/.config/opencode/plugin/ for every project. The AIRA console offers the same file
#   beside the key it issues: "Download budget plugin".
```

That is the whole installation. It reads the key from the `opencode.json` OpenCode has already
loaded — so the console's generated configuration needs nothing added — or from `AIRA_API_KEY` and
`AIRA_BASE_URL` when you prefer the environment. It carries no credential and guesses no address.

What it does, and what it deliberately does not:

| | |
| --- | --- |
| when it speaks | at load, when a session starts, and after a turn that moved the counters |
| when it stays quiet | an unchanged figure, a counter that went *down*, and anything less than a tenth of the limit since the last notice |
| what it shows | the dimension that will **refuse first** across every budget that binds you, whose counter it is, and when it resets |
| how loudly | `info`, `warning` from four fifths spent, `error` at the limit — with the warning on screen for twice as long |
| when it complains | no key, no address, or a gateway that refuses: said **once**, naming the place to fix |

Measured against a live stack, which is also what it looks like:

```
┃  hi           ┃                                                          ┃
┃               ┃  AIRA budget                                             ┃
                ┃                                                          ┃
   ▣  Build · qw┃  AIRA: 489 of 500 requests left · use case, per day ·    ┃
```

**Three rules the file obeys, each one having broken the client first** — they are in its header
too, because the next person to tidy it will not have measured them:

1. **One file in the plugin directory.** OpenCode loads *every* file there as a plugin, so a helper
   module beside it is handed to the plugin API and `opencode run` dies with `UnknownError`.
2. **One export, and it is a function.** The loader calls every export as a plugin factory. An
   `export const id = "…"` — valid according to the published types — makes it refuse the file with
   *"Plugin export is not a function"*, and the display then silently never appears.
3. **Loading may not block.** Awaiting the first reading waits on the gateway *and* on the TUI's
   own server, which is not serving yet; the interface drew nothing at all until it was killed.

### 4. Inside the session, on request: the `/budget` command

[`command/budget.md`](command/budget.md) is a **prompt template**, not code. Copy it next to a
project (`.opencode/command/budget.md`) or into `~/.config/opencode/command/`, then:

```bash
opencode run --command budget      # or type /budget in the TUI
```

The agent runs the `curl` above with its own shell tool and reports one line. Two things it needs:

```json
"permission": { "bash": { "*": "ask", "curl -s *": "allow" } }
```

(the last matching rule wins, so the specific `allow` must come after the general `ask`), and
`AIRA_API_KEY` in the environment the agent inherits. The key is never written into a file in this
repository — that is the one way a shipped recipe leaks a credential into version control.

**Why this is not the main route.** It depends on the model calling a shell tool, and that was
measured rather than assumed: `qwen3:0.6b` never attempted it, and `qwen3:4b` — a model this
catalogue records as one that *does* emit tool calls — declared ten tools and called none, twice,
taking 90 seconds each time. The smaller model's first answer *invented* a budget that contradicted
the live stack, in the template's own bullet shape, which is why the template now orders
`I could not read the budget.` instead of a guess.

A transparency feature whose reliability depends on a model's mood is not one. The plugin in §3
does not involve the model at all; this command is for somebody who wants to ask.
