# Deployment 1 of 4: Showcase

**For:** seeing the whole product work, with real traffic, in about fifteen minutes.
**You need:** Docker with Compose, `make`, `python3`, [`uv`](https://docs.astral.sh/uv/) (it
fetches the Python 3.14 the helper scripts run on) and `curl`; about 20 GB of free disk; no
accounts anywhere.
**You do not need:** a cloud provider, an API key, Node, or a Keycloak of your own.

This is the only setup that pulls a real model and sends real requests through it. Everything else
on this page is a consequence of that: the figures you see afterwards are measurements, not
fixtures.

---

## What you will have when this finishes

- The whole stack in containers: gateway, control plane, console, Postgres, Keycloak, Kafka, Redis,
  Vault, an OpenTelemetry collector and Grafana — and a schema registry that nothing uses.
- Two small models running locally in Ollama: `qwen3:0.6b` for chat and `all-minilm` for
  embeddings, about 600 MB together.
- Four use cases — `kundenservice`, `entwicklung`, `coding-assistant` (the one with tool calling
  on) and `personalwesen` — with budgets, anomaly rules, rate limits on `entwicklung` and
  `coding-assistant`, an injection filter on `kundenservice`, and an API key for each member: six
  keys. Beside them, `smoke-test`, IT Security's use case for putting the question catalogue to a
  model.
- Six accounts in the development realm: the five in Step 3, and `demo-user`.
- Real traffic already through the gateway, **including a prompt injection the filter refused** —
  so the security console has something true in it rather than rows somebody inserted — and a
  month of history behind it, so the usage chart has a shape.
- An OpenCode configuration for the coding assistant, `opencode.showcase.json`, in the repository
  root.
- Somewhere to point a client you already have, over either API surface and with either an API key
  or its own Keycloak identity (Step 5).

---

## Step 1: check the tools

```bash
docker version
docker compose version
python3 --version
uv --version
curl --version
command -v make
```

Each must print a version, and the last a path. If `docker version` says it cannot connect to the
daemon, start Docker Desktop (macOS, Windows) or `sudo systemctl start docker` (Linux) and try
again. `make` asks `python3` for every address the stack answers on, and the steps that wait for
the demo run under `uv`, so none of them is optional.

Check the disk, because a failed download halfway through is the most common way this goes wrong:

```bash
df -h .
```

You want about 20 GB free: the images come to roughly 18 GB — Ollama, the schema registry and
Grafana's bundle are most of it — and the two models to 600 MB.

---

## Step 2: start it

```bash
make showcase
```

This runs for a while and prints what it is doing. In order:

1. **Creates `deploy/compose/.env`** from `deploy/compose/.env.example`, if it does not exist.
2. **Builds the three application images and starts everything** — infrastructure, both planes,
   the console, Ollama and the model download — with the demo provisioning: the realm repair, the
   development Vault's secrets, the migrations, the Kafka topics and the seed (roles, users, use
   cases, budgets, limits, rules, the pipeline, keys and the local models' catalogue entries).
3. **Waits until the console, the gateway, Management and Keycloak answer** (`make wait-healthy`).
4. **Waits for the models** (`make demo-ready`): until the download has finished, then seeds once
   more against what was actually downloaded, then until the gateway accepts every demo key and
   serves every demo model. The download is a separate container on purpose, so that the model
   server's health check does not report "ready" while it is still running.
5. **Builds a month of history** — real requests whose timestamps are then moved into the past.
   Allowed to fail; the demo does not depend on it.
6. **Clears what earlier runs consumed**, so this run's figures are its own. Allowed to fail too.
7. **Drives traffic** through the gateway with the demo keys, and fails the whole command when
   nothing is served, when the injection is not refused by the filter, or when the embedding batch
   is not served — the requests the demo exists to show.
8. **Prints where to sign in**, writes `opencode.showcase.json`, and prints a request you can paste
   straight away.

`make showcase RELEASE=vX.Y.Z` does the same with the published images instead of building them;
`make current-version` prints the newest.

If it stops with an error, read the last twenty lines: every step names what it was doing.
`make showcase-doctor` walks the chain — the realm and its accounts, Management, the gateway's
read-model, the browser's login — and names the first link that is broken; it reads and changes
nothing.

---

## Step 3: sign in

Open <http://localhost:4200>.

| Sign in as | Password | Shows you |
|---|---|---|
| `admin` | `demo-password` | everything, including model prices and approval; the only one who may create a use case |
| `itsec` | `demo-password` | security oversight across every use case, and the content of requests |
| `itgov` | `demo-password` | every figure, no write anywhere, no content |
| `ucadmin` | `demo-password` | administering three of the four use cases — `personalwesen` is deliberately not theirs |
| `ucuser` | `demo-password` | a member of `kundenservice` and `coding-assistant` |

The first three hold a role through their Keycloak group; `ucadmin` and `ucuser` hold none and
reach use cases through grants. The differences are the point. See [`../ROLES.md`](../ROLES.md) for
what each may do and why.

Keycloak's own admin console, at <http://localhost:8080> with `admin` / `admin`, is a different
account in a different realm (`master`); the demo lives in the realm `aira`.

---

## Step 4: what to look at, in order

**Requests** (as `itsec`) — every request across every use case. Open a row: the metadata is on the
left, and the prompt and the answer are one click away. The panel tells you the read was recorded,
because it was.

**Reporting** (as `itgov`) — spend and usage. The figures come from the traffic Step 2 sent; nothing
here is seeded.

**Security** (as `itsec`) — findings, what is stopped, and the rules behind them. One rule throttles
rather than alerts, so you can see the difference.

**Use cases → kundenservice → Edit pipeline** (as `ucadmin`) — the filter that refused the
injection. Use the test panel to send the same prompt again and watch it be refused.

**Models & prices** (as `admin`) — the catalog. Open a row for everything on file about a model, and
press **Check reachability**: the local model answers, and a model this installation has no
credential for reports that it is declared and nothing serves it.

**The coding assistant** — `opencode.showcase.json` points OpenCode at the gateway with
`coding-assistant`'s key and the models it may call; [`tools/opencode/README.md`](../../tools/opencode/README.md)
has the rest. `make showcase-agent` writes the file again.

### Strict data mode, both ways

The showcase runs in **strict data mode**, as every installation does unless an operator switches
it off: nobody but the Global Administrator and IT Security learns what a named colleague did. The
mode is applied when data is read, so one command shows the same requests, figures and findings
without it — the comparison a works council is asked to judge.

```bash
make showcase-mode         # which mode deploy/compose/.env asks for, and which one is running
make showcase-strict-off   # each use case's own setting decides again; containers are recreated
make showcase-strict-on    # back to the default
```

Each switch writes one line of `deploy/compose/.env`, `AIRA_STRICT_DATA_MODE`, and recreates the
containers whose environment changed; the data stays. `kundenservice` stores the most open setting
there is — *every member reads everything* — so the difference is as large as it can be. Sign in as
`ucadmin`, who administers it, and open **Use cases → kundenservice**:

| Where | Strict data mode on | Off |
| --- | --- | --- |
| **Members** — *What each person used* | not shown — it is the Global Administrator's, in either mode | not shown either |
| **Requests** | `ucuser`'s rows read *another member*, without the API key; no button for the prompt | `ucuser` by name and key; the prompt opens |
| **Warnings** | a finding about a member — when a rule fired on one — names *a person* | names them |
| **API keys** | `ucuser`'s key belongs to *another member* | `ucuser` |
| **Data protection** | two choices: not stored, or Global Administrators and IT Security only | four |

`ucadmin`'s own rows, figures and key are shown in full either way, and so are the use case's
totals. Then:

- **as `itgov`** (IT Steuerung), **Reporting** — every total and every breakdown by use case and
  model; the breakdown by member shows nobody while the mode is on;
- **as `itsec`**, the same screens — every person, in both modes: the incident roles are what the
  mode leaves people to;
- **as `admin`**, the Members tab — *What each person used*, with what is left of each person's
  allowance: the one place it is shown;
- **as `admin`**, **Platform → Content reads** — every opening of a request's details and every
  read of its content, by whom, on what ground, with *What was opened* telling the two apart;
- **Grafana**, once telemetry is on (`AIRA_OTEL_ENABLED=true`, see
  [Telemetry](#telemetry-as-an-example-of-what-this-buys)) — a span's caller is a pseudonym such as
  `p-3f9a…` while the mode is on, and so is the person in every log line. `make showcase` draws a
  random `AIRA_PSEUDONYM_SECRET` into `deploy/compose/.env` once, so one person keeps one
  pseudonym across restarts; a real deployment keeps that key in Vault.

`make showcase-doctor` says which mode is running, and warns when the stack and `.env` disagree.

---

## Step 5: point an existing client at it

The showcase is a real gateway with a real Keycloak, so a client you already have — a
KIRA-compatible chatbot, an SDK, anything that speaks one of the two surfaces — can be aimed at it
without changing the client's code. Two ways in, and the first takes about a minute.

**The two base URLs.** Both are on the gateway, and both are also reachable through the console's
own proxy at `http://localhost:4200/gw/…` if that is easier to reach from where your client runs.

| Surface | Base URL | Verbs |
|---|---|---|
| KIRA compatibility | `http://localhost:8001/kira/api/external` | `POST /chat`, `POST /streaming-chat`, `POST /embed`, `POST /rerank`, `GET /models`, `GET /health`, `GET /version-info`, `GET /ki-usage` |
| Gemini dialect | `http://localhost:8001/v1beta` | `GET /models`, `POST /models/{model}:generateContent`, `:streamGenerateContent`, `:embedContent`, `:batchEmbedContents` |

### With an API key — the quickest test

`make showcase` prints a ready-made request with `kundenservice`'s key, and the console shows the
same thing on a use case's **Overview** tab — base URL, the models it may call, and a ready-made
example per surface. Credentials are read in one order: `Authorization: Bearer <token>` →
`x-goog-api-key` → `?key=`, so a client that can set *either* header works unchanged.

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
Keycloak group named after the use case.

As `admin` — creating a use case is the Global Administrator's — then as its administrator:

1. **Use cases → + New use case.** A slug and a name; the slug is what appears in `/uc/<slug>`.
2. **Overview → Models this use case may call.** Release at least one — `qwen3:0.6b` is the local
   model the showcase pulled. A use case with nothing released refuses every request by name, which
   is the rule rather than a bug.
3. **Members.** Add `ucadmin` as an administrator and `ucuser` as a user.
4. **API keys**, if the client will use an API key: issue one and copy it. Shown once.
5. Optionally give it a **Budget** and a **Rate limit** small enough to watch them bite, and a
   **pipeline** with an injection filter — that is what makes the traffic interesting afterwards.

Nothing else is needed. The use case, its releases and its members travel to the gateway over
Kafka, and the relay here runs every ten seconds: until the use case has arrived, a request for it
is refused `403` *"not known to this gateway"*, and a member added a moment ago may still read
*"not a member"*. Wait a few seconds and send it again.

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
     **switched off in this realm on purpose** — a client that only knows that flow cannot be used
     here.
   - → **Next**
3. *Login settings*: a service-account client needs none of it — leave the redirect URIs empty. A
   standard-flow client needs **Valid redirect URIs** and **Web origins** matching where it runs.
   → **Save**

**The secret: Credentials tab → Client Secret → copy.** The tab only exists when client
authentication is on. *Regenerate* replaces it and invalidates the old one immediately.

**The use case — the step people miss.** A machine client's groups belong to its **service-account
user**, not to the client. Putting the *client* somewhere does nothing.

- In Keycloak: **Service accounts roles** tab → the link to the service account user → **Groups** →
  **Join Group** → `/use-cases/<slug>`, or any group you have granted to a use case in the console.
  **This alone is not enough — see the mappers below.**
- Adding **`service-account-<clientId>`** as a member in the AIRA console works only if
  Management's directory search finds that account: a name is accepted when the directory confirms
  it, and refused with *"The directory knows no user …"* when it does not. Whether Keycloak's user
  search returns a client's service account has not been checked here, so the group and the two
  mappers are the route to rely on.

#### The mappers a new client does not have

**A group you joined is not a group in the token.** Keycloak puts group membership into a token
only when the client has a *group membership mapper*, and a client you create today has none. The
clients this realm ships with (`aira-gateway`, the integration-test clients) carry one, which is
why the demo works and a client of your own silently does not.

Measured in this realm, on a client created exactly as above, whose service account had been joined
to `/use-cases/entwicklung`:

```
aud                : account          ← not the gateway's
groups             : *** ABSENT ***   ← the join is invisible
preferred_username : service-account-probe-groups
```

The request that follows is authenticated and then refused with `403
STANDARD_USER_PERMISSION_REQUIRED` — *"cannot be attributed to a use case"* — which reads like a
permission problem and is a mapper problem.

**Clients → your client → Client scopes → `<clientId>-dedicated` → Add mapper → By configuration.**

1. **Group Membership** — *Token Claim Name* `groups`, **Full group path: On**, *Add to access
   token*: On. Full paths matter: the gateway reads `/use-cases/<slug>`, and a bare `entwicklung`
   resolves to nothing.
2. **Audience** — *Included Client Audience* `aira-gateway`, *Add to access token*: On. Needed as
   soon as `AIRA_OIDC_AUDIENCE` is set, which a deployment outside `local` must do: both planes
   refuse to start without one — the gateway unless it declares itself a demo
   (`AIRA_DEMO_MODE`) — because without an audience *any* token this realm minted would be accepted
   here, including one issued to an unrelated client.

The same client, after both mappers:

```
aud                : ['aira-gateway', 'account']
groups             : ['/use-cases/entwicklung']
```

Which is what the gateway reads: `usecases_from_group_paths(['/use-cases/entwicklung'])` →
`('entwicklung',)`.

**Roles come from group paths too**: the three built-in roles through
`AIRA_ROLE_GROUPS` — semicolon-separated, full paths, e.g. `it-security=/aira/it-security` — and a
role an installation defines through the group bound to it in the console. A caller in
`/use-cases/entwicklung` and nothing else holds no role, which is correct: use-case membership is
not oversight. Without the groups mapper there are no roles at all, so a permission such as
`report.read_all` does not reach even a caller in a group that confers it.

#### Getting a token and calling with it

```bash
TOKEN=$(curl -s -X POST \
  http://localhost:8080/realms/aira/protocol/openid-connect/token \
  -d grant_type=client_credentials \
  -d client_id=my-chatbot -d client_secret=… | jq -r .access_token)

curl -s -X POST http://localhost:8001/uc/<slug>/kira/api/external/chat \
  -H "Authorization: Bearer $TOKEN" -H "content-type: application/json" \
  -d '{"model_id": 1004, "request": {"parts": [{"text": "say hi"}]}}'
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
| `403 … not known to this gateway` | the use case's configuration has not reached the gateway yet — a new one, a few seconds after it was created — or Management has no such use case |
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
| Model release | **still enforced** — a model not released to that use case is refused by name | not consulted |
| Budgets, rate limits, pipeline | apply; they are keyed on the use case, which is present | nothing to key on |
| Per-person budgets and limits | meaningless — every caller is the same `demo` subject | meaningless |
| Model approval and residency | **always enforced** — properties of the installation, checked at every dispatch | **always enforced** |

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
   registered only when a key is present. This stack's Vault is a development one that is refilled
   from that same file on every start, so here the key lives in the file either way.
2. **Residency has to be widened, or the gateway will not start.** Google AI Studio's endpoint
   names no region and guarantees none, so it is treated as `global` — and `AIRA_ALLOWED_REGIONS`
   defaults to the EU regions of every supported cloud, on the principle that a residency
   constraint which must be switched on is one that will be found switched off. Either
   `AIRA_ALLOWED_REGIONS=global,…` deliberately, or use **Vertex AI in an EU region**
   (`AIRA_VERTEX_PROJECT` + credentials), which needs no widening and is the reason the two
   adapters exist separately.
3. **The model must be catalogued and approved.** Approval is an installation property and is
   checked at **every** dispatch — it still applies with authentication off. Catalogued under the
   provider `generative-language`, a model is served by AI Studio without being named in
   `AIRA_GEMINI_MODELS`, which is empty by default: the endpoint lists models a key
   issued today cannot use, and a default naming something unusable produces a 404 that reads as our
   fault. Use the catalog screen's discovery to list what your key actually serves, then approve
   what you want.

#### The whole thing, once

Four steps:

```bash
# 1. the gateway: a key, a widened residency, auth off
AIRA_GOOGLE_API_KEY=…  AIRA_ALLOWED_REGIONS=global,eu,europe-west1,…  \
AIRA_AUTH_REQUIRED=false  make up-full

# 2. catalogue and approve it, under AI Studio's provider name, with the integer a KIRA client sends
POST /api/v1/models/  {"name": "gemini-flash-latest", "provider": "generative-language",
                       "numeric_id": 9102, "approved": true}

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

and the row it writes:

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
id**.

**Three things that look like failures and are the product being right.**

*The model in the listing may not be callable.* `gemini-2.5-flash` has been in Google's own
`/models` response while answering `404 — no longer available to new users` on the first generate.
Ask the endpoint what your key actually serves.

*The provider name decides who serves it.* Catalogued under a provider no adapter owns, nothing
claims the model, and every request for it is refused as not found.

*The release check is real even here.* Asking `anon-demo` for a model it has not been released
answers *"'qwen3:0.6b' has not been released to use case 'anon-demo'. An administrator of the use
case can add it; it currently has 1 model(s)."*

`cost_nanos` is empty above because the catalog entry carries no price — unpriced traffic is
counted apart rather than as zero, and by default an unpriced model is refused outright under a
cost budget. Set the prices if you want the money figures to mean something.

Before doing any of this where others can reach it: an open port, one shared `demo` identity, and a
real bill.

### What to look at afterwards

The interesting part is not that it answered. Open the console and find the client's own traffic:
**Requests** shows which credential and which identity, the use case's **Overview** shows what it
spent under Consumption, and if the client sent something the injection filter dislikes, the
**Security** console has it as a refusal with the prompt attached. A client connected this way is
subject to every control on this page — the same budget, the same rate limit, the same pipeline —
which is the thing worth demonstrating.

If it is refused, the message says which control did it. `Not a member of use case …` is the group
question above; `over budget` and `rate limit exceeded` mean the demo's deliberately small limits
did their job.

---

## Step 6: more traffic, and stopping

```bash
make showcase-traffic   # send another round through the gateway
make showcase-history   # more history for the usage chart: DAYS=42 PER_DAY=6
make down                # stop everything, keep the data
make destroy             # stop everything and delete the data
```

`showcase-history` is the one target in this walkthrough that **moves a timestamp**. It drives real
requests — real tokens, real price, a real audit row — and then backdates the rows it just created,
so the usage chart has a month of columns instead of one. `make showcase` runs it once; this is how
to ask for a longer or busier history. What is real is every request and every figure on it — token
counts, latency, money; what is not is *when* each one happened, and how many fell on which day. It
moves only rows this run created for the demo's own use cases, and its output says what it did.

**`make destroy` deletes every volume** — the audit trail, the configuration, the realm and the
downloaded models — so the next `make showcase` starts from nothing.

---

## What is configurable here

The showcase runs on `deploy/compose/.env`, which `make showcase` copies from
`deploy/compose/.env.example` on first start. Everything this walkthrough shows can be changed
there — but the **file an integrator is meant to edit** is
[`config/showcase.example.yaml`](../../config/showcase.example.yaml): one YAML with every setting
under a section, comments explaining what each one refuses, and secrets deliberately absent. Its
values are not all the ones `.env.example` gives the demo — the largest request body, the longest
key lifetime and how long request rows are kept differ, among others — so rendering it changes the
demo.

Editing that file changes nothing on its own. It is **rendered** into the environment both planes
already read — copy it first, so the tracked example stays as it is:

```bash
cp config/showcase.example.yaml config/my-showcase.yaml
uv run python tools/config_render.py config/my-showcase.yaml -o deploy/compose/.env
make up-full            # or `make showcase`, to redo the walkthrough
```

And afterwards:

```bash
make config-verify      # does the deployment run what the file says?
```

`config-verify` is not an errand. Compose fills every gap it is given from `${VAR:-default}`, so a
value left empty, a variable the file does not name, or a `.env` edited by hand all end the same
way — the stack runs on something nobody chose, and nothing says so.

### What the file does not carry

Rendering emits the `AIRA_*` contract and the `VAULT_*` addresses, and nothing else. Two kinds of
value are therefore absent, and the difference between them matters:

- **Credentials, refused on purpose.** `config_render.py` raises rather than emitting a password or
  an API key: those belong in HashiCorp Vault, which is what the `secrets:` section of the example
  points at.
- **The infrastructure containers' own settings**, which are not AIRA settings at all —
  `POSTGRES_PASSWORD`, `KEYCLOAK_ADMIN`, `KC_DB_PASSWORD`, `VAULT_DEV_ROOT_TOKEN_ID`,
  `AIRA_BIND_HOST`, the `AIRA_PUBLISH_*` ports. Compose fills these from the defaults written into
  `docker-compose.yml` — `aira-local`, `admin`, `127.0.0.1` — which are **development values**, and
  which is exactly right for this walkthrough and exactly wrong anywhere else. Note that both
  planes' database password comes from `POSTGRES_PASSWORD` here, not from a variable of its own.

### Telemetry, as an example of what this buys

`config/showcase.example.yaml` already carries:

```yaml
otel:
  enabled: true
  endpoint: http://otel-collector:4318
```

`enabled` is the switch that decides whether the applications export at all — with it off, the
collector and Grafana run, receive nothing, and show an empty screen that looks exactly like a
broken one. `deploy/compose/.env.example` leaves it off. `endpoint` is where the **applications**
send, which is the collector. Where the collector sends *onward* is in the same section, commented
out until you want it: `backend_*` keys for the observability channel and `forward_*` keys for the
delivery channel — one record per request, for a SIEM — rendered as `AIRA_OTEL_BACKEND_*` and
`AIRA_OTEL_FORWARD_*`, which Compose hands to the collector rather than to the applications
([`CONFIGURATION.md` §5a](../CONFIGURATION.md)). A `sample_ratio` below 1 thins both channels: the
delivery channel's records are spans.

**Plain JSON to a second destination.** The delivery channel sends OTLP/JSON by default,
gzip-compressed. For a receiver that wants the JSON as it is, readable in a proxy log and parseable
without unwrapping, turn the compression off — in the file as `forward_config`, `forward_endpoint`,
`forward_encoding` and `forward_compression`, or directly in `.env`:

```bash
AIRA_OTEL_FORWARD_CONFIG=/etc/otelcol-contrib/forward.yaml
AIRA_OTEL_FORWARD_ENDPOINT=https://siem.internal:4318
AIRA_OTEL_FORWARD_ENCODING=json        # the default; `proto` is the only other value
AIRA_OTEL_FORWARD_COMPRESSION=none     # the default is gzip
```

**There is no encoding called `none`.** The encoding says *what* is sent, `json` or `proto`; the
compression says whether it is wrapped, `gzip`, `none`, `zstd` or `snappy`. `ENCODING=none` stops
the collector at validation, and with it Grafana. Plain JSON is still OTLP/JSON — one nested
document per batch, each attribute a `{"key": …, "value": {"stringValue": …}}` pair — and not one
flat event per request; for that shape in Splunk, the Splunk HEC recipe in
[`INTEGRATIONS.md` §6](../INTEGRATIONS.md) is the answer. `make otlp-inspector` shows what leaves,
byte for byte, before a real receiver exists.

---

## When something goes wrong

**First, ask the demo which link is broken.** `make showcase-doctor` reads the realm and its
accounts, Management, the gateway's read-model and the browser's login chain, names the first thing
that is wrong and what to do about it, and changes nothing. It runs on the host's `python3`, which
must be 3.14 for this one.

**The model download fails or hangs.** The registry may be unreachable from your network. The
download is retried three times; after that `make showcase` waits in `make demo-ready` for up to
fifteen minutes (`AIRA_DEMO_WAIT_SECONDS`, 900 by default) and then stops with an error, before any
traffic is sent. The stack itself stays up.

```bash
docker compose -f deploy/compose/docker-compose.yml --profile demo logs ollama-pull
```

**A container never becomes healthy.** Ask it why:

```bash
make ps
docker logs aira-gateway --tail 50
```

**Port already in use.** The stack publishes 4200 (console), 8001 (gateway), 8002 (control plane),
8080 and 9000 (Keycloak and its health port), 5432 (Postgres), 6379 (Redis), 29092 (Kafka), 8081
(schema registry), 8200 (Vault), 4317, 4318 and 8889 (the collector), 3000 (Grafana) and 11434
(Ollama), all on `127.0.0.1`. Every one moves with a variable in `deploy/compose/.env` —
`AIRA_PUBLISH_FRONTEND_PORT`, `AIRA_PUBLISH_GATEWAY_PORT`, `AIRA_PUBLISH_MANAGEMENT_PORT`,
`AIRA_PUBLISH_KEYCLOAK_PORT`, `AIRA_PUBLISH_KEYCLOAK_HEALTH_PORT`, `AIRA_PUBLISH_POSTGRES_PORT`,
`AIRA_PUBLISH_REDIS_PORT`, `AIRA_PUBLISH_KAFKA_PORT`, `AIRA_PUBLISH_SCHEMA_REGISTRY_PORT`,
`AIRA_PUBLISH_VAULT_PORT`, `AIRA_PUBLISH_OTLP_GRPC_PORT`, `AIRA_PUBLISH_OTLP_HTTP_PORT`,
`AIRA_PUBLISH_OTLP_METRICS_PORT`, `AIRA_PUBLISH_GRAFANA_PORT`, `AIRA_PUBLISH_OLLAMA_PORT` — and the
Makefile and the tools follow it. A forwarder in front that shows the console on another port
(`sbx ports`, a VM, an SSH tunnel) is named with `AIRA_CONSOLE_PORT`, and reaching the stack from
another machine needs `AIRA_BIND_HOST=0.0.0.0` (and `AIRA_BIND_HOST6=::` for IPv6) — which puts a
stack whose passwords and Vault token are in this repository on the network, so only on one you
trust.

**Signing in fails after moving the console's port, or after editing the realm file.** Keycloak
imports `aira-realm.json` only when the realm does not exist, and fills in the console's port at
that import. `keycloak-init`, which `make showcase` and `make up-full` run on every start, imports
it again when a user, group, membership or client the file names is missing — and only then. For
any other change, a moved port included, delete the realm `aira` in Keycloak's admin console
(<http://localhost:8080>, `admin` / `admin`, *Realm settings → Action → Delete*) and run
`make up-full`; `keycloak-init` imports it from the file. Do not delete a volume for this: the realm
lives in the `keycloak` database, in the same Postgres volume as the audit trail and every
configuration.

**The demo's keys are refused after a use case was deleted and recreated.** Revocation is final at
the gateway. `make showcase-reset-keys` removes the demo keys of the four showcase use cases from
the gateway's read-model and seeds them again — demo only.

---

## What this setup is not

It is **not** a deployment. It runs with `AIRA_ENVIRONMENT=local`, which switches every start-up
check off; its realm has fixed passwords, its Postgres password is published, and its Vault runs in
dev mode with the root token `root`. Outside `local` the gateway refuses the published Postgres
password, a missing audience, `PLAINTEXT` Kafka and plain-HTTP identity and Vault addresses — unless
`AIRA_DEMO_MODE` is on, which waives exactly those — and Management refuses its own development
defaults with no waiver at all. Neither plane can see the realm's passwords or Vault's
root token: those belong to the infrastructure, and nothing checks them.

For something you can actually put in front of people, read
[`integrated.md`](integrated.md).

---

Next: [Development](dev.md) · [Standalone](standalone.md) · [Integrated](integrated.md)
