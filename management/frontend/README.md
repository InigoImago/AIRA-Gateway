# Console (Angular)

The management console: an Angular single-page application of standalone components, **zoneless**.
It calls Management under `/api` and the gateway under `/gw` — a prefix stripped before the gateway
sees the request — and signs people in against Keycloak with the authorization-code flow.

## Toolchain

Node 26 and npm 11 (`.nvmrc`, and `engines` in `package.json`). From the repository root,
`make sync` installs these dependencies along with the Python ones.

## Commands

Run from the repository root:

| | |
|---|---|
| `make run-frontend` | `ng serve` on the console's port with `proxy.conf.cjs`, which sends `/api` to Management and `/gw` to the gateway. Without that proxy configuration neither is reached. Management and the gateway have to be running: [`docs/deployment/dev.md`](../../docs/deployment/dev.md) |
| `make test-frontend` | the unit tests — Vitest on jsdom through `ng test`, once — with the coverage gate in `angular.json` (statements 90 %, branches 92 %, lines 93 %, functions 75 %) |
| `make lint-frontend` | Prettier in check mode; the **production** `ng build`, which is the type check (there is no ESLint); and Prettier and `tsc` over `/e2e` |
| `make fmt` | rewrites the formatting, of this and of the Python code |

Where the console signs in comes from `public/runtime-config.js`, which names the local stack's
realm (`http://localhost:8080/realms/aira`, client `aira-gateway`) and no trace address. In the
container image, `deploy/10-runtime-config.envsh` writes that file at every start from
`AIRA_OIDC_ISSUER`, `AIRA_OIDC_CLIENT_ID` and `AIRA_TRACE_URL` — where a request's trace opens,
`{trace_id}` standing for its id — and derives the content policy's `connect-src` from the issuer
unless `AIRA_CSP_CONNECT_SRC` names it.

## Conventions

- **Zoneless.** All mutable component state is a `signal`: a plain property changed from code
  schedules no re-render. `[(ngModel)]` is therefore written as
  `[ngModel]="x()" (ngModelChange)="x.set($event)"`.
- **A page is a parent plus panels.** The parent loads and owns the tab bar; each tab is a child
  that owns its form state and mutations, and reports through the page's one `PageFeedback`. In a
  child an `input()` is a signal: `{{ slug() }}` renders the value, `{{ slug }}` the function.
- **No silent failures.** Every load and mutation reports its outcome through
  `core/api/error-message.ts`, which surfaces the backend's error envelope.
- **Tests assert the rendered DOM and real interactions**, not component methods.
- **One file is generated.** `src/app/features/use-cases/opencode-plugin.generated.ts` is the
  OpenCode budget plugin the console hands out beside a new key, copied from
  `tools/opencode/plugins/aira-budget/tui.tsx` by
  `uv run python tools/opencode_plugin_asset.py --write`. Edit the plugin, never the copy: a test
  fails while the two differ.

## Browser tests

End-to-end tests are Playwright, in [`/e2e`](../../e2e/README.md) — `make test-e2e`. There is no
`ng e2e`.
