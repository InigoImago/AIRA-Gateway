/**
 * Where the stack answers, for the browser layer.
 *
 * Asks `tools/stack_addresses.py` through `tools/stack-addresses.cjs`, so this layer follows the
 * same `AIRA_PUBLISH_…_PORT` variables that publish the stack. The suite used to carry
 * `http://localhost:8001`-shaped literals in five spec files and the config: move a port to dodge
 * a collision with another system and the browser tests fail against a perfectly healthy stack,
 * with a timeout that names no port.
 *
 * The `AIRA_E2E_*` variables still win, because they answer a different question — *which* stack
 * to test, which may be a deployed one on another host entirely.
 */
// **`require`, not `import`.** Playwright transpiles these files to CommonJS, where `import.meta`
// does not exist — a `createRequire(import.meta.url)` here loaded fine under `tsc` and threw
// `Cannot use 'import.meta' outside a module` the moment the suite ran. The helper is CommonJS on
// purpose: the Angular dev proxy config requires it too, and that one has no choice.
const { url } = require('../tools/stack-addresses.cjs') as { url: (service: string) => string };

export const CONSOLE_URL: string = process.env.AIRA_E2E_BASE_URL ?? url('console');
export const GATEWAY_URL: string = process.env.AIRA_E2E_GATEWAY_URL ?? url('gateway');
export const KEYCLOAK_URL: string = process.env.AIRA_E2E_KEYCLOAK_URL ?? url('keycloak');
