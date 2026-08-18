/**
 * Where the local stack answers, for the Node-side tooling — the Angular dev proxy and the
 * Playwright suite.
 *
 * **It asks `tools/stack_addresses.py` rather than resolving anything itself.** The resolution has
 * three steps (environment, then `deploy/compose/.env`, then the default written in the Compose
 * file) and a correction that is easy to miss (`AIRA_BIND_HOST=0.0.0.0` is a listener instruction,
 * not an address to connect to). Writing that twice, once per language, would be a second copy of
 * the fact this whole arrangement exists to keep single — and it would drift in the JavaScript
 * half, because the stack keeps working either way and only the *tools* go to the wrong port.
 *
 * One child process, ~40ms, at dev-server or test-runner start. The Python module deliberately
 * imports nothing outside the standard library so this needs no dependency resolver.
 */
const { execFileSync } = require('node:child_process');
const { join } = require('node:path');

const OWNER = join(__dirname, 'stack_addresses.py');

let cached = null;

function all() {
  if (cached) return cached;
  const line = execFileSync('python3', [OWNER, 'make'], { encoding: 'utf8' }).trim();
  cached = Object.fromEntries(
    line.split(' ').map((pair) => {
      const at = pair.indexOf('=');
      return [pair.slice(0, at), pair.slice(at + 1)];
    }),
  );
  return cached;
}

/** `http://<host>:<port>` for a published service — `gateway`, `console`, `keycloak`, … */
function url(service) {
  const found = all()[service];
  if (!found) {
    throw new Error(
      `'${service}' is not a service this stack publishes. Known: ${Object.keys(all())
        .filter((k) => !k.endsWith('.netloc'))
        .join(', ')}`,
    );
  }
  return found;
}

module.exports = { url, all };
