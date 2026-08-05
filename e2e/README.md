# End-to-end tests (Playwright)

Browser-driven tests for the whole AIRA stack. They cover what unit tests structurally cannot:

- the **real Keycloak authorization-code flow** (the dev realm has the password grant disabled,
  so a token can only be obtained the way a user gets one);
- **layout at real widths** — jsdom has no layout engine, so horizontal overflow is invisible to
  the unit suite; here it is measured (`document.scrollWidth <= clientWidth`);
- the **couplings between components**: that the gateway accepts the very token the SPA holds,
  and that a saved pipeline survives the round trip through the control plane.

## Prerequisites

```bash
make up                 # infrastructure (postgres, keycloak, kafka, …)
make seed               # migrate + demo accounts
make migrate-gateway
make kafka-topics
make run-backend        # management on :8002   (own terminal)
make run-gateway-oidc   # gateway on :8001 with OIDC   (own terminal)
make consume            # gateway config consumer   (own terminal)
make run-frontend       # SPA on :4200   (own terminal)
```

The gateway must run with OIDC enabled, otherwise it cannot verify the SPA's token and the
dry-run and consumption views will (correctly) refuse it — `make run-gateway-oidc` does that.

## Running

```bash
make e2e                       # or: cd e2e && npx playwright test
npx playwright test --ui       # interactive
npx playwright show-report     # after a failing run
```

## Browser

By default Playwright uses its own Chromium (`npx playwright install chromium`). Where that
download is unavailable, point the suite at an existing binary:

```bash
AIRA_E2E_CHROME=/path/to/chrome make e2e
```

## Realm changes are not hot-reloaded

Keycloak imports `deploy/compose/keycloak/realms/*.json` only when the realm does not exist yet
(`IGNORE_EXISTING`). After editing the realm, recreate it — `make destroy && make up`, or delete
the realm in the admin console and restart the container — otherwise the tests run against the
old configuration.
