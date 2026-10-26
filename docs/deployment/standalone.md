# Deployment 3 of 4: Standalone

**For:** running the whole product on one machine, in containers, without touching your
organisation's infrastructure.
**You need:** Docker with Compose, `make`, `python3`, [`uv`](https://docs.astral.sh/uv/) and
`curl`; about 20 GB of disk.
**Shape:** every component in a container, including the two application planes.

This is the setup for a workshop machine, a demonstration laptop, or a first evaluation. It is
[Showcase](showcase.md) without the traffic: the same composition, demo provisioning included — the
development realm and its accounts, the seeded use cases, budgets, rules and API keys, and two small
local models in Ollama — but nothing waits for the models, and no request is sent. What you add,
you add through the console.

---

## Step 1: start everything

```bash
make up-full
```

This creates `deploy/compose/.env` from `deploy/compose/.env.example` if needed, builds the three
application images, and starts:

| Container | Port on `127.0.0.1` | What it is |
|---|---|---|
| `aira-frontend` | 4200 | the console |
| `aira-gateway` | 8001 | the data plane, where model requests go |
| `aira-management` | 8002 | the control plane API |
| `aira-management-relay` | — | moves configuration changes to Kafka, every 10 seconds |
| `aira-gateway-consumer` | — | applies them to the gateway |
| `aira-gateway-retention` | — | deletes expired stored payloads, hourly |
| `aira-keycloak` | 8080, 9000 | sign-in: the development realm, imported on first start; 9000 is its health port |
| `aira-postgres` | 5432 | the gateway's, Management's and Keycloak's databases |
| `aira-kafka` | 29092 | the event bus |
| `aira-redis` | 6379 | shared counters for rate limits and budgets |
| `aira-vault` | 8200 | Vault in development mode — read only when `VAULT_ADDR` and a token or role id are set |
| `aira-schema-registry` | 8081 | runs; nothing uses it |
| `aira-otel-collector` | 4317, 4318, 8889 | OTLP in, and its own counters |
| `aira-otel-lgtm` | 3000 | Grafana, with a provisioned dashboard — it receives nothing until `AIRA_OTEL_ENABLED=true` |
| `aira-ollama` | 11434 | the two local models |

And, once each, the migrations of both planes, the topics, the realm repair (`keycloak-init`), the
development Vault's secrets (`vault-init`), the seed (`management-seed`) and the model download
(`ollama-pull`).

Wait for health, and then for the models:

```bash
make ps            # every service, and whether it is healthy
make demo-ready    # until the download has finished and the gateway serves both models
```

The download runs in the background, so a stack whose containers are all healthy may still be
without its models for a few minutes; `make demo-ready` is what waits for them.

### Without building: run a published release

`make up-full` builds the three application images from this checkout. A released version can be
pulled instead:

```bash
make up-full RELEASE=vX.Y.Z
```

Same stack, same infrastructure containers, same ports — the three AIRA images come from
`ghcr.io/inigoimago/…:vX.Y.Z` and nothing is built. `RELEASE=` works on `make showcase` too. It does
not remove the need for `make`, `python3`, `uv` and `curl`: those run the targets, not the product.
`make current-version` prints the newest version this checkout's tags name; there is no default,
because `latest` moves under a restart and a stack that cannot say which version it runs is what
the version numbers exist for.

---

## Step 2: sign in

The accounts already exist: the realm import created them in Keycloak, and the stack's own seed
created their counterparts in Management — `make seed` is not needed here. Sign in at
<http://localhost:4200> as `admin` / `demo-password`: the Global Administrator, who may create use
cases and price models. `itsec`,
`itgov`, `ucadmin` and `ucuser` are the others, all with `demo-password`; [Showcase](showcase.md)
Step 3 says what each one sees.

---

## Step 3: give it a model

**Only catalogued, approved models may be used**, and a use case may call only the models released
to it.

### The local models are already there

`qwen3:0.6b` for chat and `all-minilm` for embeddings are catalogued and approved by the seed — with
prices that are invented and labelled so — once the download has finished; `make demo-ready` waits
for that. The gateway reaches them through `AIRA_OPENAI_SERVERS` in `deploy/compose/.env`.

### Or a cloud model

The credential goes into `deploy/compose/.env`: `AIRA_VERTEX_PROJECT` with `AIRA_VERTEX_API_KEY`
(or `AIRA_VERTEX_CREDENTIALS`, the service account's JSON as one line — or
`AIRA_VERTEX_CREDENTIALS_FILE`, the path of the key file as the gateway container sees it) for Vertex AI in an EU region, or `AIRA_GOOGLE_API_KEY` for Google AI
Studio — which also needs `global` in `AIRA_ALLOWED_REGIONS`, or the gateway refuses to start. Then
`make up-full` again. This setup's Vault runs in development mode and keeps nothing across a
restart, so on this machine the key lives in that file; [Integrated](integrated.md) §4 is how a key
leaves it.

Then catalogue the model in the console:

1. Sign in as `admin`.
2. **Models & prices → + Add a model**, and choose the provider.
3. Under **From the model reference**, find the model and press **Catalogue…**: the editor opens
   with its limits, capabilities, how it reasons and — where this installation counts in US
   dollars — its list prices filled in from a public model list, and says so. Check them; add the
   region the model is reached in. A model the reference does not carry, you name yourself: the
   name exactly as the provider spells it, the prices per million tokens, and what the model can
   do. By default a model with no price is refused wherever a cost budget applies.
4. Press **Check reachability**. You check before you add — not because the answer has to be good,
   but because *"I did not know it was unreachable"* is the one outcome a single button can rule
   out.
   When the answer is no, the editor says why under the regions — an API not enabled, a missing
   role, a model not offered in that region, a proxy whose certificate is not trusted — and what to
   change.
5. Tick **Approved for use** and save.

---

## Step 4: a use case, its model, and a key

1. **Use cases → + New use case** (as `admin`). A name; the technical id is derived from it. You
   are its administrator from then on.
2. On its **Overview** tab, under **Models this use case may call**, release the model —
   `qwen3:0.6b` for the local one. A use case with nothing released can call nothing.
3. On its **Members** tab, add the people or a group who will use it.
4. On its **API keys** tab, issue a key. **The plaintext is shown once and never again.** If you are
   pointing a coding assistant at it, take the OpenCode configuration from the same panel — it is
   generated at that moment because that is the only moment the key exists.

The use case, its release and its key reach the gateway over Kafka. The relay here runs every ten
seconds, and until the use case has arrived a request for it is refused `403` *"not known to this
gateway"* — wait a moment. Then call it:

```bash
curl -s http://localhost:8001/uc/<use-case>/v1beta/models/qwen3:0.6b:generateContent \
  -H "x-goog-api-key: aira_..." \
  -H "content-type: application/json" \
  -d '{"contents":[{"parts":[{"text":"Say OK"}]}]}'
```

`/uc/<use-case>` names the use case, which every model call does — with a key as much as with a
sign-in; the key may name only the one it was issued for. The request appears on the use case's
**Requests** tab once its row is written.

---

## Step 5: check it is healthy

```bash
curl -s localhost:8001/readyz | jq '.status, .degraded'
```

`ready` means Postgres answered — readiness depends on nothing else. `degraded: false` means Kafka,
Redis and every configured upstream answered too; `degraded: true` with a 200 is deliberate: the
gateway still serves, with a named control on a fallback rather than silently unbounded. Here, with
`AIRA_ENVIRONMENT=local`, the whole body is shown to anyone (`curl -s localhost:8001/readyz | jq`),
and the gateway's API documentation is at <http://localhost:8001/docs> — both only locally or in
demo mode.

---

## What is configurable here

`make up-full` creates `deploy/compose/.env` from the example, and that is what the stack runs on.
The file an integrator edits is [`config/standalone.example.yaml`](../../config/standalone.example.yaml)
— one YAML, every setting under a section, comments saying what each one refuses. Its header says
what a standalone installation on the bundled stack costs, and why it stays `environment: local`.

It does nothing until it is rendered into the environment both planes read. Copy it first, so the
tracked example stays as it is:

```bash
cp config/standalone.example.yaml config/my-standalone.yaml
uv run python tools/config_render.py config/my-standalone.yaml -o deploy/compose/.env
make up-full
make config-verify      # does the deployment run what the file says?
```

**What it does not carry:** credentials, which `config_render.py` refuses rather than emits — they
belong in Vault — and the infrastructure containers' own settings (`POSTGRES_PASSWORD`,
`KEYCLOAK_ADMIN`, `AIRA_BIND_HOST`, the `AIRA_PUBLISH_*` ports), which are not AIRA settings.
Compose fills those from the development defaults in `docker-compose.yml`, and both planes' database
password comes from `POSTGRES_PASSWORD`. That is fine for a workshop machine and is the thing
[Integrated](integrated.md) must not inherit.

---

## Stopping and starting again

```bash
make down-full          # stop, keep the data
make up-full            # start again with the data
make destroy            # stop and delete everything
```

`make destroy` deletes every volume: the audit trail, the configuration, the realm and the
downloaded models.

---

## What this setup is not

The compose file ships a **development** Keycloak realm with fixed passwords, a published Postgres
password and Vault in dev mode, and the applications run with `AIRA_ENVIRONMENT=local`, which
switches every start-up check off. That is fine on a laptop and unacceptable anywhere else. Outside
`local` the gateway refuses the published Postgres password, a missing audience, `PLAINTEXT` Kafka
and plain-HTTP identity and Vault addresses — unless `AIRA_DEMO_MODE` is on, which waives exactly
those — and Management refuses its own development defaults with no waiver at all.

The moment more than one person uses it, read [Integrated](integrated.md).

---

Next: [Showcase](showcase.md) · [Development](dev.md) · [Integrated](integrated.md)
