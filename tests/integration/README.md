# Integration tests

Server-side checks that need the **live Compose stack**. They carry the `integration` marker, which
the default hermetic `pytest` run excludes, and run with `make test-integration`
(`uv run pytest -m integration --no-cov`).

> **This suite calls a paid cloud model.** Whenever the stack has Vertex credentials,
> `test_speech_live.py` catalogues Vertex's `gemini-2.5-flash-tts` in `europe-west1` and asks it
> for speech twice, once buffered and once streamed. Without Vertex credentials nothing serves that
> name and both cases skip. To run everything else without it:
>
> ```bash
> uv run pytest -m integration --no-cov --ignore=tests/integration/test_speech_live.py
> ```

## What it needs

The stack CI starts, in the same order:

```bash
make up-full        # everything in containers, demo provisioning and the local models included
make wait-healthy   # until the console, gateway, Management and Keycloak answer
make demo-ready     # until the demo's keys and models have reached the gateway
make test-integration
```

The suite talks to the gateway and Management over HTTP, to both PostgreSQL databases directly, to
Keycloak through the development realm's test service accounts, and to Redis, Vault, the collector
and the local model; it also runs `docker` on the host. Before the first test it prints what the
gateway's `/readyz` reports, so a degraded stack shows above the failures it causes.
`test_diagnostics.py` stops the local model's container on purpose and starts it again in a
`finally` — after an interrupted run, `docker start aira-ollama` brings it back. A stack started
without the demo profile has no local model; the tests that need one skip.
`-m "integration and not model_output"` leaves out the few tests that read what a real model
answered, which a model's drift can fail without a defect anywhere in this repository.

Each session removes what it created — every creation is written to
`tests/integration/.artifacts/created.jsonl` before it is made — and then checks that the
installation-wide counts it took at the start have not grown (`installation.py`). Two sessions
against one stack at the same time are not supported: each would tidy the other's work away.

## Split of responsibilities

| Suite | Runs against | Covers |
|---|---|---|
| `tests/integration/` (this folder) | the containerised stack: both planes, both databases, Kafka, Keycloak, Redis, Vault, the collector and a local model | both API surfaces against a real model, the controls on the request path, configuration reaching the gateway over Kafka, Management's API by role |
| `e2e/` (Playwright) | the same stack, with the SPA in a real browser | the browser-facing behaviour: OIDC login, layout at real widths, the UI's server interactions |

Anything that needs a **user token** belongs in `e2e/`: the dev realm has the direct-access
grant disabled, so a token for a person can only be obtained by completing the code flow in a
browser. This suite uses service-account tokens (`client_credentials`) instead.
