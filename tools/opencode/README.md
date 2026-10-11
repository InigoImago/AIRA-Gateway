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

That last row is why there is no one-liner that paints a budget into its chrome: it was assumed to
exist, and the assumption is what made this section wrong before.

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

### 3. Inside the session: the `/budget` command

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

**What has not been demonstrated:** an agent actually running it. This demo stack serves
`qwen3:0.6b`, and that model never calls the shell tool — OpenCode's `part` table holds only `text`
rows for the attempt — while its first answer *invented* a budget that contradicted the stack. Hence
the template's standing order to reply `I could not read the budget.` rather than guess. A coding
model able to use tools is the premise of the use case; this one is not it, and §1 and §2 above are
what was actually measured here.

A **plugin** would be the only route into OpenCode's own chrome (`opencode-opper-budget` does this
for Opper.ai) and an **MCP tool** would be a second authenticated surface — `FRD-139` §5 weighs
both, and neither is in this repository: an integration that cannot be exercised here is worse than
a documented `curl`.
