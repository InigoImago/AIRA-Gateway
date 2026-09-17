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

## UI audit (`make ui-audit`)

Not a test suite: a walk through the whole console that reports what it finds. For every role it
starts at the navigation, clicks every tab, follows one link of every kind (one use case stands for
all of them) and opens every window a button offers. Each state is photographed at 1440 px and at
390 px and checked for:

- console errors and failed API requests;
- a page wider than the screen, controls that overlap or are covered, labels cut off, and table
  columns squeezed into stacks of letters;
- WCAG 2.2 AA through axe-core (contrast, names, roles), targets under 24 × 24 px on a phone, and
  keyboard focus that cannot be seen;
- `undefined`, `NaN`, `[object Object]` and unrendered `{{ }}` in the page;
- routes in `app.routes.ts` that the walk never reached.

It changes nothing: it presses only buttons whose names open something, never one that acts or that
would read stored content (every read is recorded, `ADR-0016`). Output: `ui-audit-report/report.md`
(findings grouped by shape, most severe first), `findings.json` and `screens/`. The screenshots are
what a person — or an agent — reviews for what no rule can see: hierarchy, spacing, wording.
`AIRA_AUDIT_ROLES=global-admin,use-case-user make ui-audit` walks a subset.

