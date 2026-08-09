# Deployment 3 of 4: Standalone

**For:** running the whole product on one machine, in containers, without a checkout of anything
else and without touching your organisation's infrastructure.
**You need:** Docker and about 2 GB of disk.
**Shape:** every component in a container, including the two application planes.

This is the setup for a workshop machine, a demonstration laptop, or a first evaluation. It differs
from [Showcase](showcase.md) in one way: it does **not** pull a real model or send traffic. You get
an empty, working installation and fill it yourself.

---

## Step 1: start everything

```bash
make up-full
```

This creates `deploy/compose/.env` from the example if needed, then starts:

| Container | Port | What it is |
|---|---|---|
| `aira-frontend` | 4200 | the console |
| `aira-gateway` | 8001 | the data plane, where model requests go |
| `aira-management` | 8002 | the control plane API |
| `aira-management-relay` | — | moves configuration changes to Kafka |
| `aira-gateway-consumer` | — | applies them to the gateway |
| `aira-gateway-retention` | — | deletes expired stored payloads, hourly |
| `aira-keycloak` | 8080 | sign-in |
| `aira-postgres` | 5432 | both databases |
| `aira-kafka`, `aira-redis`, `aira-vault` | — | event bus, shared counters, secrets |
| `aira-otel-lgtm` | 3000 | Grafana, for traces |

Wait for health:

```bash
make ps
```

---

## Step 2: create the roles and users

```bash
make seed
```

Creates the five realm roles and one demo user per role. Sign in at <http://localhost:4200> as
`admin` / `demo-password`.

---

## Step 3: give it a model

An empty catalog means nothing can be called: **only catalogued, approved models may be used**
(`FRD-307`). You have two options.

### Option A: a local model, no accounts

Start Ollama alongside the stack and pull something small:

```bash
make verify-up          # starts Ollama and pulls a small chat and embedding model
make seed-local-catalog # declares them in the catalog, approved
```

### Option B: a cloud model

Put the credential in Vault rather than in a file — see [Integrated](integrated.md) §4 for the
full procedure. Then catalogue the model in the console:

1. Sign in as `admin`.
2. **Models & prices → + Add model**.
3. Fill in the name exactly as the provider spells it, the prices per million tokens, and what the
   model can do.
4. Press **Check reachability**. You must check before you can add — not because the answer has to
   be good, but because *"I did not know it was unreachable"* is the one outcome a single button can
   rule out.
5. Tick **Approved for use** and save.

---

## Step 4: create a use case and a key

1. **Use cases → New use case.** A name; the technical id is derived from it.
2. On its **Access** tab, grant yourself or a group.
3. On its **API keys** tab, issue a key. **The plaintext is shown once and never again.** If you are
   pointing a coding assistant at it, take the OpenCode configuration from the same panel — it is
   generated at that moment because that is the only moment the key exists.

Then call it:

```bash
curl -s http://localhost:8001/v1beta/models/<model>:generateContent \
  -H "x-goog-api-key: aira_..." \
  -H "content-type: application/json" \
  -d '{"contents":[{"parts":[{"text":"Say OK"}]}]}'
```

The request appears in the console's **Requests** view within a second or two.

---

## Step 5: check it is healthy

```bash
curl -s localhost:8001/readyz | jq '.status, .degraded'
```

`ready` and `false` means Postgres, Kafka and the counter store all answered. `degraded: true` with
a 200 is deliberate: the gateway still serves, with a named control on a fallback rather than
silently unbounded.

---

## Stopping and starting again

```bash
make down-full          # stop, keep the data
make up-full            # start again with the data
make destroy            # stop and delete everything
```

---

## What this setup is not

The compose file ships a **development** Keycloak realm with fixed passwords, a published Postgres
password, and Vault in dev mode. That is fine on a laptop and unacceptable anywhere else — and the
gateway enforces it: outside a `local` environment it refuses to start with any of those, naming
every reason at once (`ADR-0015`).

The moment more than one person uses it, read [Integrated](integrated.md).

---

Next: [Showcase](showcase.md) · [Development](dev.md) · [Integrated](integrated.md)
