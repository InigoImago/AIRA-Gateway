# `config/` — one file per installation

What each external system must provide is [`../docs/INTEGRATIONS.md`](../docs/INTEGRATIONS.md).
What every variable does and what breaks without it is
[`../docs/CONFIGURATION.md`](../docs/CONFIGURATION.md). **This directory is what you edit.**

```bash
cp config/integrated.example.yaml config/my-installation.yaml     # or the example nearest yours
$EDITOR config/my-installation.yaml
make config-check CONFIG=config/my-installation.yaml               # would both planes start?
uv run python tools/config_render.py config/my-installation.yaml -o deploy/compose/.env
make config-verify                                                 # does the stack run it?
```

## Three examples, one per shape

| | |
| --- | --- |
| [`showcase.example.yaml`](showcase.example.yaml) | a demo-grade installation on the bundled stack — `environment: local`, `demo_mode` on, well-known accounts. `make showcase` does not read it: the demo runs on `deploy/compose/.env`, made from `deploy/compose/.env.example`, and the two differ in places — the largest request body, the longest key lifetime, how long request rows are kept, CORS for the console's origin among them — so rendering it changes the demo |
| [`standalone.example.yaml`](standalone.example.yaml) | everything in containers on one machine, without the demo's traffic. Still `environment: local`, because the bundled stack cannot pass the checks any other value applies; its header says what to replace, in order, to leave that behind |
| [`integrated.example.yaml`](integrated.example.yaml) | your infrastructure — external Keycloak, PostgreSQL, Kafka, Redis and Vault, `environment: production`, governance enforced, TLS in front. The one for a deployment ([`docs/deployment/integrated.md`](../docs/deployment/integrated.md)) |

The same keys with different answers. Reading them side by side is the fastest way to see which
settings are *decisions* and which are addresses. Copy one before editing it: the examples are
tracked, and the test suite holds them to the settings classes.

## Nothing else here is tracked

`.gitignore` in this directory ignores `*` and names the exceptions. A new file is invisible to git
until somebody adds it to that list — the safe direction, because an installation file holds
hostnames, project ids and account names. None of that is a secret in the sense Vault means, and
none of it is anybody else's business.

## No secrets, and that is enforced

A value that authenticates belongs in **HashiCorp Vault**. The examples name *where*
each one comes from and never what it is, and `tools/config_render.py` **refuses** a key it
recognises as a credential rather than asking you not to write one:

```
$ uv run python tools/config_render.py config/mine.yaml
error: 'postgres.password' would set AIRA_POSTGRES_PASSWORD, which is a credential.
       It belongs in HashiCorp Vault — see the `secrets:` section of the examples.
```

It refuses every credential setting of both planes, `VAULT_SECRET_ID` and `VAULT_TOKEN`, and any
credential-shaped name (`…_PASSWORD`, `…_SECRET`, `…_API_KEY`, `…_TOKEN` and the like) that no
setting declares. Anything else renders, including a key that is no setting at all;
`make config-verify` then reports it as a variable no service receives. The one credential Vault cannot supply is the one that authenticates *to* Vault: the
AppRole's secret-id, as a file the containers can read (`vault.secret_id_file` names its path, and
the Compose files do not mount it — `docs/deployment/integrated.md` §4), or as `VAULT_SECRET_ID`.

## The shape

Two levels. A section is a prefix, a key completes it — `postgres.host` is `AIRA_POSTGRES_HOST`.
Three sections are special:

- **`core:`** — keys used unprefixed, for settings that belong to no system (`AIRA_CURRENCY`).
- **`vault:`** — emitted as `VAULT_*`, because those are read *before* any settings object exists.
- **`secrets:`** — prose. A list of what the file deliberately does not hold, ignored by the
  renderer.

Not every key is a setting of either plane: the console's `oidc.client_id`, `csp`, `management`,
`gateway` and `dns` sections are read by the console's container, and `otel`'s `backend_*` and
`forward_*` keys by the collector.

## Before it is deployed: would it start?

```bash
make config-check CONFIG=config/my-installation.yaml
```

It renders the file and hands it to both planes' own start-up checks (`unsafe_settings`) and their
settings classes, in a subprocess holding nothing but that environment. A line marked `!` is the
file's problem (exit 1), one marked `·` a credential the file rightly leaves to Vault; exit 2 is a
file that does not render, exit 3 a declared Vault this machine cannot use — which it can use only
when the file names `vault.secret_id_file` and that path is readable here, and
`uv run python tools/config_check.py <file> --without-vault` checks the rest. What the gateway
checks only while it starts — every configured model's region, the Vertex specs and credentials, the
Foundry deployments, the CORS rule — is not part of it ([`docs/DEPLOYMENT.md`](../docs/DEPLOYMENT.md)
§0 Step 0).

## It sits above the deployment, and that is checked

The chain is `config/*.yaml` → `tools/config_render.py` → `deploy/compose/.env` → Compose's
`${VAR}` interpolation → the container. Precedence, highest first:

| | |
| --- | --- |
| 1 | **HashiCorp Vault**, for every key it holds — ranked above the environment on purpose; a key it does not hold falls through to the ranks below |
| 2 | **this file** |
| 3 | a Compose default, `${VAR:-…}`, when the file does not set the variable |
| 4 | the settings class's own default |

Rank 2 is not true by itself. Compose fills every gap it is given, so a value left empty, a variable
the file does not name, a `.env` edited after rendering, or a source edited without re-rendering
all end the same way — the deployment runs on something nobody chose, and **nothing says so**.

So the rendered file is stamped with its source and a hash of it, and there is a command that
compares all four:

```bash
make config-verify        # or: uv run python tools/config_render.py --verify deploy/compose/.env
```

It reports, and exits non-zero on, each of: a source that changed since the render, a rendered file
edited by hand, a variable the file sets that no service receives, and a variable where a compose
default would take over. Every `make up*` target runs it before starting — **and starts anyway**,
because a stack somebody configured by hand has no file to check against. The message is the point,
not the exit code: read it.

Rendering also drops `deploy/compose/.aira-config-source` — one path, never a secret, ignored by
git. It answers the question the stamp cannot, because the stamp leaves with the file that carried
it: *was this deployment ever config-driven?* Replace a rendered `.env` with a hand-written one and
what remains looks exactly like the demo path, which has no config file and needs none. Without the
marker the check has one rule for both cases, and either it warns on every demo start-up — the
surest way to be ignored on the day it is right — or it stays quiet through the takeover it exists
to catch. With it, the demo says *nothing above me claims otherwise* and the integration says
*config/showcase.example.yaml no longer decides anything*.

Two variables the deployment is allowed to decide, each named in `COMPOSE_DECIDES` with the reason:
`AIRA_OIDC_JWKS_URI`, where empty means *the in-network address* on the bundled stack (the external
overlay refuses it empty, so an integrated deployment sets it), and `AIRA_POSTGRES_DB`, because
Management's second database on the same server has no setting of its own to name it with.

## It cannot drift

`tools/tests/test_the_config_examples_are_real.py` checks the examples against the settings classes
themselves, in both directions and at four levels:

1. every key an example renders is a field the product declares — a misspelling is a setting
   somebody believes they configured;
2. every field the product declares is named by an example, or listed as deliberately absent with
   the reason, in that file's `NOT_IN_A_CONFIG_FILE` — this is the one that notices a **new
   external system**;
3. no example carries a credential, checked from outside the renderer, because these files exist to
   be edited by hand;
4. and the rendered environment is fed to both planes' settings objects with the environment
   otherwise **empty**, so the file alone has to describe a configuration the settings classes
   accept. That is their validators, not every start-up check: `make config-check` asks those, and
   the gateway's upstream registration is checked only when it starts.
