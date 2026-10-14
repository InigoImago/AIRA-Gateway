# Deployment

Four ways to run AIRA, one page each, written for somebody doing it for the first time.

| | For | Needs |
|---|---|---|
| [**Showcase**](showcase.md) | seeing the whole product work, with real traffic and a local model | Docker, `make`, `python3`, `uv`, `curl`; about 20 GB of disk |
| [**Standalone**](standalone.md) | running it on one machine, everything in containers | the same |
| [**Development**](dev.md) | changing the code, with reload on save | Docker, `make`, Python 3.14 + uv, Node 26 |
| [**Integrated**](integrated.md) | your infrastructure, your Keycloak, your model platform | see its access checklist |

Showcase, standalone and integrated run the three application images. They build them from this
checkout unless `RELEASE=vX.Y.Z` names a published release — on `make up-apps`, `make up-full`,
`make showcase` and `make up-integrated`, nothing is then built. For the integrated shape that is
the documented path: [§5a](integrated.md#5a-running-the-published-images-against-your-infrastructure).
Development runs the applications from source and builds no image.

Supporting reference:

- [**Configuration**](../CONFIGURATION.md) — every variable, what it does, what breaks without it
- [**Integrations**](../INTEGRATIONS.md) — what each connected system must provide
- [**Operations**](../DEPLOYMENT.md) — the runbook, topics, scheduled jobs, degradation, backups
- [**Testing**](../TESTING.md) — the test layers, and what each needs running
- [**Roles**](../ROLES.md) — who may do what, once it is running
