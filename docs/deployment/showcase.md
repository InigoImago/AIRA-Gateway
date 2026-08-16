# Deployment 1 of 4: Showcase

**For:** seeing the whole product work, with real traffic, in about fifteen minutes.
**You need:** Docker, roughly 8 GB of free disk, and no accounts anywhere.
**You do not need:** a cloud provider, an API key, Python, Node, or a Keycloak of your own.

This is the only setup that pulls a real model and sends real requests through it. Everything else
on this page is a consequence of that: the figures you see afterwards are measurements, not
fixtures.

---

## What you will have when this finishes

- The whole stack in containers: gateway, control plane, console, Postgres, Keycloak, Kafka, Redis,
  Vault, an OpenTelemetry collector and Grafana.
- A small language model (`qwen2.5:3b`, about 2 GB) running locally in Ollama.
- Three use cases with budgets, rate limits, pipelines, anomaly rules and one API key each.
- Five users, one per role.
- Real traffic already through the gateway, **including a prompt injection the filter refused** —
  so the security console has something true in it rather than rows somebody inserted.
- Somewhere to point a client you already have, over either API surface and with either an API key
  or its own Keycloak identity (Step 5).

---

## Step 1: check Docker

```bash
docker version
docker compose version
```

Both must print a version. If `docker version` says it cannot connect to the daemon, start Docker
Desktop (macOS, Windows) or `sudo systemctl start docker` (Linux) and try again.

Check the disk, because a failed model pull halfway through is the most common way this goes wrong:

```bash
df -h .
```

You want at least 8 GB free.

---

## Step 2: start it

```bash
make showcase
```

This runs for a while and prints what it is doing. In order:

1. **Creates `deploy/compose/.env`** from the example, if it does not exist.
2. **Starts the infrastructure** and waits for each container to report healthy.
3. **Pulls the model.** This is the slow part — a few minutes on a normal connection. It is a
   separate step on purpose, so that the container's health check does not report "ready" while a
   2 GB download is still running.
4. **Applies database migrations** for both planes.
5. **Seeds** the roles, users, use cases, budgets, limits, pipelines, rules and keys.
6. **Drives traffic** through the gateway with those keys.

If it stops with an error, read the last twenty lines: every step names what it was doing.

---

## Step 3: sign in

Open <http://localhost:4200>.

| Sign in as | Password | Shows you |
|---|---|---|
| `admin` | `demo-password` | everything, including model prices and approval |
| `itsec` | `demo-password` | security oversight across every use case, and the content of requests |
| `itgov` | `demo-password` | every figure, no write anywhere, no content |
| `ucadmin` | `demo-password` | administering two of the three use cases — the third is deliberately not theirs |
| `ucuser` | `demo-password` | a member's view |

The roles differ on purpose, and the differences are the point. See [`../ROLES.md`](../ROLES.md) for
what each may do and why.

---

## Step 4: what to look at, in order

**Requests** (as `itsec`) — every request across every use case. Open a row: the metadata is on the
left, and the prompt and the answer are one click away. The panel tells you the read was recorded,
because it was.

**Reporting** (as `itgov`) — spend and usage. The figures come from the traffic step 2 sent; nothing
here is seeded.

**Security** (as `itsec`) — findings, what is stopped, and the rules behind them. One rule throttles
rather than alerts, so you can see the difference.

**Use cases → Pipeline** (as `ucadmin`) — the filter that refused the injection. Use the test panel
to send the same prompt again and watch it be refused.

**Models & prices** (as `admin`) — the catalog. Open a row for everything on file about a model, and
press **Check reachability**: the local model answers, and a model this installation has no
credential for reports that it is declared and nothing serves it.

---

## Step 5: point an existing client at it

The showcase is a real gateway with a real Keycloak, so a client you already have — a
KIRA-compatible chatbot, an SDK, anything that speaks one of the two surfaces — can be aimed at it
without changing the client's code. Two ways in, and the first takes about a minute.

**The two base URLs.** Both are on the gateway, and both are also reachable through the console's
own proxy at `http://localhost:4200/gw/…` if that is easier to reach from where your client runs.

| Surface | Base URL | Verbs |
|---|---|---|
| KIRA compatibility | `http://localhost:8001/kira/api/external` | `POST /chat`, `POST /streaming-chat`, `POST /embed`, `GET /models`, `GET /health` |
| Gemini dialect | `http://localhost:8001/v1beta` | `POST /models/{model}:generateContent`, `:streamGenerateContent`, `:embedContent` |

### With an API key — the quickest test

`make showcase` prints one key per use case, and the console shows the same thing on a use case's
**Overview** tab — base URL, the models it may call, and a ready-made example per surface. Credentials are read in one order:
`Authorization: Bearer <token>` → `x-goog-api-key` → `?key=`, so a client that can set *either*
header works unchanged.

```bash
curl -s http://localhost:8001/kira/api/external/models \
  -H "x-goog-api-key: aira_…"      # the key printed for kundenservice
```

A key is bound to its use case, so nothing else has to be configured: attribution, budget, rate
limit and audit row all follow from the key.

### With its existing Keycloak identity

If the point is to test the client's *own* login rather than a key, register it in the showcase
realm. Keycloak is at `http://localhost:8080` (`admin` / `admin`), realm **`aira`**.

Which flow depends on what the client is, and the realm is deliberately strict about it:

- **A machine client** (a bot with no human at the keyboard) uses the **client-credentials** grant.
  The realm already contains four such clients for the integration tests — copy the shape of
  `aira-integration-tests`: confidential, service accounts on.
- **A client with a user in front of it** uses **authorization code + PKCE**, like `aira-gateway`.
  The **password grant is switched off on purpose** (`ADR-0007`), so a client that only knows how to
  exchange a username and password for a token cannot be used here, and that is the realm telling
  you something true about the product rather than a gap in the demo.

Then give it a use case, which is the step people miss. AIRA never writes to your directory: it
reads the **groups** in the token. For a machine client the groups belong to the service-account
user (`service-account-<clientId>`), not to the client — putting the *client* in a group does
nothing, and the request is refused with a message about membership.

Two ways to do it, both real:

1. **The convention.** Put the user in `/use-cases/kundenservice`. Nothing else to configure —
   the gateway resolves that path to the use case by name (`FRD-102`).
2. **Your own group.** Put the user in any group you like — the realm ships
   `/abteilungen/kundendienst` — and grant that group to a use case on its **Members** tab. This
   is what a real installation does, because it does not require the directory to be renamed
   around AIRA (`FRD-209`).

Point the client at the issuer `http://localhost:8080/realms/aira` and one of the base URLs above.

### What to look at afterwards

The interesting part is not that it answered. Open the console and find the client's own traffic:
**Requests** shows which credential and which identity, the use case's **Overview** shows what it
spent under Consumption, and if the client sent something the injection filter dislikes, the
**Security** console has it as a refusal with the prompt attached. A client connected this way is subject to every control
on this page — the same budget, the same rate limit, the same pipeline — which is the thing worth
demonstrating.

If it is refused, the message says which control did it. `Not a member of use case …` is the group
question above; `over budget` and `rate limit exceeded` mean the demo's deliberately small limits
did their job.

---

## Step 6: more traffic, and stopping

```bash
make showcase-traffic   # send another round through the gateway
make down                # stop everything, keep the data
make destroy             # stop everything and delete the data
```

---

## When something goes wrong

**The model pull fails or hangs.** The registry may be unreachable from your network. Everything
except the real-model parts still works — the mock upstream serves `mock-1`.

```bash
docker compose -f deploy/compose/docker-compose.yml --profile demo logs ollama-pull
```

**A container never becomes healthy.** Ask it why:

```bash
docker compose -f deploy/compose/docker-compose.yml ps
docker logs aira-gateway --tail 50
```

**Port already in use.** The stack uses 4200 (console), 8001 (gateway), 8002 (control plane), 8080
(Keycloak), 5432 (Postgres), 6379 (Redis), 8200 (Vault), 3000 (Grafana). Stop whatever holds the
port, or change the mapping in `deploy/compose/docker-compose.yml`.

**Signing in fails with a CORS error naming no setting.** Keycloak imports a realm only if it does
not already exist. If you edited the realm file after first start, recreate it:

```bash
docker compose -f deploy/compose/docker-compose.yml rm -sf keycloak
docker volume rm compose_keycloak-data
make up
```

---

## What this setup is not

It is **not** a deployment. It uses a development Keycloak realm with fixed passwords, a published
Postgres password, and Vault's dev-mode root token. The gateway refuses to start with any of those
outside a `local` environment, on purpose (`ADR-0015`).

For something you can actually put in front of people, read
[`integrated.md`](integrated.md).

---

Next: [Development](dev.md) · [Standalone](standalone.md) · [Integrated](integrated.md)
