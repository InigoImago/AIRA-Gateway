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

### Or make a use case of your own

Reusing `kundenservice` works and is the quickest thing, but a use case you create yourself is the
better test — it exercises the console, and it shows that **membership added in the console is
enough**: the gateway resolves a person by name from the read-model, so nobody has to be put into a
Keycloak group named after the use case (`FRD-209` §2.1).

As `admin` or `ucadmin`:

1. **Use cases → New use case.** A slug and a name; the slug is what appears in `/uc/<slug>`.
2. **Overview → Models this use case may call.** Release at least one — `qwen3:0.6b` is the local
   model the showcase pulled. A use case with nothing released refuses every request by name, which
   is the rule rather than a bug.
3. **Members.** Add `ucadmin` as an administrator and `ucuser` as a user. If your client
   authenticates as a machine (below), add its service-account user here too — the username is
   `service-account-<clientId>`.
4. **Keys**, if the client will use an API key: issue one and copy it. Shown once.
5. Optionally give it a **Budget** and a **Rate limit** small enough to watch them bite, and a
   **Pipeline** with an injection filter — that is what makes the traffic interesting afterwards.

Nothing else is needed. Members reach the new use case immediately (the membership travels to the
gateway over Kafka, which takes a moment on a busy machine — a first call that says *"not a member"*
is worth retrying once).

### With its existing Keycloak identity

If the point is to test the client's *own* login rather than a key. Keycloak is at
`http://localhost:8080`, sign in with **`admin` / `admin`**, and switch the realm selector at the
top left from `master` to **`aira`** — everything below is in that realm, and doing it in `master`
is the commonest way to spend twenty minutes on a client that AIRA will never see.

#### Making the client, screen by screen

**Clients → Create client.**

1. *General settings*: **Client type** `OpenID Connect`; **Client ID** whatever your client calls
   itself, e.g. `my-chatbot`. → **Next**
2. *Capability config* — this is the screen that decides everything:
   - **Client authentication**: **On**. This is what makes the client *confidential*, and it is
     what produces a client secret at all. Left off, the client is public and there is no secret to
     copy — the usual reason people cannot find one.
   - **Authentication flow**: tick **Service accounts roles** for a bot with nobody at the keyboard
     (that is the client-credentials grant). Tick **Standard flow** instead for a client with a
     human in front of it. **Direct access grants** is the username-and-password flow and is
     **switched off in this realm on purpose** (`ADR-0007`) — a client that only knows that flow
     cannot be used here.
   - → **Next**
3. *Login settings*: a service-account client needs none of it — leave the redirect URIs empty. A
   standard-flow client needs **Valid redirect URIs** and **Web origins** matching where it runs.
   → **Save**

**The secret: Credentials tab → Client Secret → copy.** The tab only exists when client
authentication is on. *Regenerate* replaces it and invalidates the old one immediately.

**The use case — the step people miss.** A machine client's groups and memberships belong to its
**service-account user**, not to the client. Putting the *client* somewhere does nothing.

- In Keycloak: **Service accounts roles** tab → the link to the service account user → **Groups** →
  **Join Group** → `/use-cases/<slug>`, or any group you have granted to a use case in the console.
- Or, simpler and with no Keycloak work at all: add **`service-account-<clientId>`** as a member of
  the use case in the AIRA console, exactly as you would add a person.

#### Getting a token and calling with it

```bash
TOKEN=$(curl -s -X POST \
  http://localhost:8080/realms/aira/protocol/openid-connect/token \
  -d grant_type=client_credentials \
  -d client_id=my-chatbot -d client_secret=… | jq -r .access_token)

curl -s -X POST http://localhost:8001/uc/<slug>/kira/api/external/chat \
  -H "Authorization: Bearer $TOKEN" -H "content-type: application/json" \
  -d '{"model_id": 9001, "request": {"parts": [{"text": "say hi"}]}}'
```

`model_id` is the numeric id from `GET /kira/api/external/models`, which is the KIRA contract —
that surface identifies models by id, not by name.

**Fetch the token from `localhost`, not `127.0.0.1`.** The issuer is baked into the token, the
gateway is configured for `http://localhost:8080/realms/aira`, and a token minted through any other
hostname is rejected with `401 INVALID_TOKEN` — which reads exactly like a wrong secret and is not.

#### If it is refused

The message names the control, and each of these means something different:

| Answer | Means |
|---|---|
| `401 INVALID_TOKEN` | the token is not valid *for this gateway* — usually the issuer above, or an expired token |
| `403 STANDARD_USER_PERMISSION_REQUIRED`, *"cannot be attributed to a use case"* | authenticated fine; you named no use case. Use `/uc/<slug>` or the `X-AIRA-Use-Case` header |
| `403 Not a member of use case …` | the use case is named but this caller does not reach it — the group or membership step above |
| `429` / over budget | it worked, and the demo's deliberately small limits did their job |

### Or turn authentication off entirely

For a laptop demo where credentials are just in the way, the whole of the above can be skipped.

**The file is `deploy/compose/.env`**, not the `.env` at the repository root — the containers read
the first and never see the second, which is the commonest reason a variable "does nothing".

```bash
echo 'AIRA_AUTH_REQUIRED=false' >> deploy/compose/.env
make up-full          # or: docker compose … up -d gateway
```

Every route is then served to anyone who can reach the port, with **no credential and no use
case**:

```bash
curl -s -X POST http://localhost:8001/v1beta/models/mock-1:generateContent \
  -H "content-type: application/json" \
  -d '{"contents":[{"role":"user","parts":[{"text":"hi"}]}]}'
```

The caller becomes `Principal(subject="demo", method="demo")`, and that method is an explicit
exemption from the use-case requirement — so `/uc/<slug>` is optional rather than required. Both
surfaces work this way, and so do the read-only routes (`GET /v1beta/models`,
`/kira/api/external/models`).

**What you give up depends on whether the caller names a use case**, and the difference is larger
than it looks. Both were measured against the running stack rather than reasoned about.

| | `/uc/<slug>/…` — a use case is named | no use case named |
|---|---|---|
| Attribution | the row carries `use_case`, `auth_method=demo`, `subject=demo` | `use_case` is `NULL` |
| Client IP | recorded in `source_ip` either way | recorded either way |
| Model release (`FRD-308`) | **still enforced** — a model not released to that use case is refused by name | not consulted |
| Budgets, rate limits, pipeline | apply; they are keyed on the use case, which is present | nothing to key on |
| Per-person budgets and limits | meaningless — every caller is the same `demo` subject | meaningless |
| Model approval (`FRD-307`) and residency (`FRD-115`) | **always enforced** — properties of the installation, checked at every dispatch | **always enforced** |

So `auth_required=false` plus `/uc/<slug>` is closer to "anonymous access to a use case" than it
first appears: the use case's own governance is intact, and only the *person* is missing.

**But it is not enforceable, and that is the point.** With authentication off, a caller who simply
omits the `/uc/` prefix is served anyway — measured: `200`, `use_case = NULL`. Naming a use case is
then the caller's choice, so every control that hangs off one is optional to them. That is the
difference between this switch and a per-use-case anonymous flag, which would make anonymity a
property of *one* use case that the gateway requires rather than a global amnesty the caller may
decline.

**It is bounded to `AIRA_ENVIRONMENT=local`, and a demo does not unlock it.** With `auth_required`
off in any other environment the gateway **refuses to start**, saying so:

> `AIRA_AUTH_REQUIRED is off — every route is served to anyone who can reach the port. Leave it on
> outside local development.`

That refusal is at startup rather than per request, because a check that fires per request produces
a service that is up, passes its health probe, and answers wrongly. `AIRA_DEMO_MODE` waives a named
list of things (a published Postgres password, a missing OIDC audience); **authentication is
deliberately not on that list** — one environment variable that turned into an open port serving
models is the reason the list exists at all. So this is a laptop switch, not a way to expose a demo
to a network.

### Can it call real Gemini models?

Yes, and three things have to line up. Two of them are refusals on purpose, so it is worth knowing
which one you are looking at.

1. **A key, in the right file.** `AIRA_GOOGLE_API_KEY=…` in `deploy/compose/.env`. The adapter is
   registered only when a key is present.
2. **Residency has to be widened, or the gateway will not start.** Google AI Studio's endpoint
   names no region and guarantees none, so it is treated as `global` — and `AIRA_ALLOWED_REGIONS`
   defaults to the EU regions of every supported cloud, on the principle that a residency
   constraint which must be switched on is one that will be found switched off. Either
   `AIRA_ALLOWED_REGIONS=global,…` deliberately, or use **Vertex AI in an EU region**
   (`AIRA_VERTEX_PROJECT` + credentials), which needs no widening and is the reason the two
   adapters exist separately.
3. **The model must be catalogued and approved.** Approval is an installation property and is
   checked at **every** dispatch — it still applies with authentication off. `AIRA_GEMINI_MODELS`
   is empty by default, deliberately: it used to name two models a key issued today cannot use, and
   a default naming something unusable produces a 404 that reads as our fault. Use the catalog
   screen's discovery to list what your key actually serves, then approve what you want.

#### The whole thing, once, as it was actually run

Walked end to end against a live key rather than described. Four steps, and the two surprises are
worth more than the happy path.

```bash
# 1. the gateway: a key, a widened residency, a model list, auth off
AIRA_GOOGLE_API_KEY=…  AIRA_ALLOWED_REGIONS=global,eu,europe-west1,…  \
AIRA_GEMINI_MODELS=gemini-flash-latest  AIRA_AUTH_REQUIRED=false      make up-full

# 2. catalogue and approve it, with the integer alias a KIRA client sends
POST /api/v1/models/  {"name": "gemini-flash-latest", "provider": "google",
                       "region": "global", "numeric_id": 9102, "approved": true}

# 3. a use case, with that model released to it
POST  /api/v1/use-cases/          {"slug": "anon-demo", "name": "Anonymous demo"}
PATCH /api/v1/use-cases/anon-demo/ {"allowed_models": ["gemini-flash-latest"]}

# 4. the request — no Authorization header anywhere
curl -X POST http://localhost:8001/uc/anon-demo/kira/api/external/chat \
  -H "content-type: application/json" \
  -d '{"model_id": 9102, "request": {"parts": [{"text": "say hi"}]}}'
```

```json
{"parts":[{"text":"anonymous gemini works"}],"usage_data":{"token_input":9,"token_output":4}}
```

and the row it wrote:

```
subject=demo  username=  auth_method=demo  use_case=anon-demo
model=gemini-flash-latest  api=kira  status=200  outcome=served  source_ip=172.19.0.1
credential=  cost_nanos=
```

**The id is `numeric_id`, which you choose.** KIRA identifies models by integer, not by name, and
that integer is a column on the catalog entry — not the catalog's primary key, and not anything
Google knows about. `GET /kira/api/external/models` lists what this installation serves with the
ids to use. It is unique across the catalog: two entries claiming one id make the surface answer
`503` rather than guess which model to bill.

**Leave it out and one is assigned** (from `9500` upwards, above everything this repository seeds),
so a model catalogued without a thought about KIRA is still reachable there. Set it when clients
already send a particular number — that is what keeps an installation migrating from the
predecessor working unchanged. The console's *Models* screen offers the same field, labelled **KIRA
id**; before it did, every model added from the console got none and was invisible to this surface.

**Two things went wrong on the way, and both were the product being right.**

*The model in the listing could not be called.* `gemini-2.5-flash` is in Google's own `/models`
response and answers `404 — no longer available to new users` on the first generate. That is
exactly why `AIRA_GEMINI_MODELS` ships empty: it once named two models a key issued today cannot
use, and a default that names something unusable produces a 404 that reads as AIRA's fault. Ask the
endpoint what your key actually serves.

*Cataloguing a model does not make the gateway serve it.* Those are two facts with two owners — the
catalog is the installation's declaration, `AIRA_GEMINI_MODELS` is which models the adapter offers.
With the model catalogued but not in the adapter's list, the surface answered
`MODEL_NOT_FOUND: Model 'gemini-flash-latest' not found.`

And the release check is real even here: asking `anon-demo` for a model it has not been released
answers *"'qwen3:0.6b' has not been released to use case 'anon-demo'. An administrator of the use
case can add it; it currently has 1 model(s)."*

`cost_nanos` is empty above because the catalog entry carries no price — unpriced traffic is
counted apart rather than as zero (`FRD-403`). Set the prices if you want the money figures to mean
something.

Before doing any of this where others can reach it: an open port, one shared `demo` identity, and a
real bill.

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
