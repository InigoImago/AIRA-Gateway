/**
 * Shows what is left of the caller's AIRA budget inside OpenCode, without being asked (`FRD-139`).
 *
 * **Why a plugin and not a command.** A `/budget` command only helps somebody who knows it is
 * there; a developer who has not read the documentation spends until a `429` arrives and learns
 * about the limit from the refusal. This reads the figure by itself — once when it loads, then
 * whenever the agent finishes a turn — and pushes it into the TUI as a toast.
 *
 * **Three rules this file obeys because the installed client says so, not because the types do.**
 * Measured against `opencode 1.18.15`, each one having broken the client first:
 *
 * 1. **One file.** OpenCode loads *every* file in a plugin directory as a plugin. A helper module
 *    beside this one is handed to the plugin API, exports no plugin, and `opencode run` dies with
 *    `UnknownError`. So the arithmetic and the display live here together.
 * 2. **One export, and it is a function.** The loader calls every export as a plugin factory. An
 *    `export const id = "aira-budget"` — which `PluginModule.id` says is allowed — makes it refuse
 *    the file with *"Plugin export is not a function"*, and an exported helper is called with a
 *    plugin's arguments and its return value read as hooks. The arithmetic therefore hangs off
 *    `server` rather than being exported, which is also how the tests reach the shipped file.
 * 3. **A `server` plugin, not a `tui` one.** `TuiPluginModule` exists in the types, and a file
 *    exporting `tui` is loaded by the *server* anyway, called with a plugin's arguments, and the
 *    session then hangs. The way into the TUI's chrome is the server API the TUI itself serves:
 *    `client.tui.showToast`, which is what a budget plugin in the wild uses.
 *
 * Install by dropping **this file alone** into `.opencode/plugin/` of a project, or
 * `~/.config/opencode/plugin/` for every project. It takes the key from the `opencode.json` the
 * AIRA console generates, or from `AIRA_API_KEY`, and it talks to nothing but that gateway.
 */

// ---- the arithmetic: what the line says, and when it is worth saying --------
/** Below this fraction of a limit, nothing is said unless the figure has not been said yet. */
const WARN_AT = 0.8;

/** A step in consumption worth interrupting for, as a fraction of the tightest limit. */
const STEP = 0.1;

/** `null`/`""`/`undefined` → null, a decimal string → a number. Never `NaN`. */
function amount(value) {
  if (value === null || value === undefined || `${value}`.trim() === "")
    return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * Every capped dimension of every allowance, as a comparable fraction.
 *
 * `used / limit` and not `1 - remaining / limit`: a settle past the limit makes `remaining`
 * negative on purpose (`FRD-139`), and that has to come out as a fraction above 1 rather than be
 * clamped — "spent more than allowed" is the case somebody has to explain.
 */
function dimensions(budget) {
  const found = [];
  for (const allowance of budget?.allowances ?? []) {
    if (allowance?.enabled === false) continue;
    for (const [unit, limitKey, usedKey, leftKey] of [
      ["cost", "limit_cost", "used_cost", "remaining_cost"],
      ["requests", "limit_requests", "used_requests", "remaining_requests"],
      ["tokens", "limit_tokens", "used_tokens", "remaining_tokens"],
    ]) {
      const limit = amount(allowance[limitKey]);
      if (limit === null || limit <= 0) continue;
      const used = amount(allowance[usedKey]) ?? 0;
      found.push({
        unit,
        limit,
        used,
        left: amount(allowance[leftKey]),
        fraction: used / limit,
        scope: allowance.scope,
        period: allowance.period,
        ends: allowance.period_ends ?? null,
        mine: Boolean(allowance.measured_for),
      });
    }
  }
  return found;
}

/** The dimension that will refuse first — the only one worth putting in a single line. */
function tightest(budget) {
  const all = dimensions(budget);
  if (all.length === 0) return null;
  return all.reduce((worst, one) =>
    one.fraction > worst.fraction ? one : worst,
  );
}

/** `2026-11-01T00:00:00+00:00` → `1 Nov`. The question that follows a refusal is *when*. */
function resets(ends) {
  if (!ends) return null;
  const when = new Date(ends);
  if (Number.isNaN(when.getTime())) return null;
  const month = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
  ][when.getUTCMonth()];
  return `${when.getUTCDate()} ${month}`;
}

function rounded(value, unit) {
  return unit === "cost" ? value.toFixed(2) : `${Math.round(value)}`;
}

/**
 * The line a developer reads without having asked for it.
 *
 * Says the unit, says whose counter it is, and says when **enforcement is off** — a figure that
 * stops nothing must not be shown as if it did (`FRD-610` §3.3).
 */
function line(budget, currency) {
  const unit = (currency ?? budget?.currency ?? "").trim();
  const worst = tightest(budget);
  const enforced = budget?.enforced !== false;

  if (!worst) {
    const spent = amount(budget?.this_period?.cost);
    if (spent === null) return "AIRA: no budget configured";
    return `AIRA: ${spent.toFixed(2)}${unit ? ` ${unit}` : ""} spent this month · no limit set`;
  }

  const left = worst.left ?? worst.limit - worst.used;
  const label =
    worst.unit === "cost" ? (unit ? ` ${unit}` : "") : ` ${worst.unit}`;
  const whose = worst.mine
    ? "mine"
    : worst.scope === "use_case"
      ? "use case"
      : worst.scope;
  const when = resets(worst.ends);

  const head =
    left < 0
      ? `AIRA: ${rounded(-left, worst.unit)}${label} over the ${rounded(worst.limit, worst.unit)} limit`
      : `AIRA: ${rounded(left, worst.unit)} of ${rounded(worst.limit, worst.unit)}${label} left`;

  return [
    head,
    `${whose}, per ${worst.period ?? "period"}`,
    when ? `resets ${when}` : null,
    enforced ? null : "not enforced",
  ]
    .filter(Boolean)
    .join(" · ");
}

/** How loudly to say it. `error` is reserved for a limit that is actually reached. */
function severity(budget) {
  const worst = tightest(budget);
  if (!worst) return "info";
  if (worst.fraction >= 1) return "error";
  if (worst.fraction >= WARN_AT) return "warning";
  return "info";
}

/**
 * Whether this reading is worth interrupting for, given the last one that was.
 *
 * The whole point of the plugin is that nobody has to ask — and the whole way to make that
 * unbearable is to say it after every turn. So: the first reading always, a change in severity
 * always, and otherwise only when a tenth of the tightest limit has gone since the last notice.
 * A figure that moved *down* (a period rolled over, a reservation released) is not an interruption.
 */
function announce(previous, budget) {
  if (!budget) return false;
  if (!previous) return true;
  if (severity(previous) !== severity(budget)) return true;
  const before = tightest(previous);
  const now = tightest(budget);
  if (!before || !now) return Boolean(before) !== Boolean(now);
  return now.fraction - before.fraction >= STEP;
}

// ---- the display: what reaches the screen, and how often ---------------------

/** A budget read must never be what makes a keystroke wait. */
const TIMEOUT_MS = 5000;

/** How long a figure stays on screen. Longer for the ones that mean something. */
const DURATION = { info: 4000, warning: 8000, error: 12000 };

/**
 * Where to ask, and with what.
 *
 * **The configuration first, because the key is already in it.** A developer who took the
 * console's generated `opencode.json` has the gateway's URL and their own key in the file OpenCode
 * has already loaded; asking them to export two environment variables as well is the kind of
 * second step that makes a feature optional in practice. The environment still wins when set — it
 * is the explicit answer, and it is how a shared project configuration gets a personal key.
 *
 * The provider's `baseURL` ends in `/v1beta` (it is a Gemini-shaped surface) and this endpoint is
 * `/v1beta/me/budget`, so the version segment is taken off rather than doubled.
 */
async function credential(client) {
  const env = {
    base: process.env.AIRA_BASE_URL ?? "",
    key: process.env.AIRA_API_KEY ?? "",
  };

  let providers = {};
  try {
    const config = await client?.config?.get?.();
    providers = (config?.data ?? config)?.provider ?? {};
  } catch {
    // A configuration this plugin cannot read is not an error worth a toast: the environment may
    // well carry the key, and the complaint for having neither is made once, below.
    providers = {};
  }

  // `aira` is what the console generates; any other name is accepted too, because a developer may
  // have named the provider after their own installation.
  const names = providers.aira ? ["aira"] : Object.keys(providers);
  let base = "";
  let key = "";
  for (const name of names) {
    const options = providers[name]?.options ?? {};
    if (!key && typeof options.apiKey === "string" && options.apiKey) key = options.apiKey;
    if (!base && typeof options.baseURL === "string" && options.baseURL) base = options.baseURL;
    if (key && base) break;
  }

  return {
    // **Never a guessed default.** A shipped file that falls back to a loopback address is right
    // where it was written and wrong everywhere it is installed — and it turns "nobody told me
    // where the gateway is" into a connection error about a host the reader never named. Unknown
    // is reported as unknown, below. (The console obeys the same rule for itself.)
    base: (env.base || base).replace(/\/v1beta\/?$/, "").replace(/\/+$/, ""),
    key: env.key || key,
    // Named in the complaint, so a developer is told which of the two places to fix.
    source: env.key ? "environment" : key ? "configuration" : "nowhere",
  };
}

export const server = async ({ client }) => {
  let last = null;
  /** The reason last complained about, so a broken setup is said once and not every turn. */
  let complaint = null;

  function toast(variant, title, message) {
    // Deliberately not awaited for its result and never allowed to throw: this is decoration, and
    // decoration that can fail a session is worse than no decoration.
    return client.tui
      .showToast({ body: { title, message, variant, duration: DURATION[variant] ?? DURATION.info } })
      .then(
        () => true,
        () => false,
      );
  }

  /** Said out loud exactly once per distinct reason: a silent failure here looks like no budget. */
  async function trouble(reason) {
    if (complaint === reason) return;
    complaint = reason;
    await toast("warning", "AIRA budget unavailable", reason);
  }

  async function read(base, key) {
    const response = await fetch(`${base}/v1beta/me/budget`, {
      headers: { "x-goog-api-key": key },
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    if (!response.ok) {
      // The gateway's error envelope names the reason; a bare status code sends a reader nowhere.
      const body = await response.text().catch(() => "");
      throw new Error(`${response.status} ${body.slice(0, 200)}`.trim());
    }
    return await response.json();
  }

  async function show(always) {
    const { base, key } = await credential(client);
    if (!key || !base) {
      await trouble(
        `No ${key ? "gateway URL" : "API key"}: use the opencode.json the AIRA console generates, ` +
          `or set ${key ? "AIRA_BASE_URL" : "AIRA_API_KEY"}.`,
      );
      return;
    }
    let budget;
    try {
      budget = await read(base, key);
    } catch (error) {
      await trouble(`${base} did not answer: ${error?.message ?? error}`);
      return;
    }
    complaint = null;

    if (always || announce(last, budget)) {
      const level = severity(budget);
      await toast(level, "AIRA budget", line(budget, budget?.currency));
    }
    last = budget;
  }

  // Once at load, so the figure is on screen before anything has been spent. This is the half a
  // command cannot do, and the whole reason the plugin exists.
  //
  // **Started and not awaited.** Awaiting it here blocks OpenCode's bootstrap on a gateway and on
  // the TUI's own server — and the TUI is not serving yet, so the first toast's HTTP call never
  // completes. Measured: the interface drew nothing at all and the client sat there until it was
  // killed. A plugin that decorates a session must not be able to stop one from starting.
  void show(true).catch(() => {});

  return {
    event: async ({ event }) => {
      // **A new session is shown the figure whatever it says.** Not only because a toast sent
      // while the plugin was loading can be lost — OpenCode accepts it before the interface is
      // necessarily drawing anything — but because this is the moment a developer starts
      // spending, which is the moment the number is worth reading.
      if (event?.type === "session.created") {
        await show(true);
        return;
      }
      // The end of a turn: the counters have moved by exactly the work just done, so the figure is
      // re-read and `announce` decides whether it is worth interrupting for.
      if (event?.type === "session.idle") await show(false);
    },
  };
};

// Not exported, because the loader calls every export as a plugin factory (see the header). The
// tests reach the arithmetic through the shipped file rather than through a copy of it.
server.internals = { announce, credential, dimensions, line, severity, tightest };
