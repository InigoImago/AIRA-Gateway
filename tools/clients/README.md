# Real clients

The clients in use, installed side by side so the gateway is tested against what they actually
send:

- every major of the AI SDK's Google provider (`@ai-sdk/google` 1–4), each with the `ai` release it
  ships for, under npm aliases. `run.mjs` uses one of them the way an application does — a tool
  call and its result, a structured answer, a stream — and prints what came back;
- OpenCode 1 (`opencode-ai`) and OpenCode 2 (`@opencode/cli`), at pinned versions;
- Bun, with `solid-js` and `@opentui/*`, which the OpenCode budget plugin's own tests run on
  (`opencode-budget-plugin.test.ts`, loaded through `preload-opencode-plugin.ts`).

`gateway/tests/test_real_gemini_clients.py` serves the gateway on a socket and runs each AI SDK
major and Google's `google-genai` against it, checking both the answers and which form of the
request each client sent. It runs both OpenCodes through a coding turn headless, and their terminal
interface in a `tmux` pane with the budget plugin installed — from the project, and from the
release archive unpacked into OpenCode's configuration directory — read back from the screen.
`tools/tests/test_the_opencode_plugin_holds_its_properties.py` runs the plugin's tests under Bun.

```bash
make test-clients        # npm ci here, then both test files; needs tmux
```

Node 22 or newer (`engines`), and network access for `npm ci` and for OpenCode 1, which installs
its AI SDK provider on first use. `uv run pytest` skips the Node clients and the plugin's tests when
nothing is installed here, and the terminal tests when `tmux` is missing; the `google-genai` cases
need neither. `make test-clients` and CI's `clients` job set `AIRA_REQUIRE_REAL_CLIENTS=1`, which
turns every such skip into a failure.

Every OpenCode run has a home directory of its own, so nothing of the developer's OpenCode is read
or written. Every client asks the gateway on the test's socket for its mock model, so no run reaches
a paid one.
