# `config/` — one file per installation

What each external system must provide is [`../docs/INTEGRATIONS.md`](../docs/INTEGRATIONS.md).
What every variable does and what breaks without it is
[`../docs/CONFIGURATION.md`](../docs/CONFIGURATION.md). **This directory is what you edit.**

```bash
cp config/standalone.example.yaml config/my-installation.yaml
$EDITOR config/my-installation.yaml
uv run python tools/config_render.py config/my-installation.yaml -o deploy/compose/.env
```

## Two examples, and why two

| | |
| --- | --- |
| [`showcase.example.yaml`](showcase.example.yaml) | the demo this repository starts — local everything, `demo_mode` on, well-known accounts |
| [`standalone.example.yaml`](standalone.example.yaml) | one machine, no demo — external Keycloak, PostgreSQL and Kafka, governance enforced, TLS in front |

The same keys with different answers. Reading them side by side is the fastest way to see which
settings are *decisions* and which are addresses.

## Nothing else here is tracked

`.gitignore` in this directory ignores `*` and names the exceptions. A new file is invisible to git
until somebody adds it to that list — the safe direction, because an installation file holds
hostnames, project ids and account names. None of that is a secret in the sense Vault means, and
none of it is anybody else's business.

## No secrets, and that is enforced

A value that authenticates belongs in **HashiCorp Vault** (`FRD-116`). The examples name *where*
each one comes from and never what it is, and `tools/config_render.py` **refuses** a key it
recognises as a credential rather than asking you not to write one:

```
$ uv run python tools/config_render.py config/mine.yaml
error: 'postgres.password' would set AIRA_POSTGRES_PASSWORD, which is a credential.
       It belongs in HashiCorp Vault (`FRD-116`) — see the `secrets:` section of the examples.
```

The one credential Vault cannot supply is the one that authenticates *to* Vault:
`VAULT_SECRET_ID`, or `VAULT_SECRET_ID_FILE` on Kubernetes.

## The shape

Two levels. A section is a prefix, a key completes it — `postgres.host` is `AIRA_POSTGRES_HOST`.
Two sections are special:

- **`core:`** — keys used unprefixed, for settings that belong to no system (`AIRA_CURRENCY`).
- **`vault:`** — emitted as `VAULT_*`, because those are read *before* any settings object exists.
- **`secrets:`** — prose. A list of what the file deliberately does not hold, ignored by the
  renderer.

## It cannot drift

`tools/tests/test_the_config_examples_are_real.py` checks the examples against the settings classes
themselves, in both directions and at four levels:

1. every key an example renders is a field the product declares — a misspelling is a setting
   somebody believes they configured;
2. every field the product declares is named by an example, or listed as deliberately absent with
   the reason — this is the one that notices a **new external system**;
3. no example carries a credential, checked from outside the renderer, because these files exist to
   be edited by hand;
4. and the rendered environment is fed to both planes' settings objects with the environment
   otherwise **empty**, so the file alone has to describe a configuration the product accepts.
