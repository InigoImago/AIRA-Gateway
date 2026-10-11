/**
 * The wiring: what reaches the screen, how often, and what happens when nothing answers.
 *
 * The arithmetic is `budget-arithmetic.test.mjs`. What is asked here is everything that is only
 * true of the plugin: that the figure appears **before** anything has been spent, that a broken
 * setup says so once instead of looking like "no budget", that an unchanged figure does not
 * interrupt a developer every turn, and that a failed read never takes the session with it.
 *
 * The doubles are thin on purpose, and one of them is a trap this project has paid for twice: a
 * stand-in more capable than the thing it replaces. So the plugin's own returned hook is what the
 * test calls — exactly as OpenCode calls it, with an event object — and nothing here reaches past
 * the client into the plugin's internals to simulate a turn.
 */

import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { server } from "./aira-budget.js";

const { credential } = server.internals;

const KEY = "aira_0000_test";

function answer(over = {}) {
  return {
    use_case: "coding-assistant",
    person: "erika.musterfrau",
    enforced: true,
    currency: "EUR",
    allowances: [
      {
        id: 1,
        measured_for: "",
        scope: "use_case",
        period: "day",
        period_ends: "2026-10-12T00:00:00+00:00",
        enabled: true,
        limit_requests: 100,
        used_requests: 10,
        remaining_requests: 90,
        limit_cost: null,
        limit_tokens: null,
        ...over,
      },
    ],
    this_period: { cost: "0.00" },
  };
}

/**
 * The `client` a plugin is handed, with only what this plugin may use.
 *
 * `showToast` resolves, like the real one: it is an HTTP call to the TUI's own server, and whether
 * it reaches a listening TUI is exactly what the plugin cannot know.
 */
function fakeClient({ config = {}, toastFails = false } = {}) {
  const toasts = [];
  return {
    toasts,
    tui: {
      showToast: async (input) => {
        toasts.push(input.body);
        if (toastFails) throw new Error("no TUI listening");
        return true;
      },
    },
    config: { get: async () => ({ data: config }) },
  };
}

/** Replaces `fetch` with a scripted sequence and records every request. */
function stubFetch(...replies) {
  const calls = [];
  let index = 0;
  globalThis.fetch = async (url, init) => {
    calls.push({ url: `${url}`, init });
    const reply = replies[Math.min(index++, replies.length - 1)];
    if (typeof reply === "function") return reply();
    return {
      ok: reply.status === undefined || reply.status === 200,
      status: reply.status ?? 200,
      json: async () => reply.body,
      text: async () => reply.text ?? JSON.stringify(reply.body ?? ""),
    };
  };
  return calls;
}

const realFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = realFetch;
  delete process.env.AIRA_API_KEY;
  delete process.env.AIRA_BASE_URL;
});

/**
 * The plugin starts its first reading without awaiting it, so that loading cannot block OpenCode's
 * bootstrap. These tests therefore have to let the microtasks run before asserting — which is also
 * the only honest way to test it: `await server(...)` returning is **not** the same as the figure
 * having been sent, and a test that assumed otherwise would be asserting the bug back.
 */
async function settled() {
  for (let i = 0; i < 5; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
}

/** A finished turn, as OpenCode delivers it to the `event` hook. */
const IDLE = { event: { type: "session.idle", properties: {} } };

test("the figure is on screen before anything has been spent", async () => {
  process.env.AIRA_API_KEY = KEY;
  process.env.AIRA_BASE_URL = "http://gw.test";
  stubFetch({ body: answer() });
  const client = fakeClient();

  await server({ client });
  await settled();

  assert.equal(client.toasts.length, 1);
  assert.equal(client.toasts[0].variant, "info");
  assert.equal(client.toasts[0].title, "AIRA budget");
  assert.equal(
    client.toasts[0].message,
    "AIRA: 90 of 100 requests left · use case, per day · resets 12 Oct",
  );
});

test("it asks the gateway for the caller's own allowance, with the caller's own key", async () => {
  process.env.AIRA_API_KEY = KEY;
  process.env.AIRA_BASE_URL = "https://aira.example.com/";
  const calls = stubFetch({ body: answer() });

  await server({ client: fakeClient() });
  await settled();

  // One trailing slash in an environment variable is not a different endpoint.
  assert.equal(calls[0].url, "https://aira.example.com/v1beta/me/budget");
  assert.equal(calls[0].init.headers["x-goog-api-key"], KEY);
  assert.ok(calls[0].init.signal, "no timeout, so a hung gateway would hang the session");
});

test("the key in the configuration OpenCode already loaded is enough", async () => {
  // The point of the whole plugin: no second setup step. A developer who downloaded the console's
  // opencode.json has the key in that file; asking them to export it as well is how a feature
  // becomes optional in practice.
  const calls = stubFetch({ body: answer() });
  const client = fakeClient({
    config: {
      provider: { aira: { options: { apiKey: "aira_cfg", baseURL: "https://gw.example/v1beta" } } },
    },
  });

  await server({ client });
  await settled();

  assert.equal(calls[0].url, "https://gw.example/v1beta/me/budget");
  assert.equal(calls[0].init.headers["x-goog-api-key"], "aira_cfg");
});

test("the environment wins, because it is the explicit answer", async () => {
  // How a shared project configuration gets a personal key.
  process.env.AIRA_API_KEY = "aira_env";
  const calls = stubFetch({ body: answer() });

  await server({
    client: fakeClient({
      config: { provider: { aira: { options: { apiKey: "aira_cfg", baseURL: "http://gw/v1beta" } } } },
    }),
  });
  await settled();

  assert.equal(calls[0].init.headers["x-goog-api-key"], "aira_env");
  assert.equal(calls[0].url, "http://gw/v1beta/me/budget", "the base still comes from the config");
});

test("a provider the developer named themselves still carries the key", async () => {
  const found = await credential({
    config: {
      get: async () => ({
        data: { provider: { "our-gw": { options: { apiKey: "aira_x", baseURL: "http://gw/v1beta" } } } },
      }),
    },
  });

  assert.deepEqual(found, { base: "http://gw", key: "aira_x", source: "configuration" });
});

test("the version segment is taken off rather than doubled", async () => {
  for (const given of ["http://gw/v1beta", "http://gw/v1beta/", "http://gw/"]) {
    const found = await credential({
      config: { get: async () => ({ data: { provider: { aira: { options: { apiKey: "k", baseURL: given } } } } }) },
    });
    assert.equal(found.base, "http://gw");
  }
});

test("a configuration that cannot be read is not an error on its own", async () => {
  // The environment may well carry both. The complaint for having neither is made below.
  process.env.AIRA_API_KEY = KEY;
  process.env.AIRA_BASE_URL = "http://gw.test";
  const calls = stubFetch({ body: answer() });
  const client = fakeClient();
  client.config.get = async () => {
    throw new Error("not ready");
  };

  await server({ client });
  await settled();

  assert.equal(calls.length, 1);
  assert.equal(client.toasts[0].title, "AIRA budget");
});

test("no key anywhere is a complaint naming both places to fix", async () => {
  // A plugin that says nothing when it cannot read looks exactly like a use case with no budget.
  const calls = stubFetch({ body: answer() });
  const client = fakeClient();

  await server({ client });
  await settled();

  assert.equal(calls.length, 0, "it asked without a credential");
  assert.equal(client.toasts.length, 1);
  assert.equal(client.toasts[0].variant, "warning");
  assert.match(client.toasts[0].message, /AIRA_API_KEY/);
  assert.match(client.toasts[0].message, /opencode\.json/);
});

test("a gateway that refuses is reported with its status, once", async () => {
  process.env.AIRA_API_KEY = KEY;
  process.env.AIRA_BASE_URL = "http://gw.test";
  stubFetch({ status: 401, text: '{"error":{"message":"API key is not valid"}}' });
  const client = fakeClient();

  const hooks = await server({ client });
  await settled();
  await hooks.event(IDLE);

  assert.equal(client.toasts.length, 1, "the same breakage was announced twice");
  assert.match(client.toasts[0].message, /401/);
  assert.match(client.toasts[0].message, /API key is not valid/);
});

test("a gateway that is not there does not throw into the session", async () => {
  process.env.AIRA_API_KEY = KEY;
  process.env.AIRA_BASE_URL = "http://gw.test";
  stubFetch(() => {
    throw new TypeError("fetch failed");
  });
  const client = fakeClient();

  const hooks = await server({ client });
  await settled();
  await hooks.event(IDLE);

  assert.equal(client.toasts.length, 1);
  assert.match(client.toasts[0].message, /fetch failed/);
});

test("a setup that starts working says so again if it breaks again", async () => {
  process.env.AIRA_API_KEY = KEY;
  process.env.AIRA_BASE_URL = "http://gw.test";
  stubFetch({ status: 503, text: "upstream" }, { body: answer() }, { status: 503, text: "upstream" });
  const client = fakeClient();

  const hooks = await server({ client }); // fails → complaint
  await hooks.event(IDLE); // works → figure, and the complaint is re-armed
  await hooks.event(IDLE); // fails again → said again

  assert.deepEqual(
    client.toasts.map((toast) => toast.title),
    ["AIRA budget unavailable", "AIRA budget", "AIRA budget unavailable"],
  );
});

test("a turn that changed nothing does not interrupt", async () => {
  process.env.AIRA_API_KEY = KEY;
  process.env.AIRA_BASE_URL = "http://gw.test";
  stubFetch({ body: answer() });
  const client = fakeClient();

  const hooks = await server({ client });
  await settled();
  await hooks.event(IDLE);
  await hooks.event(IDLE);

  assert.equal(client.toasts.length, 1);
});

test("crossing into the last fifth interrupts, and for longer", async () => {
  process.env.AIRA_API_KEY = KEY;
  process.env.AIRA_BASE_URL = "http://gw.test";
  stubFetch({ body: answer() }, { body: answer({ used_requests: 85, remaining_requests: 15 }) });
  const client = fakeClient();

  const hooks = await server({ client });
  await settled();
  await hooks.event(IDLE);

  assert.equal(client.toasts.length, 2);
  assert.equal(client.toasts[1].variant, "warning");
  assert.ok(
    client.toasts[1].duration > client.toasts[0].duration,
    "a warning that vanishes as fast as a figure",
  );
});

test("a limit actually reached is shown as an error", async () => {
  process.env.AIRA_API_KEY = KEY;
  process.env.AIRA_BASE_URL = "http://gw.test";
  stubFetch({ body: answer() }, { body: answer({ used_requests: 100, remaining_requests: 0 }) });
  const client = fakeClient();

  const hooks = await server({ client });
  await settled();
  await hooks.event(IDLE);

  assert.equal(client.toasts.at(-1).variant, "error");
  assert.match(client.toasts.at(-1).message, /0 of 100 requests left/);
});

test("a new session is shown the figure whatever it says", async () => {
  // Two reasons, and the second is the one that makes it unconditional: a toast sent while the
  // plugin was loading can be lost (OpenCode accepts it before the interface is necessarily
  // drawing), and a session starting is the moment a developer begins spending. An unchanged
  // figure would otherwise be suppressed by `announce`, which is right for a finished turn and
  // wrong here.
  process.env.AIRA_API_KEY = KEY;
  process.env.AIRA_BASE_URL = "http://gw.test";
  stubFetch({ body: answer() });
  const client = fakeClient();

  const hooks = await server({ client });
  await settled();
  assert.equal(client.toasts.length, 1, "the attempt at load");

  await hooks.event({ event: { type: "session.created" } });

  assert.equal(client.toasts.length, 2);
  assert.equal(client.toasts[1].message, client.toasts[0].message);
});

test("a toast OpenCode never draws does not stop the next one", async () => {
  // `showToast` resolving says the server took it, not that anybody saw it. So a rejection is not
  // a reason to give up on the figure either.
  process.env.AIRA_API_KEY = KEY;
  process.env.AIRA_BASE_URL = "http://gw.test";
  stubFetch({ body: answer() });
  const client = fakeClient({ toastFails: true });

  const hooks = await server({ client });
  await settled();
  await hooks.event({ event: { type: "session.created" } });

  assert.equal(client.toasts.length, 2);
});

test("once a figure has been seen, an unremarkable event says nothing", async () => {
  process.env.AIRA_API_KEY = KEY;
  process.env.AIRA_BASE_URL = "http://gw.test";
  stubFetch({ body: answer() });
  const client = fakeClient();

  const hooks = await server({ client });
  await settled();
  await hooks.event({ event: { type: "message.part.updated" } });
  await hooks.event({ event: { type: "message.part.updated" } });

  assert.equal(client.toasts.length, 1);
});

test("an event with no type at all is not a crash", async () => {
  // Events are a growing union; the plugin must survive one it has never heard of.
  process.env.AIRA_API_KEY = KEY;
  process.env.AIRA_BASE_URL = "http://gw.test";
  stubFetch({ body: answer() });
  const client = fakeClient();

  const hooks = await server({ client });
  await settled();
  await hooks.event({});
  await hooks.event({ event: {} });

  assert.ok(client.toasts.length >= 1);
});

test("loading the plugin cannot keep OpenCode from starting", async () => {
  // Measured, with the interface drawing nothing at all: awaiting the first reading here waits on
  // the gateway **and** on the TUI's own server, which is not serving yet when a plugin loads. So
  // the reading is started and not awaited, and this is the test that says so.
  process.env.AIRA_API_KEY = KEY;
  process.env.AIRA_BASE_URL = "http://gw.test";
  globalThis.fetch = () => new Promise(() => {}); // a gateway that never answers
  const client = fakeClient();
  client.tui.showToast = () => new Promise(() => {}); // a TUI that is not listening yet

  const loaded = await Promise.race([
    server({ client }).then(() => "loaded"),
    new Promise((resolve) => setTimeout(() => resolve("blocked"), 200)),
  ]);

  assert.equal(loaded, "loaded");
});

test("a key with no gateway address is reported as a missing address", async () => {
  // Nothing is guessed. A shipped file that fell back to `http://localhost:8001` would be right
  // where it was written and wrong everywhere it is installed, and the reader would then be told
  // about a connection to a host they never named.
  process.env.AIRA_API_KEY = KEY;
  const calls = stubFetch({ body: answer() });
  const client = fakeClient();

  await server({ client });
  await settled();

  assert.equal(calls.length, 0);
  assert.match(client.toasts[0].message, /gateway URL/);
  assert.match(client.toasts[0].message, /AIRA_BASE_URL/);
  assert.ok(!client.toasts[0].message.includes("localhost"), "a guessed address in the complaint");
});

test("no address appears anywhere in the shipped plugin", async () => {
  // The same rule the console obeys (`test_the_console_carries_no_address.py`), checked here
  // because this file is handed out *by* the console and installed somewhere else entirely.
  const { readFileSync } = await import("node:fs");
  const source = readFileSync(new URL("./aira-budget.js", import.meta.url), "utf8");

  assert.ok(!/localhost|127\.0\.0\.1|:\d{4}\b/.test(source), "a host or a port is written in");
});
