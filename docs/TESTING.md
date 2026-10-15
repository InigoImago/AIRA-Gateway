# Testing strategy

AIRA is tested in layers, each for what the layer below cannot see. The hermetic layer runs
anywhere **without any infrastructure**; the others need the live stack and run on demand and in CI.

| Layer | Command | Needs |
| --- | --- | --- |
| Hermetic unit tests — Python, the repository's own checks, the console | `make test`, `make ci` | `uv` and Node; no Docker |
| Mutation checks | `make mutants` | the same |
| Integration tests | `make test-integration` | the containerised stack, ready (below) |
| A real local model | `make test-verify` | the stack with its local models |
| Load runs | `make loadtest` | the stack and the load overlay |
| Browser tests | `make test-e2e` | the stack, Playwright and Chromium |
| Windows sign-on | `make test-windows-sso` | the stack and a Windows domain beside it |
| UI audit | `make ui-audit` | the stack, Playwright and Chromium |

## The hermetic layer (default)

Runs everywhere, no services required. This is what CI's first two jobs run on every push.

- **Python** (`make test-py` → `uv run pytest`): `gateway/tests`, `libs/tests`,
  `management/backend/tests` and `tools/tests`. The product's tests use **in-memory SQLite**
  (auto-detected under pytest), a **fake JWKS resolver** + self-signed RS256 for OIDC, the
  **deterministic mock provider**, and FastAPI `TestClient` / `httpx.ASGITransport`. No
  Postgres/Keycloak/Kafka/OTel needed, and the gateway's suite empties `AIRA_REDIS_URL` for the
  whole session, so a Redis running on the machine is never shared between runs.
- **The repository's own checks** (`tools/tests`) read the repository rather than the product: that
  a document names only `make` targets, ports, topics and links that exist, that the Compose files,
  the Makefile and CI agree with each other, that the two planes agree on the event vocabulary and
  the topic list, that the configuration examples name real settings. A claim a reader acts on is a
  claim a test can hold to the code.
- **Console** (`make test-frontend`): Angular unit tests via **Vitest + jsdom** — no browser.
- Enforced by `make ci` and by CI: `ruff check`, `ruff format --check`, `mypy` (strict), Prettier,
  the console's production build, Prettier and `tsc --noEmit` over the browser suite, and a coverage
  **floor**. The floors are where they are so that CI fails on a *drop*, and they are deliberately
  below the actual figures:

  | | gate | last measured |
  | --- | --- | --- |
  | Python (`--cov-fail-under`) | **90%** | 97.26% |
  | Console statements | 90% | 95.9% |
  | Console branches | **92%** | 93.8% |
  | Console lines | 93% | 97.2% |
  | Console functions | 75% | 86.7% |

  The last measurement was taken with 6397 Python tests and 1523 console tests; `make test-py` and
  `make test-frontend` print the current figures at the end of their runs. **A number here is a
  floor and not an achievement**: 100% enforced would mean no line could be added without a test
  that reaches it, and that is not what CI does.

Proof of the hermeticity: with the **entire Compose stack stopped**, `uv run pytest` passes.

`make ci` is `make lint` and `make test` — `lint-py` and `lint-frontend`, `test-py` and
`test-frontend`. It is the hermetic half of CI, not all of it: the integration and browser jobs run
on live stacks.

## Mutation checks (hermetic, on demand)

`make mutants` (`tools/mutation_check.py`) breaks one property at a time and requires a named test
to notice. How many properties it guards is defined in one place, in the mutation harness,
`tools/mutation_check.py`, and a run says it — a passing one ends *All N properties are defended
by at least one test*. It is not repeated here, because a claim about how much of a system is
checked is exactly the sort that rots quietly. It is the answer to *"a green test proves only that
the code and the test agree"*: a property nothing would notice losing is reported as a
**survivor**, and a mutation whose anchor has moved as **stale** — never as a pass. Run it when
adding a rule worth keeping, and add the mutation that reintroduces a bug you have just fixed.

A selection's Python paths run under pytest and its console specs (`*.spec.ts`) under
`ng test --include`, and both must be green for the baseline. A mutant that does not parse or does
not build is reported **broken** rather than caught — it fails every test that loads it, whatever
the tests check. Each run has a time limit (`--timeout`, 900 s by default), so a mutant that makes a
test hang is reported rather than waited for. To run a subset, name the mutations by their ids —
the first field of each entry in the harness — with
`uv run python tools/mutation_check.py --only <id>,<id>`. A run that is killed leaves a journal,
and the next one restores the file first — but only a file that still holds the mutant, never one
somebody has edited since.

## Integration tests (the live stack, opt-in)

`tests/integration/`, marked `@pytest.mark.integration` and **excluded from the default run**
(`-m 'not integration'` in `addopts`). They run against the real Postgres, Keycloak, Kafka, Redis,
Vault, collector, local model and both planes, with realm-signed tokens from the development realm's
service-account clients. Bring the stack up first, and wait for it properly:

```bash
make up-full            # infrastructure, both planes, the console, the demo provisioning
make wait-healthy       # block until the four HTTP endpoints answer
make demo-ready         # block until the models and keys have reached the gateway
make test-integration   # uv run pytest -m integration --no-cov
```

**Both data modes.** Strict data mode is on by default, and what a reader sees differs with it off,
so CI runs the integration and the browser suite once in each (`AIRA_STRICT_DATA_MODE=true` and
`false`, a matrix over both stack jobs). Locally, the suites ask the running stack which mode it is
in and check that mode's answer, so the same commands test either; switch the stack between them
with `make showcase-strict-off` and `make showcase-strict-on` (`make showcase-mode` says which runs),
or start it with the variable set: `AIRA_STRICT_DATA_MODE=false make up-full`.

> **`make test-integration` can spend money.** `tests/integration/test_speech_live.py` sends
> requests to the paid Vertex model `gemini-2.5-flash-tts` (in `europe-west1`) whenever the stack
> has Vertex credentials — in `deploy/compose/.env` or in its Vault — and skips only when nothing on
> the stack serves that model. On a machine configured with a real Vertex project, remove the
> credentials first, or run the suite without that file:
> `uv run pytest -m integration --no-cov --ignore=tests/integration/test_speech_live.py`.
> CI's stacks have no cloud credentials.

**`demo-ready` is not optional.** `wait-healthy` answers *"do four endpoints respond?"*; the suite
calls models, and both planes learn the model catalogue through the seed. The seed deliberately
waits only for the model *download* to **start**, and the download fetches the chat model before the
embedding one — so a stack whose endpoints all answer can still hold a catalogue with one of the two
models in it, and the suite then fails on an embedding model with no failure mentioning a
download. `demo-ready` blocks until every demo credential and **every** demo model has crossed
seed → relay → Kafka → gateway, and it is the same target `make showcase` uses.

`make test-verify` runs one file of the suite, `tests/integration/test_local_model.py`: a real
model through the whole governed path, asserting what the gateway recorded rather than what the
model said. It skips, saying why, when no local model is serving. The containerised stack has one;
for a gateway run from source, `make verify-up` starts Ollama with the two small models and prints
how to point the gateway at them, and `make verify-down` stops it again.

### The same commands against a published release

`RELEASE=vX.Y.Z` on the first one runs the whole layer against the images a release published,
rather than against a build of the checkout:

```bash
make up-full RELEASE=vX.Y.Z && make wait-healthy && make demo-ready && make test-integration
```

This is the only thing that answers *"are the images we publish functionally complete?"* With the
database volume deleted first (`aira_pgdata`), the published images' migrations create both schemas
from nothing. It is a fair oracle only while the product code under test is the released product
code; when it is not, a failure may be the suite moving on rather than the image being wrong, and
the suite cannot tell you which.

## Load runs (`tools/loadtest/`, the live stack, opt-in)

Not a test: nothing about a load run is red or green, and it asserts on no number. `make loadtest`
drives the three real workloads — agentic coding, chat with a RAG, nightly embedding batches —
against a **free upstream double** whose latency is declared, so the gateway's own cost is what
remains when that latency is subtracted.
Every step runs twice, once at the gateway and once straight at the double, because a figure about
the gateway that was never compared with the same load *without* it is a figure about whatever was
slowest that afternoon.

The overlay (`docker-compose.loadtest.yml`) empties the cloud settings in the gateway's
**environment** for the duration and points it at the double. It does not touch Vault, and Vault
ranks above the environment: a cloud credential held there is not emptied. Run load tests on a stack
whose Vault holds none — with `VAULT_ADDR` empty, the bundled default, nothing comes from Vault at
all. [`tools/loadtest/README.md`](../tools/loadtest/README.md) has the scenarios.

The harness itself is tested in the default run — `tools/tests/test_the_load_double_keeps_its_promise.py`
— because a double that overshoots its promise does not look slow, it makes the gateway look slow.
Results go to [`docs/measurements/`](measurements/), sizing guidance to
[`DEPLOYMENT.md`](DEPLOYMENT.md) §1a.

## Browser tests (`e2e/`, need the whole thing)

Playwright against the running console, gateway and Keycloak (`make test-e2e`). This layer exists
for what the ones above **structurally cannot see**: a real authorization-code flow, a control that
renders and does nothing, a layout that only a viewport has an opinion about. Anything needing a
*user token* belongs here — the dev realm has the password grant off, so a token only comes from
the real flow. It needs the same ready stack as the integration tests (`make up-full`,
`make wait-healthy`, `make demo-ready`) and Chromium for Playwright
(`cd e2e && npx playwright install --with-deps chromium`); [`e2e/README.md`](../e2e/README.md) has
the rest.

The suite cleans up after itself, passed or not: Playwright's teardown retires the use cases it
made, and `make test-e2e` then purges their tombstones with `make purge-e2e-use-cases` — a step for
a demo installation only, which no HTTP route offers.

### Beside it: the Windows sign-on (`make test-windows-sso`)

Its own suite and its own config (`e2e/windows-sso`), because its precondition is one every other
spec must not have: a Windows domain beside the stack — Samba as the domain controller, profile
`windows-sso` — and a realm federated with it. The target starts the domain; the suite's global
setup gives the realm its half of the sign-on (`tools/windows_sso.py apply`) and its teardown takes
it away again, passed or not. Each test makes the people it needs in the domain and opens a browser
that stands in for one of its workstations, holding that person's ticket or none — console and
gateway, the people who get in and those who must not.
It **refuses rather than skips** when the domain is down or Chromium cannot load MIT Kerberos
(`libgssapi_krb5.so.2`). CI runs it after the browser suite, in the browser job. The configuration
it stands on is guarded in the default run too, by `tools/tests/test_windows_sso.py`.

### Beside it: the UI audit (`make ui-audit`)

Not a test and never red: a walk through every page, tab and window for each of the five demo
accounts, at desktop and phone width, that writes `e2e/ui-audit-report/report.md` and a screenshot
per state (`e2e/README.md`). It checks what no spec is written for — console errors, failed
requests, overflow, overlapping or covered controls, squeezed columns, WCAG 2.2 AA via axe-core,
target size, visible focus, `undefined` on the page, and routes nobody can navigate to. The
screenshots are for the review a rule cannot do: hierarchy, spacing, wording. A finding worth
keeping becomes a spec or a guard in `tools/tests/`; the report itself is not a record.

## CI (`.github/workflows/ci.yml`)

Every push to any branch, every pull request, and every release tag runs:

| Job | Runs | Limit |
| --- | --- | --- |
| `python` | `uv sync --frozen`; `ruff check`, `ruff format --check`, `mypy`; `uv run pytest` with the coverage gate | 20 min |
| `frontend` | `npm ci`; Prettier over the console and `proxy.conf.cjs`; the production build; the unit tests with the coverage gate; Prettier and `tsc --noEmit` over the browser suite | 20 min |
| `stack` | `make up-full`, `make wait-healthy`, `make demo-ready`, then the integration suite; the application logs on failure; `make down-full` | 40 min |
| `browser` | a stack of its own, made ready the same way; Chromium; the browser suite, then `make test-windows-sso`; the logs and the Playwright report on failure | 30 min |

The two stack jobs run side by side, each on its own stack. The first two run the commands of
`make lint-py`, `make test-py`, `make lint-frontend` and `make test-frontend` written out, so each
job installs its toolchain once; `tools/tests/test_ci_runs_what_a_developer_runs.py` holds the two
in step.

A pushed release tag adds two jobs: `described`, which checks the tag's annotation with no
dependencies so a bad one is refused within a minute, and `publish`, which needs all five and builds
and pushes the three images ([`DEPLOYMENT.md`](DEPLOYMENT.md) §1b).

### Running the same elsewhere

The hermetic half needs `uv` (which fetches Python 3.14) and Node 26, and no Docker:

```bash
uv sync --frozen
(cd management/frontend && npm ci)
(cd e2e && npm ci)          # `make lint-frontend` type-checks the browser suite
make ci
```

The live half needs Docker, and one stack per suite run:

```bash
make up-full && make wait-healthy && make demo-ready
make test-integration
(cd e2e && npx playwright install --with-deps chromium)
make test-e2e
make test-windows-sso
make down
```
