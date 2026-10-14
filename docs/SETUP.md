# Setup

This page is kept so that links to it still arrive somewhere. Running AIRA is described in four
guides, one per way of running it, each written for somebody doing it for the first time:

| | For |
|---|---|
| [**Showcase**](deployment/showcase.md) | seeing the whole product work, with a local model and real traffic |
| [**Standalone**](deployment/standalone.md) | running it on one machine, everything in containers |
| [**Development**](deployment/dev.md) | changing the code, with reload on save |
| [**Integrated**](deployment/integrated.md) | your infrastructure, your Keycloak, your model platform |

Reference that applies to all four: [configuration](CONFIGURATION.md),
[integrations](INTEGRATIONS.md), [operations](DEPLOYMENT.md) — its §0 is the runbook for an
integration, in order — [testing](TESTING.md) and [roles](ROLES.md).

> **`make up-apps` is not a deployment.** It starts the applications on this repository's
> infrastructure file, whose Keycloak imports the development realm — six accounts with published
> passwords, a global administrator among them — and whose Vault runs in `-dev` mode with the root
> token `root`. On infrastructure you provide, the target is `make up-integrated`
> ([integrated](deployment/integrated.md) §5a).
