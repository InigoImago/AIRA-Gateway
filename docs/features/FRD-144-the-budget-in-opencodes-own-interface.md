# FRD-144 — The budget in OpenCode's own interface

> Phase: 8 (KIRA parity) · Status: **Built** · Owner: Vadim Scheibe
> Origin: the owner, after rolling out OpenCode 2: *"Ich will vernünftig funktionierenden Plugin
> haben"* — the budget in OpenCode's right-hand sidebar, on a command what is left, and small
> statistics for today, this week and this month; the `/budget` command, run in OpenCode 2, showed
> the template in the chat, then a tool call, then a permission dialog, then the answer as chat text.
> And: use the work to check the gateway against a current coding assistant for real.
> Related: `FRD-139` (the endpoint, and the plugin this replaces), `FRD-143` (the Gemini clients,
> whose OpenCode check this corrects), `ADR-0033` (why a figure nobody asked for).

## 1. Summary

The budget a developer can spend appears where they already look while they work: a section of
OpenCode's session sidebar, under the context gauge, with the limit that will refuse first as a bar
and a sentence and what they spent today, this week and this month. `/budget` opens every allowance
in a dialog. Nothing involves the model and nothing is written into the conversation.

One file does this for **both** OpenCode generations, which are two products: OpenCode 1
(`opencode-ai`) and OpenCode 2 (`@opencode/cli`), a rewrite with a new plugin API and a Gemini client
of its own. Both run against the gateway in the test suite, headless and in their terminal
interface.

## 2. Goals & Non-Goals

**Goals**
- The figure without asking, in the sidebar; the detail on request, in a dialog; a warning only at
  four fifths and at the limit.
- Today, this week and this month from the gateway, because only the gateway has them.
- One file for OpenCode 2 and OpenCode 1 from 1.4, installed by copying it.
- The key OpenCode already sends to the gateway, wherever OpenCode keeps it; no copy.
- OpenCode 1 and 2, both run against the gateway in CI.

**Non-Goals**
- OpenCode 1 before 1.4, which has no sidebar slot for a plugin. The toast-only server plugin that
  served it is retired with the prompt template (§5).
- Showing a colleague's spend, or the use case's total: the figures are the caller's own (`FRD-139`).
- Budgets per week. The week is a window of the record, not a period a budget counts.

## 3. What was found

- **"v2" is OpenCode 2, a separate npm package.** `opencode-ai` stops at 1.19.3; OpenCode 2 is
  `@opencode/cli` 2.0.32, which OpenCode 1 offers as an upgrade. `FRD-143`'s check of "the current
  release" ran 1.19.3 — an OpenCode 1. Measured now: OpenCode 2 streams `streamGenerateContent?alt=sse`
  and declares its twelve tools in `parametersJsonSchema`, whether its configuration names its own
  Gemini package or the AI SDK's (it maps one onto the other); the gateway serves both since
  `FRD-143`.
- **OpenCode 2 runs no OpenCode 1 plugin.** Its documentation says so, and that is why only the
  prompt template was left for the owner to try — the route `FRD-139` had already measured as one a
  model may ignore or answer from imagination.
- **Both generations offer the sidebar to a plugin, through different APIs.** OpenCode 1 from 1.4
  loads a TUI plugin listed in `tui.json` (`{ id, tui }`, slots `sidebar_content` and
  `session_prompt_right`); OpenCode 2 discovers a plugin directory under `.opencode/plugins/` and
  loads its `tui` entry (`{ id, setup }`, slots `sidebar.content` and `prompt.footer.status`).
  Each loader ignores the other's key, so one default export carries both.
- **A plugin cannot read a key from OpenCode 1's credential store directly, but OpenCode resolves it:**
  `api.state.provider[].key`. OpenCode 2 exposes stored credentials through its own client
  (`credential.list`, what `opencode auth export` uses), and the environment variables a provider
  names through its configuration.
- **OpenCode 2 ends a turn with `session.execution.succeeded`** (or `failed`, `interrupted`), not
  OpenCode 1's `session.idle`; a sidebar listening for the latter stayed one turn behind.
- The mock filled every tool argument with the prompt as a string; OpenCode 2 validates the
  arguments of its `edit` tool and refused `replaceAll: "…"`. The mock now gives each argument its
  declared type.

## 4. Functional Requirements

- **FR-1 — the record by day and week.** `GET /v1beta/me/budget` answers `today` and `this_week`
  (Monday to Monday) beside `this_period` (the month), each `{from, to}` and the reporting totals,
  narrowed to the caller like the month. UTC, as every reset time beside them. One session, three
  totals queries (`ReportingService.totals`), without the breakdowns a report screen needs.
- **FR-2 — the sidebar.** *AIRA budget*: the dimension that refuses first across every allowance
  that binds the caller, as a 20-cell bar with its percentage and `N of M <unit> left` (or `… over the
  limit`), whose counter and when it resets; then *Today*, *This week*, *This month* as cost and
  requests, with `≥` where the record is a floor. *Not enforced* where limits stop nothing. Within
  the sidebar's 37 columns.
- **FR-3 — narrow terminals.** Where OpenCode hides the sidebar (120 columns and below), one line in
  the prompt footer: `AIRA · 85% used · 0.37 EUR today`.
- **FR-4 — `/budget`.** A slash command and a palette entry opening a dialog with every allowance,
  each with its bar, and the three windows with cost, requests and tokens; read again as it opens;
  the reason where the gateway could not be read.
- **FR-5 — when it reads.** At start (not awaited), at the end of every turn, and every five minutes
  for spend from elsewhere — another session, a colleague on a shared pot. Five minutes and not one
  because each read sums three windows of the audit trail. One read at a time.
- **FR-6 — when it interrupts.** A toast only when a limit is crossed upwards — into four fifths,
  into the limit — and once per distinct reason the budget cannot be read.
- **FR-7 — where the gateway is.** The `aira` provider; another only if it is the one provider with
  both an address and a key; the address without `/v1beta`; `AIRA_BASE_URL`/`AIRA_API_KEY` override
  and never pair with another provider's half. The key OpenCode sends: OpenCode 1's resolved provider
  key; OpenCode 2's `settings.apiKey`, then the environment variables the provider names, then the
  active credential stored for it. Of the provider's headers, only the use-case selector is sent.
- **FR-8 — one file.** `tools/opencode/plugins/aira-budget/tui.tsx`, importing only `solid-js` and
  `@opentui/solid`, which OpenCode provides; the console hands out the same file.

## 5. What is retired

The OpenCode 1 server plugin (`aira-budget.js`, toasts only) and the `/budget` prompt template. The
first does not run in OpenCode 2 and, beside the new plugin, would toast twice in OpenCode 1; the
second is the flow the owner described, and `FRD-139` §5 had measured a model ignoring it.

## 6. Distribution to workstations

Added after v0.1.7, which shipped the plugin; v0.1.8 is the first release to carry the archive.
The owner rolls it out to workstations with Terraform, which needs a fixed address that requires
no credentials.

- **FR-8 — an archive on every release.** The release job packs the plugin
  (`tools/opencode_plugin_archive.py`) as `opencode-aira-budget-<version>.tar.gz` and attaches it to
  the GitHub Release beside its `.sha256`. The notes list it. Its only member is
  `plugins/aira-budget/tui.tsx`, laid out relative to OpenCode's configuration directory, so
  unpacking into `~/.config/opencode` installs it for every project. It carries no `tui.json`:
  that file is the developer's own, and OpenCode 1's one entry is merged in by the rollout. The
  file is mode 0644 and owned by uid 0. The archive is reproducible (fixed time, no gzip name or
  time), so the published checksum can be recomputed from a checkout.
- **Why not a package.** Both generations install npm packages (`opencode plugin -g <spec>`,
  `opencode plugin add <spec>`). A trial against a local registry loaded the plugin in both. But
  GitHub's npm registry requires a token even to install a public package, unlike GHCR's anonymous
  pulls. npmjs.org would need an account and a scope outside this repository. A file on the Release
  needs neither.
- Tested by `tools/tests/test_the_opencode_plugin_ships_as_an_archive.py`, by the workflow's shape
  in `tools/tests/test_a_release_is_published_only_behind_the_gates.py`, and by
  `test_the_release_archive_unpacked_for_every_project_shows_the_budget`: OpenCode 1 and 2 with the
  archive unpacked into their configuration directory, sidebar and dialog read from the screen.
  Mutations `PA1`–`PA11`.

## 8. Security & Privacy

The plugin stores nothing. It reads a key OpenCode already holds for the gateway and sends it only
to that provider's own address — never one from another provider's entry. An early version asked
for a missing key and kept it in OpenCode 2's plugin storage, which writes a world-readable file;
it was dropped once every place OpenCode keeps a key was readable. The endpoint's new windows are
the caller's own figures over shorter dates: no new processing.

## 10. Testing & Acceptance Criteria

- `gateway/tests/test_a_caller_sees_their_own_allowance.py` — the windows on both sides of every
  boundary, worked out from the calendar; Monday weeks across a month; the caller's own only.
- `tools/clients/opencode-budget-plugin.test.ts` under Bun with OpenCode's module resolution, run by
  `tools/tests/test_the_opencode_plugin_holds_its_properties.py`: the arithmetic, locating the
  gateway, the sidebar, dialog and footer **rendered** by OpenTUI's test renderer and compared line
  by line, and both generations' glue against their APIs.
- `gateway/tests/test_real_gemini_clients.py` — OpenCode 1.19.3 and 2.0.32 run a coding turn against
  the app (OpenCode 2 also with the console's configuration), and both terminal interfaces show the
  sidebar and the dialog, read from a `tmux` pane. In the `clients` CI job, which gates a release.
- Measured by hand as well: OpenCode 1.4.0 (the oldest supported, before the keymap API) and a key
  kept only in OpenCode 2's credential store.
- Mutations `OB1`–`OB18`, `AL9`, `AL10`, `MG-23`.
