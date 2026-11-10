# Integration tests

Server-side checks that need the **live Compose stack**. They carry the `integration` marker, which
the default hermetic `pytest` run excludes, and run with `make test-integration`
(`uv run pytest -m integration --no-cov`).

> **This suite calls a paid cloud model.** Whenever the gateway has a Vertex project and a way to
> authenticate to it — a credential, a credentials file, the metadata server, or running on Google
> Cloud — from its environment or its Vault, `test_speech_live.py` catalogues Vertex's
> `gemini-2.5-flash-tts` in `europe-west1` and asks it for speech twice, once buffered and once
> streamed. The model need not be configured anywhere. Without Vertex the gateway answers `404` for
> that name and both cases skip; no marker leaves them out. The test replaces the gateway's
> catalogue entry of that name for the run and deletes it afterwards. To run everything else
> without it:
>
> ```bash
> uv run pytest -m integration --no-cov --ignore=tests/integration/test_speech_live.py
> ```
>
> `test_serving_options.py` addresses a real catalogued model as well — the first approved one with
> a bounded thinking mode, a paid one where the catalogue has it — with a request the gateway must
> refuse before dispatch, so it spends only if that refusal is broken. Every other model request in
> the suite asks for the mock model, a local model, or a name nothing serves. CI's stacks have no
> cloud credentials.

## What it needs

The stack CI starts, in the same order:

```bash
make up-full        # everything in containers, demo provisioning and the local models included
make wait-healthy   # until the console, gateway, Management and Keycloak answer
make demo-ready     # until the demo's keys and models have reached the gateway
make test-integration
```

**Both data modes.** Strict data mode is on by default, and what a reader sees differs with it off,
so CI runs the integration and the browser suite once in each (`AIRA_STRICT_DATA_MODE=true` and
`false`, a matrix over both stack jobs). Locally, the suites ask the running stack which mode it is
in and check that mode's answer, so the same commands test either; switch the stack between them
with `make showcase-strict-off` and `make showcase-strict-on` (`make showcase-mode` says which runs),
or start it with the variable set: `AIRA_STRICT_DATA_MODE=false make up-full`.

**The mail round needs the mail catcher.** `test_mail_round.py` uses up a budget and reads the mail
it caused out of the showcase's mail catcher (`mailpit`, on port 8025), so the gateway has to send
there, with a link to the console. CI's integration job starts the stack with these, and so must a
local run that includes that file:

```bash
AIRA_SMTP_HOST=mailpit AIRA_SMTP_PORT=1025 AIRA_SMTP_SECURITY=none \
AIRA_MAIL_FROM=aira@demo.aira AIRA_CONSOLE_URL=http://localhost:4200 make up-full
```

Without them that test fails after two and a half minutes; it does not skip.

The suite talks to the gateway and Management over HTTP, to both PostgreSQL databases directly, to
Keycloak through the development realm's test service accounts, and to Redis, Vault, the collector,
the mail catcher and the local model; it also runs `docker` on the host, into the Vault, Kafka and
model containers among others. Before the first test it prints what the gateway's `/readyz`
reports, so a degraded stack shows above the failures it causes.
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
