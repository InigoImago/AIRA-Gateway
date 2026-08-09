# Deployment

Four ways to run AIRA, one page each, written for somebody doing it for the first time.

| | For | Needs |
|---|---|---|
| [**Showcase**](showcase.md) | seeing the whole product work, with real traffic and a real model | Docker |
| [**Standalone**](standalone.md) | running it on one machine, everything in containers | Docker |
| [**Development**](dev.md) | changing the code, with reload on save | Docker, Python 3.14 + uv, Node 26 |
| [**Integrated**](integrated.md) | your infrastructure, your Keycloak, your model platform | see its access checklist |

Supporting reference:

- [**Configuration**](../CONFIGURATION.md) — every variable, what it does, what breaks without it
- [**Integrations**](../INTEGRATIONS.md) — what each connected system must provide
- [**Operations**](../DEPLOYMENT.md) — topics, scheduled jobs, degradation, backups
- [**Roles**](../ROLES.md) — who may do what, once it is running
