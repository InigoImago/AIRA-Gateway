# OpenCode and AIRA

Two things live here, and they have different standing:

- **The budget plugin** (§3 below) is how a developer sees their allowance inside OpenCode — a
  section of the session sidebar and a `/budget` dialog, from `GET /v1beta/me/budget`. One file
  serves OpenCode 2 and OpenCode 1 from 1.4. The console hands out the same file beside a key it
  issues.
- **`opencode.json` is a measurement harness.** The question it was built to answer: *can a real
  coding assistant be served by the surface AIRA already has, or does it need a new one?* Choosing
  that from documentation is how a contract gets built on a guess and maintained forever — so the
  answer came from a client that actually sends requests. It could: no new surface is needed.

**OpenCode 1 and OpenCode 2 are different products.** OpenCode 1 is the npm package `opencode-ai`
and reaches Gemini through the AI SDK (`@ai-sdk/google`); OpenCode 2 is `@opencode/cli`, a rewrite
with a Gemini client and a plugin API of its own. Both are run against the gateway in
`gateway/tests/test_real_gemini_clients.py` (`make test-clients`).

## Why the Gemini surface, and why `@ai-sdk/google`

AIRA serves `/v1beta/models/{model}:generateContent`, which is Google's shape. OpenCode's providers
are AI SDK packages with an overridable `baseURL`, so `@ai-sdk/google` pointed at the gateway is the
cheapest thing that could possibly work. If it does, no new surface is needed at all. If it does
not, what breaks — and exactly where — is the evidence that decides between an OpenAI-compatible
surface, which AIRA once planned and withdrew, and an Anthropic-shaped one.

## A working configuration is generated for you

The console's API-keys panel offers **Copy OpenCode config** and **Download opencode.json** on a key
it has just issued — built at that moment because the plaintext exists for exactly
that moment, and naming only the models released to the use case whose catalog entry **declares**
`tools`. On the showcase, `make showcase` — or `make showcase-agent` alone — writes the same kind of
file, `opencode.showcase.json`, for its `coding-assistant` use case.

Use one of those if you want a working assistant. `opencode.json` here stays as the **harness**: it
is what re-runs that measurement against a changed gateway, and it is hand-editable in ways a
generated file deliberately is not.

## Running it

```bash
# 1. A use case and a key. Keys are always bounded; 30 days is the default.
#    Issue one in the console, or:
curl -sX POST http://localhost:8002/api/v1/use-cases/coding-assistant/api-keys/ \
     -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
     -d '{"label":"opencode","expires_in_days":30}'

export AIRA_API_KEY=aira_...

# 2. Point OpenCode at this config and ask it something small.
cd <a scratch project>
OPENCODE_CONFIG=/path/to/AIRA/tools/opencode/opencode.json opencode run "list the files here"
```

**First set `"model"` in that file to a model the use case may call.** Its default, `qwen2.5:3b`,
has no catalog entry in the showcase (see §1 below), so the gateway refuses it; the showcase's
`coding-assistant` may call `qwen3:0.6b`, whose entry declares `tools`.

The key is read from the environment on purpose — a credential is what the gateway masks out of the
payloads it stores, and it should not be in a file that lives in the repository either.

## What to write down

**The output rather than a summary of it**: which request shape the client sends, which auth
header, where the first failure lands and what it says, how many requests one instruction produces,
and how many tokens. The last two calibrate rate limits and budgets for a use-case shape that makes
many model calls per human instruction.

## Tool calling

Tools are carried — never executed — where the use case has `tools_enabled` on and the model's
catalog entry declares `tools`; the showcase's `coding-assistant` is such a use case.
Without the switch, `tools` is refused with a **400** naming it. A model that does not declare tool
calling is skipped by a fallback chain, and refused when it is the only candidate.

## Showing a developer what they are spending

Two different figures with two different owners:

| Question | Who knows the answer | Where it comes from |
| --- | --- | --- |
| *what did this session cost?* | **OpenCode** | `cost` in its configuration file — never an API |
| *what is left of my budget?* | **AIRA** | `GET /v1beta/me/budget` |

### 1. The session cost: prices in the configuration

Without a `cost` block OpenCode prints **`$0.00` forever**, whatever was spent. The console's
generated configuration always writes the catalog's prices and puts the currency in the model's
**name** (`qwen3:0.6b via AIRA (EUR)`), since the name is the one field it controls. Nothing is
converted — an exchange rate needs a date per booking.

The harness config here carries the same block for the one model this installation catalogues, so
a run reproduces the figure:

```json
"qwen3:0.6b": { "cost": { "input": 0.1, "output": 0.4 } }
```

`qwen2.5:3b` deliberately has none: there is no catalog entry to take it from, and a price invented
in a harness is the same wrong number as a price invented in the console.

### 2. The remaining budget: one endpoint, one `curl`

The figure a developer actually needs is the one that will **refuse** them, which is AIRA's counter:

```bash
# The same key the assistant uses. No console login, no permission beyond the key.
curl -s http://localhost:8001/v1beta/me/budget -H "x-goog-api-key: $AIRA_API_KEY" | jq -r '
  (.allowances[] | select(.limit_cost != null)
    | "\(.remaining_cost) of \(.limit_cost) left (resets \(.period_ends[0:10]))")
  // "spent \(.this_period.cost) this month"'
```

Put it in a shell prompt, a tmux `status-right`, or an alias beside the editor — it needs nothing
from OpenCode. What the answer says that a tally cannot:

- `remaining_*` is `null` where that dimension is **not capped** — not a large number, so a bar
  drawn from it cannot invent a limit nobody set;
- `period_ends` is when the counter resets, which is the question that follows a refusal;
- `enforced: false` means the limits are **figures and not stops** in this installation
  (`AIRA_ENFORCE_BUDGETS`), so a full-looking bar would be a lie;
- `today`, `this_week` and `this_period` are what the audit trail says you spent today, since Monday
  and this month, in UTC — with or without a budget.

### 3. Inside the session: the budget plugin

[`plugins/aira-budget/tui.tsx`](plugins/aira-budget/tui.tsx) adds an **AIRA budget** section to the
session sidebar, under the context gauge:

```
AIRA budget
█████████████████░░░ 85%
6 of 40 requests left
use case · per day · resets 20 Oct
Today               0.37 EUR · 35 req
This week           3.10 EUR · 88 req
This month         7.87 EUR · 310 req
```

— the limit that will refuse **first** across every budget that binds you, whose counter it is and
when it resets, then the three periods. Where the terminal is too narrow for the sidebar (120
columns and below) one line takes its place in the prompt footer, `AIRA · 85% used · 0.37 EUR
today`. **`/budget`** (or *AIRA budget* in the command palette) opens every allowance with requests
and tokens. It reads at start, at the end of every turn and every five minutes; it toasts only when
a limit is crossed — four fifths, then the limit — and once when the budget cannot be read. Nothing
goes through the model and nothing into the conversation.

**Install** — the same file for both generations:

```bash
mkdir -p .opencode/plugins/aira-budget
cp /path/to/AIRA/tools/opencode/plugins/aira-budget/tui.tsx .opencode/plugins/aira-budget/
#   …or ~/.config/opencode/plugins/aira-budget/ for every project. OpenCode 2 finds it there.

# OpenCode 1 (from 1.4) loads a TUI plugin only from tui.json, beside it in .opencode/:
echo '{ "plugin": ["./plugins/aira-budget/tui.tsx"] }' > .opencode/tui.json
```

It finds the gateway in the `aira` provider OpenCode has loaded — the configuration the console
generates names it so — and uses the key OpenCode sends to it: from the configuration, from the
environment variable the configuration names, or from OpenCode's credential store
(`opencode auth login`). `AIRA_BASE_URL` and `AIRA_API_KEY` override both. It stores nothing and
guesses no address.

**What the file obeys, each rule measured against a client:**

1. **One file in a directory of its own.** OpenCode 1 loads every `*.ts`/`*.js` directly in
   `.opencode/plugin/` or `plugins/` as a *server* plugin, and a TUI module loaded that way hangs the
   session; it does not look into sub-directories. OpenCode 2 loads a sub-directory's `tui` entry.
2. **One default export with both generations' entry points.** OpenCode 2 reads `{ id, setup }`,
   OpenCode 1 `{ id, tui }`; each ignores the other's key, and OpenCode 1 refuses a module that also
   exports `server`.
3. **Only `solid-js` and `@opentui/solid` are imported** — OpenCode resolves both to its own copies.
   Anything else would have to be installed beside the file.
4. **Loading does not wait on the network.** The first reading is started, not awaited.

**After editing the plugin**, run `uv run python tools/opencode_plugin_asset.py --write`: the
console hands out a generated copy, and `tools/tests/test_the_console_hands_out_the_plugin_it_ships.py`
fails while the two differ. Its tests are `tools/clients/opencode-budget-plugin.test.ts`, run under
Bun by `make test-clients`.

**Retired**: the OpenCode 1 server plugin that showed the figure as a toast, and the `/budget`
prompt template that asked the model to `curl` it — the second wrote its template, a tool call, a
permission dialog and the answer into the chat, when a model ran it at all.
