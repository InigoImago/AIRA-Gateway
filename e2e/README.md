# End-to-end tests (Playwright)

Browser-driven tests for the whole AIRA stack. They cover what unit tests structurally cannot:

- the **real Keycloak authorization-code flow** (the dev realm has the password grant disabled,
  so a token can only be obtained the way a user gets one);
- **layout at real widths** — jsdom has no layout engine, so horizontal overflow is invisible to
  the unit suite; here it is measured (`document.scrollWidth <= clientWidth`);
- the **couplings between components**: that the gateway accepts the very token the SPA holds,
  and that a saved pipeline survives the round trip through the control plane.

## Prerequisites

The containerised stack with the demo, in the order CI starts it:

```bash
make up-full        # everything in containers, demo provisioning and the local models included
make wait-healthy   # until the console, gateway, Management and Keycloak answer
make demo-ready     # until the demo's keys and models have reached the gateway
cd e2e && npx playwright install chromium   # once, unless AIRA_E2E_CHROME is set (below)
```

Processes run from source (`make run-backend` and its siblings) are not enough: the specs call the
local model `qwen3:0.6b`, configuration reaches the gateway only through the outbox relay that
`up-full` runs in a loop (`make relay` is a single pass), and the clean-up after a run executes
inside the `management` container.

The suite finds the stack where `tools/stack_addresses.py` says it is, so a port moved with an
`AIRA_PUBLISH_…_PORT` variable moves here too. `AIRA_E2E_BASE_URL`, `AIRA_E2E_GATEWAY_URL` and
`AIRA_E2E_KEYCLOAK_URL` point it at another stack instead (`e2e/stack.ts`).

## Running

```bash
make test-e2e                  # or make e2e: install, run, then purge what the run retired
cd e2e && npx playwright test --ui   # interactive; run `make purge-e2e-use-cases` afterwards
```

**A whole run can cross Management's per-user throttle** (`AIRA_THROTTLE_USER`, 600 requests a
minute): the suite signs in as a handful of demo accounts and loads screen after screen, and a
request it needed then answers `429`. CI starts the stack for this suite with `6000/minute`; for a
full local run, start it the same way: `AIRA_THROTTLE_USER=6000/minute make up-full`.

**What a run leaves behind.** Everything a test creates is written to a register under
`e2e/.artifacts/` before it is made. The global teardown (`teardown.ts`) signs in as the Global
Administrator and removes exactly what the register names, through the API — so a use case is
retired, which keeps its record. `make test-e2e` then purges those retired use cases
(`make purge-e2e-use-cases`), which is possible only on a demo installation and not over HTTP. Both
run whether the suite passed or not; a run that was killed leaves the register, and the next run's
teardown adopts it.

**When a test fails**, the reporter is a list in the terminal, and the failing test's trace is kept
under `e2e/test-results/` (`npx playwright show-trace <its trace.zip>`). The HTML report is written
only in CI, which uploads it as the `playwright-report` artifact.

## Browser

By default Playwright uses its own Chromium (`npx playwright install chromium`). Where that
download is unavailable, point the suite at an existing binary:

```bash
AIRA_E2E_CHROME=/path/to/chrome make test-e2e
```

## Realm changes

Keycloak imports `deploy/compose/keycloak/realms/aira-realm.json` only when the realm does not exist
yet. `keycloak-init`, which `make up-full` and `make showcase` run on every start, re-imports it when
a user, group, group membership or client the file names is missing from the running realm
(`tools/keycloak_demo_realm.py`). A change to something that already exists — a client's settings,
a mapper — is not detected: delete the `aira` realm in the Keycloak admin console and run
`make up-full` again. `make destroy` also works, but removes every volume and so every other piece
of data with it.

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
would read stored content (every read is recorded). Output: `ui-audit-report/report.md`
(findings grouped by shape, most severe first), `findings.json` and `screens/`. The screenshots are
what a person — or an agent — reviews for what no rule can see: hierarchy, spacing, wording.
`AIRA_AUDIT_ROLES=global-admin,use-case-user make ui-audit` walks a subset.


## Windows sign-on (`make test-windows-sso`)

Its own config (`windows-sso.config.ts`, specs in `windows-sso/`), because it needs what every other
spec must not have: a Windows domain beside the stack (Samba, profile `windows-sso`) and a realm
federated with it. The target starts the domain; the global setup gives the realm its half of the
sign-on and the teardown takes it away again, passed or not.

Each test makes the people it needs in the domain and opens one browser per person — a workstation
of the domain, holding that person's Kerberos ticket or none — and asks the console, and with the
token it got the gateway, who gets in. Chromium answers Keycloak's `Negotiate` challenge only with
MIT Kerberos' `libgssapi_krb5.so.2` on the machine; the setup refuses to run without it, and without
a running domain, rather than skip.

What it proves, on both components. **Who gets in:** a person logged on to the domain reaches the
console with no form, holding the role their AD group gives them, and the gateway lets their token
through for a use case granted to their department; without a ticket, the form and the domain
password still work. **Who does not:** nobody signs on by ticket through a browser not told to trust
Keycloak, while disabled in AD (even with a ticket issued before), from outside the part of AD
Keycloak reads, or with a ticket for a key Keycloak does not hold; and the gateway refuses a member
of another department. **When AD changes:** somebody taken out of a group loses the role or the use
case it gave, and somebody disabled can no longer renew their session — both within the bound set
by the federation's cache. What an installation sets for this, that bound included, is in
[`INTEGRATIONS.md` §2](../docs/INTEGRATIONS.md#2-keycloak-or-another-oidc-provider).
