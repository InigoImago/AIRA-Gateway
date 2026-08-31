# Deployment 4 of 4: Integrated

**For:** running AIRA on your organisation's infrastructure, with your Keycloak, your database and
your model platform.
**You need:** the accesses listed in §1, and somebody who can grant them.
**Shape:** you supply the infrastructure; AIRA supplies two application images and a console.

This page is the order to do things in. What each connected system must provide in detail is in
[`../INTEGRATIONS.md`](../INTEGRATIONS.md), and every configuration value is in
[`../CONFIGURATION.md`](../CONFIGURATION.md). This page tells you *when* to read them.

Work through it in order. Each step can be verified before the next one, which matters: an
integration that is wrong in step 2 and discovered in step 9 costs a day.

---

## 1. What you must be given, and by whom

Ask for these before you start. The middle column is what to ask for; the right column is why, so
you can answer "what do you need that for?".

| From | Ask for | Why |
|---|---|---|
| **Database team** | A PostgreSQL 16+ server, **two** databases (`aira_gateway`, `aira_mgmt`), one user per database, owner rights on its own | The two planes are separately deployable and must not share a schema |
| **Identity team** | A Keycloak realm, permission to create **5 realm roles**, **2 clients** (console: public + PKCE; services: confidential), and a **`groups` mapper on every client** | Roles are the only source of authorisation; a token without `groups` can match no group grant |
| **Identity team** | The realm's issuer URL, the JWKS URL, and the audience your tokens carry | The gateway validates every token itself and requires `exp`, `iat`, `sub` and, outside `local`, an audience |
| **Platform team** | Kafka with permission to **create 8 compacted topics** (or have them created for you) | Configuration travels as events; auto-creation is off, and a missing topic fails silently |
| **Platform team** | A Redis instance | Shared counters for rate limits and budget reservations across replicas |
| **Security team** | A Vault (or compatible) with a KV-v2 mount, permission to create a **read-only policy** and an **AppRole** | Every credential lives there; the process fails to start rather than falling back |
| **Cloud team** | A service account or key for your model platform, and the **regions** you are permitted to use | Residency is enforced at startup, not intended |
| **Network** | Ingress and TLS for two hostnames (console, gateway), and egress to your model platform | The console and the API are separate origins |

You do **not** need cluster-admin anywhere. AIRA never writes to your directory, never creates
users, and never changes a Keycloak group.

---

## 2. Keycloak

Read [`../INTEGRATIONS.md` §2](../INTEGRATIONS.md#2-keycloak-or-another-oidc-provider) and do
exactly what it says. The three things people get wrong:

1. **The five realm roles must exist with the exact names** — `global-admin`, `it-security`,
   `it-steuerung`, `use-case-admin`, `use-case-user`. A role that is spelled differently is simply
   not held. See [`../ROLES.md`](../ROLES.md).
2. **The `groups` mapper must be on every client that reaches AIRA**, including service accounts. A
   token with no `groups` claim grants nothing through a group, and nothing anywhere says so.
3. **Redirect URIs and web origins must be pinned** to your console hostname. `*` is refused.

Verify before moving on: obtain a token for a test user and check that the claims contain
`realm_access.roles`, `groups`, `exp`, `iat`, `sub` and your audience.

---

## 3. Databases, Kafka, Redis

Create the two databases and their users.

Create the topics — the names are in
[`../INTEGRATIONS.md` §3](../INTEGRATIONS.md#3-apache-kafka). All are **compacted**: they are a
current-state log, not a history, and compaction is what lets a gateway rebuild its read-model by
replaying from the beginning.

Point Redis at the gateway. Without it, rate limits fall back to **per-process** counters — not
open, but N replicas then allow N times the limit — and budgets fall back to the older racy path.
Both are reported by `/readyz` as `degraded: true`, which is a deliberate state and not a failure.

---

## 4. Vault: put the secrets in before anything reads them

This is the step that is easiest to skip and worst to skip. Do it now.

```bash
# Against your Vault, with a token that may write policies:
export VAULT_ADDR=https://vault.example.internal
export VAULT_TOKEN=<a token that may create policies and AppRoles>

uv run python tools/vault_setup.py --mount secret --path aira
```

It creates the KV-v2 path, a **read-only policy on exactly that path**, and an AppRole; it prints
the role id and writes the secret-id to a file with mode `0600`. It never prints a secret value,
including the one it generates.

Put the credentials in:

```bash
export AIRA_GOOGLE_API_KEY=...            # or the platform key you use
export AIRA_POSTGRES_PASSWORD=...
export AIRA_SECRET_KEY=...                # Django's; generate a long random value
uv run python tools/vault_setup.py --from-env
```

Then configure the applications with the **address and role id** (these identify; they do not
authorise) and mount the secret-id **file** into the containers:

```
VAULT_ADDR=https://vault.example.internal
VAULT_MOUNT=secret
VAULT_PATH=aira
VAULT_ROLE_ID=<printed above>
VAULT_SECRET_ID_FILE=/run/secrets/vault-secret-id
```

**Do not put the secret-id in an environment variable.** A value there is in `docker inspect`, in
`/proc/<pid>/environ`, and in any library that dumps its configuration when it panics.

**Then remove the plaintext `AIRA_*` secrets from your environment files.** Leaving both means the
environment wins nothing and proves nothing.

Verify:

```bash
curl -s https://gateway.example.com/readyz -H "Authorization: Bearer $TOKEN" | jq .secrets
```

It must say `"source": "vault"` and list the key **names**. If it says `"source": "environment"`,
Vault is not configured and every credential is coming from your environment files — which is
exactly the state this step exists to leave behind.

---

## 5. Configure the two applications

Every variable is in [`../CONFIGURATION.md`](../CONFIGURATION.md). The ones that decide whether the
process starts at all:

| Variable | Set it to | If you get it wrong |
|---|---|---|
| `AIRA_ENVIRONMENT` | anything but `local` | With a non-local value, the gateway refuses to start on any unsafe default, naming every reason at once |
| `AIRA_OIDC_ISSUER`, `AIRA_OIDC_JWKS_URI`, `AIRA_OIDC_AUDIENCE` | your realm's values | An audience is **required** outside `local` |
| `AIRA_ALLOWED_REGIONS` | the regions you are permitted to use | A model outside them stops the process at startup |
| `AIRA_TRUST_FORWARDED_FOR` | `true` **only** behind a proxy you control | Otherwise a caller can dictate the address in your audit trail |
| `AIRA_MAX_REQUEST_BYTES` | your body ceiling | The default is conservative; documents raise it |
| `AIRA_STORE_PAYLOADS` | your policy | A kill switch above the per-use-case setting |

The control plane refuses to boot outside `local` with a development `SECRET_KEY`, `DEBUG=true`, or
`ALLOWED_HOSTS=*`. That is intentional and is not configurable.

### One file instead of forty variables

The table above is what a deployment must get right. Setting them one at a time is how a deployment
comes to run on a value nobody chose, so there is a file:
[`config/integrated.example.yaml`](../../config/integrated.example.yaml) — every setting under a
section, with the comments that say what each one refuses.

```bash
make config-check CONFIG=config/integrated.example.yaml     # would both planes start?
uv run python tools/config_render.py config/integrated.example.yaml -o deploy/compose/.env
make config-verify                                          # is that what is actually running?
```

**The file ranks above the deployment, and `config-verify` is what makes that a fact rather than an
intention.** Compose fills every gap from `${VAR:-default}`, so a value left empty or a `.env`
edited afterwards runs on a default silently.

**Credentials are refused, not requested.** `config_render.py` raises on a password or an API key:
they come from Vault (§4). And the infrastructure containers' own variables — `POSTGRES_PASSWORD`,
`KEYCLOAK_ADMIN_PASSWORD`, `KC_DB_PASSWORD` — are not AIRA settings and are not in the file. Their
Compose defaults are **development values**; a deployment that leaves them unset is running on
`aira-local` and `admin`. That is what §10 is for.

---

## 6. Ingress and TLS

Two hostnames, because the console and the API are separate origins:

| Hostname | Points at | Notes |
|---|---|---|
| `aira.example.com` | the console container | It also proxies `/api` to the control plane and `/gw` to the gateway |
| `gateway.example.com` | the gateway | This is what callers use |

The console takes its issuer and its content policy **at deployment time** — see
[`../INTEGRATIONS.md` §7](../INTEGRATIONS.md#7-the-spa-is-configured-at-deployment-time). Changing
them means replacing `runtime-config.js` and setting `AIRA_CSP_CONNECT_SRC`, not rebuilding the
image.

---

## 7. Migrate, then start

```bash
# control plane
python manage.py migrate --noinput
# gateway
alembic upgrade head
```

Start the six processes: gateway, control plane, console, relay, consumer, retention.

**Schedule the retention process** — hourly is right. It is what deletes expired prompts, and if it
does not run, nothing is deleted and your retention policy is a sentence in a document.

---

## 8. The first administrator

AIRA has no local user store. The first administrator is whoever holds `global-admin` in your realm;
signing in creates their record automatically.

Then, in the console:

1. **Models & prices** — catalogue every model you intend to use, price it, declare what it can do,
   press **Check reachability**, and **approve** it. Nothing is callable until this is done: an
   uncatalogued model is refused by name, and so is a catalogued one nobody approved.
2. **Use cases** — create the first one, grant a group access to it, and issue a key.

---

## 9. Verify, in this order

```bash
# 1. The gateway is up and says where its secrets came from
curl -s https://gateway.example.com/readyz -H "Authorization: Bearer $TOKEN" | jq

# 2. A token from your realm is accepted
curl -s https://gateway.example.com/v1beta/models -H "Authorization: Bearer $TOKEN"

# 3. A real request is served and recorded
curl -s https://gateway.example.com/v1beta/models/<model>:generateContent \
  -H "x-goog-api-key: aira_..." -H "content-type: application/json" \
  -d '{"contents":[{"parts":[{"text":"Say OK"}]}]}'
```

Then open the console's **Requests** view and confirm the third call is there, with its tokens, its
cost and its use case. If it is not, the request never reached the gateway or the audit writer could
not write — both are visible in the gateway's log.

---

## 10. Before you call it production

- [ ] `/readyz` reports `"source": "vault"` and no plaintext credential remains in any env file
- [ ] `AIRA_ENVIRONMENT` is not `local`, and the process started anyway
- [ ] The retention job is scheduled and has run once
- [ ] Every model in the catalog is approved deliberately, not by the migration that approved
      pre-existing rows
- [ ] `AIRA_TRUST_FORWARDED_FOR` matches reality: on behind your proxy, off otherwise
- [ ] Someone other than you can sign in and sees what their role should see
      ([`../ROLES.md`](../ROLES.md))
- [ ] A budget and a rate limit exist on at least one use case, and you have watched one refuse
- [ ] Backups cover both databases; the gateway's holds your audit trail

---

## Known gaps at this stage of the product

Stated here rather than discovered later. See [`../GAP-ANALYSIS.md`](../GAP-ANALYSIS.md) for the
full list.

- **No Kubernetes or Helm charts yet.** Compose is what exists; the images are ordinary containers.
- **No alert delivery.** Findings appear in the console; nothing sends mail or calls a webhook.
- **PII is not redacted** inside stored payloads, deliberately — names and customer numbers are what
  the payload is stored *for*. The control is the per-use-case storage switch, and it is per use
  case for exactly this reason.
- **No load or performance testing has been done.**

---

Next: [Showcase](showcase.md) · [Development](dev.md) · [Standalone](standalone.md)
