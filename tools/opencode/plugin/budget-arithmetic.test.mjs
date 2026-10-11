/**
 * What the one line may and may not say (`FRD-139` §5).
 *
 * Run by `node --test` — no OpenCode, no network, no gateway. Every case here is a way the line
 * could be *plausible and wrong*, which is the only kind of wrong that survives a screenshot: a
 * figure beside a limit nobody set, a dollar sign on a euro, a warning that fires every turn, or
 * a reassuring number because the one that binds was further down the list.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { server } from "./aira-budget.js";

// Not imported by name: the plugin may export nothing but `server`, because OpenCode calls every
// export as a plugin factory (the file's own header records what that cost). The arithmetic hangs
// off it, and these tests deliberately read the shipped file rather than a copy.
const { announce, dimensions, line, severity, tightest } = server.internals;

/** The live answer's shape, trimmed to what this module reads (`GET /v1beta/me/budget`). */
function allowance(over = {}) {
  return {
    id: 1,
    measured_for: "",
    scope: "use_case",
    period: "day",
    period_ends: "2026-10-12T00:00:00+00:00",
    enabled: true,
    limit_cost: null,
    limit_tokens: null,
    limit_requests: 500,
    used_tokens: 10158,
    used_requests: 7,
    used_cost: "0.0011",
    remaining_cost: null,
    remaining_tokens: null,
    remaining_requests: 493,
    ...over,
  };
}

function budget(over = {}) {
  return {
    use_case: "coding-assistant",
    person: "erika.musterfrau",
    enforced: true,
    currency: "EUR",
    allowances: [allowance()],
    this_period: { cost: "0.00", requests: 0 },
    ...over,
  };
}

test("an uncapped dimension is not a dimension", () => {
  // `limit_cost: null` means *no cost cap*, and a bar drawn from it would show a limit nobody set.
  const found = dimensions(budget());

  assert.deepEqual(
    found.map((one) => one.unit),
    ["requests"],
  );
});

test("a limit of zero is not a limit either", () => {
  // Nothing writes it, but `used / 0` is Infinity and would read as a permanent refusal.
  const found = dimensions(
    budget({ allowances: [allowance({ limit_requests: 0 })] }),
  );

  assert.deepEqual(found, []);
});

test("a disabled budget is not consulted", () => {
  // A lifted budget still has a row and still has figures; it just does not bind (`FRD-401`).
  const found = dimensions(
    budget({ allowances: [allowance({ enabled: false })] }),
  );

  assert.deepEqual(found, []);
});

test("the tightest dimension wins across budgets and across units", () => {
  const found = tightest(
    budget({
      allowances: [
        allowance({ limit_requests: 500, used_requests: 50 }), // 10%
        allowance({
          id: 2,
          scope: "each_member",
          measured_for: "erika.musterfrau",
          period: "month",
          limit_cost: "5.00",
          used_cost: "4.50", // 90% — this is the one that will refuse
          remaining_cost: "0.50",
          limit_requests: 200,
          used_requests: 20, // 10%
          remaining_requests: 180,
        }),
      ],
    }),
  );

  assert.equal(found.unit, "cost");
  assert.equal(found.scope, "each_member");
  assert.ok(Math.abs(found.fraction - 0.9) < 1e-9);
});

test("the line names the unit from the installation, never a dollar", () => {
  const text = line(
    budget({
      allowances: [
        allowance({
          limit_cost: "5.00",
          used_cost: "1.00",
          remaining_cost: "4.00",
          limit_requests: null,
          remaining_requests: null,
          period: "month",
          period_ends: "2026-11-01T00:00:00+00:00",
        }),
      ],
    }),
  );

  assert.equal(
    text,
    "AIRA: 4.00 of 5.00 EUR left · use case, per month · resets 1 Nov",
  );
  assert.ok(!text.includes("$"));
});

test("an installation that has not said its currency gets no unit at all", () => {
  // An unlabelled amount is a reader who asks; a wrongly labelled one is a reader who does not.
  const text = line(
    budget({
      currency: "",
      allowances: [
        allowance({
          limit_cost: "5.00",
          used_cost: "1.00",
          remaining_cost: "4.00",
          limit_requests: null,
        }),
      ],
    }),
  );

  assert.equal(
    text,
    "AIRA: 4.00 of 5.00 left · use case, per day · resets 12 Oct",
  );
});

test("a request limit is counted in requests, not in money", () => {
  assert.equal(
    line(budget()),
    "AIRA: 493 of 500 requests left · use case, per day · resets 12 Oct",
  );
});

test("the period rolls over in UTC, whatever the reader's clock says", () => {
  // `period_ends` is the gateway's own boundary. Formatting it locally would print "31 Oct" for a
  // reader an hour behind, which is a date the counter does not reset on.
  const previous = process.env.TZ;
  process.env.TZ = "America/Los_Angeles";
  try {
    const text = line(
      budget({
        allowances: [allowance({ period_ends: "2026-11-01T00:00:00+00:00" })],
      }),
    );
    assert.ok(text.endsWith("resets 1 Nov"), text);
  } finally {
    process.env.TZ = previous;
  }
});

test("spending past the limit is reported as past the limit", () => {
  // Enforcement refuses *at* the limit, so a negative remainder takes a settle correcting a
  // reservation upward (`FRD-139`). It is rare, and it is the case somebody has to explain.
  const over = budget({
    allowances: [
      allowance({
        limit_cost: "5.00",
        used_cost: "5.12",
        remaining_cost: "-0.12",
        limit_requests: null,
        remaining_requests: null,
      }),
    ],
  });

  assert.equal(
    line(over),
    "AIRA: 0.12 EUR over the 5.00 limit · use case, per day · resets 12 Oct",
  );
  assert.equal(severity(over), "error");
});

test("a limit that stops nothing says so", () => {
  const text = line(budget({ enforced: false }));

  assert.ok(text.endsWith("not enforced"), text);
});

test("no budget at all still answers the question the feature was asked", () => {
  // *"What have I used"* has an answer without any limit to put beside it.
  const text = line(budget({ allowances: [], this_period: { cost: "1.23" } }));

  assert.equal(text, "AIRA: 1.23 EUR spent this month · no limit set");
});

test("nothing known is said plainly rather than as a zero", () => {
  assert.equal(line({}), "AIRA: no budget configured");
  assert.equal(line(null), "AIRA: no budget configured");
});

test("severity escalates once, at four fifths, and not before", () => {
  const at = (used) =>
    severity(
      budget({
        allowances: [allowance({ limit_requests: 100, used_requests: used })],
      }),
    );

  assert.equal(at(79), "info");
  assert.equal(at(80), "warning");
  assert.equal(at(99), "warning");
  assert.equal(at(100), "error");
});

test("the first reading is always said, and an unchanged one never is", () => {
  const first = budget();

  assert.equal(announce(null, first), true);
  assert.equal(announce(first, first), false);
});

test("crossing into warning interrupts even when little was spent", () => {
  const before = budget({
    allowances: [allowance({ limit_requests: 100, used_requests: 79 })],
  });
  const after = budget({
    allowances: [allowance({ limit_requests: 100, used_requests: 80 })],
  });

  assert.equal(announce(before, after), true);
});

test("a tenth of the limit is worth an interruption; a hundredth is not", () => {
  const at = (used) =>
    budget({
      allowances: [allowance({ limit_requests: 100, used_requests: used })],
    });

  assert.equal(announce(at(10), at(20)), true);
  assert.equal(announce(at(10), at(11)), false);
});

test("a counter that went down is not an interruption", () => {
  // A period rolled over, or a reservation was released. Nothing was consumed, so nothing is said.
  const spent = budget({
    allowances: [allowance({ limit_requests: 100, used_requests: 90 })],
  });
  const fresh = budget({
    allowances: [allowance({ limit_requests: 100, used_requests: 0 })],
  });

  assert.equal(
    announce(spent, fresh),
    true,
    "severity falls from warning to info, which is news",
  );

  const slightly = budget({
    allowances: [allowance({ limit_requests: 100, used_requests: 20 })],
  });
  const less = budget({
    allowances: [allowance({ limit_requests: 100, used_requests: 10 })],
  });
  assert.equal(announce(slightly, less), false);
});

test("a budget appearing where there was none is news", () => {
  const none = budget({ allowances: [] });

  assert.equal(announce(none, budget()), true);
  assert.equal(announce(budget(), none), true);
});

test("nothing read is not a reading", () => {
  // A failed fetch must not overwrite the last good figure with silence.
  assert.equal(announce(budget(), null), false);
});
