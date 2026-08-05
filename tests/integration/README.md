# Integration tests

Server-side checks that need the **live Compose stack** (`make up`) and, for some of them, the
gateway running. They are excluded from the default hermetic `pytest` run by the `integration`
marker and executed with `make test-integration`.

Split of responsibilities:

| Suite | Runs against | Covers |
|---|---|---|
| `tests/integration/` (this folder) | Postgres, Kafka, a running gateway | infrastructure reachability, config distribution over Kafka, the gateway's HTTP contract |
| `e2e/` (Playwright) | the whole stack incl. Keycloak and the SPA in a real browser | the browser-facing behaviour: OIDC login, layout at real widths, the UI's server interactions |

Anything that needs a **user token** belongs in `e2e/`: the dev realm has the direct-access
grant disabled (ADR-0007), so a token can only be obtained by completing the code flow in a
browser.
