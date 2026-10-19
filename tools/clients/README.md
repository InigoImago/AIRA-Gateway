# Real Gemini clients

Every major of the AI SDK's Google provider (`@ai-sdk/google` 1–4), each with the `ai` release it
ships for, installed side by side under npm aliases. `run.mjs` uses one of them the way an
application does — a tool call and its result, a structured answer, a stream — and prints what came
back; `gateway/tests/test_real_gemini_clients.py` serves the gateway on a socket, runs it for every
major, and checks both the answers and which form of the request each client sent.

```bash
make test-clients        # npm ci here, then the test, which fails rather than skips
```

`uv run pytest` skips the Node half when nothing is installed here; CI's `clients` job does not.
