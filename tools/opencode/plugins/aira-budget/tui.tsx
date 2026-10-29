/** @jsxImportSource @opentui/solid */
/**
 * The AIRA budget inside OpenCode's own interface: a section in the session sidebar, a `/budget`
 * dialog, and a warning when a limit comes close (`FRD-144`). One file for OpenCode 2 and for
 * OpenCode 1 from 1.4 on.
 *
 * **Why a TUI plugin.** The figure a developer needs is the one that will refuse them, and the
 * place they look is the sidebar beside the conversation, where OpenCode already shows the context
 * and the session's cost. The older route, a `/budget` prompt template, asked the *model* to run
 * `curl`: the template appeared in the chat, a tool call followed, a permission dialog interrupted,
 * and the answer arrived as chat text, when the model cooperated at all. Here nothing involves the
 * model, nothing is written into the conversation, and nothing is spent.
 *
 * **What it shows.**
 * - The sidebar: the allowance that will refuse **first**, as a bar and a sentence, whose counter
 *   it is and when it resets, then what this caller spent today, this week and this month.
 * - Where the terminal is too narrow for the sidebar (OpenCode hides it at 120 columns and below),
 *   one compact line in the prompt's footer instead.
 * - `/budget`, or *AIRA budget* in the command palette: every allowance, and the three periods with
 *   requests and tokens, read again as it opens.
 * - A toast only when a limit is **crossed** (four fifths, then the limit itself) or when the
 *   budget cannot be read at all. The sidebar already shows the figure; a toast after every turn
 *   would be noise.
 * - Nothing about a missing gateway or key **before OpenCode's first turn has ended**, unless a
 *   reading has succeeded: OpenCode loads its providers and their keys beside the plugin, and what
 *   it shows in that time is not yet its setup. The sidebar says *Reading…*, and the plugin asks
 *   again (`SETTLING_MS`).
 *
 * **One file, two plugin generations.** OpenCode 2 loads a default export `{ id, setup }` and
 * OpenCode 1 a default export `{ id, tui }`; each loader ignores the other's key, so the object
 * below carries both and the view is written once against a small `Host`. Where each generation
 * finds it:
 *
 * - OpenCode 2 discovers `.opencode/plugins/aira-budget/tui.tsx` (or the same under
 *   `~/.config/opencode/plugins/`) by itself.
 * - OpenCode 1 loads a TUI plugin only from `tui.json`: `{"plugin": ["./plugins/aira-budget/tui.tsx"]}`
 *   beside it in `.opencode/`. It never scans a sub-directory of `plugins/`, so the file is not also
 *   mistaken for a server plugin, which hangs the session.
 *
 * **Imports only what the host provides.** `solid-js` and `@opentui/*` resolve to OpenCode's own
 * copies at load time; anything else would have to be installed beside the file, and a plugin a
 * developer cannot install by copying one file is one most will not install. Types are erased.
 *
 * **Where the gateway is.** The `aira` provider OpenCode has already loaded (the configuration the
 * AIRA console generates names it so), with the key OpenCode itself sends to it: from the
 * configuration, from the environment variable the configuration names, or from OpenCode's own
 * credential store (`opencode auth login aira`). `AIRA_BASE_URL` and `AIRA_API_KEY` override both.
 * It keeps no copy of a key, and sends it to that address alone: over https, or over plain http to
 * this machine, and never after a redirect.
 */
import { createMemo, createSignal, For, Show, type JSX } from "solid-js"
import { useTerminalDimensions } from "@opentui/solid"

// ---- what the figures say -----------------------------------------------------------------------

/** From this fraction of a limit on, the figure is a warning. */
export const WARN_AT = 0.8

/**
 * How often the figure is read again while nothing else asks: another session, or a colleague on a
 * shared budget, spends the same pot. Not more often, because every read sums three windows of the
 * audit trail on the gateway, and a minute across a few hundred seats is load for a sidebar.
 */
export const REFRESH_MS = 5 * 60_000

/** OpenCode 2's ends of a turn: the counters have moved by exactly the work just done. */
export const TURN_ENDED = ["session.execution.succeeded", "session.execution.failed", "session.execution.interrupted"]

/**
 * OpenCode 2's start of a turn. Before the first one ends, what OpenCode lists can lack the provider
 * that turn is about to use, so its start is a moment to ask again as well.
 */
export const TURN_STARTED = ["session.execution.started"]

/**
 * When a missing gateway or key is asked about again while it may only be *not yet* — until a
 * reading has succeeded or a turn has ended: soon, then less often, then every half minute. Each try
 * asks OpenCode, not the gateway, which is asked only once there is a gateway to ask.
 */
export const SETTLING_MS = [1_000, 2_000, 4_000, 8_000, 15_000, 30_000]

/** A budget read must never be what makes the interface wait. */
export const TIMEOUT_MS = 5_000

/** OpenCode shows its sidebar only on a terminal wider than this; below it, the prompt footer. */
export const SIDEBAR_FROM_COLUMNS = 121

/** Characters in a bar: the sidebar's content is 37 columns wide, and a percentage follows. */
export const BAR_WIDTH = 20

/** The provider id the AIRA console generates, and so the one taken without further question. */
export const PROVIDER_ID = "aira"

export type Severity = "info" | "warning" | "error"

/** `null`/`""`/`undefined` → null, a decimal string → a number. Never `NaN`. */
export function amount(value: unknown): number | null {
  if (value === null || value === undefined || `${value}`.trim() === "") return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

export type Dimension = {
  unit: "cost" | "requests" | "tokens"
  limit: number
  used: number
  left: number
  fraction: number
  scope: string
  period: string
  ends: string | null
  mine: boolean
}

/**
 * Every capped dimension of every allowance, as a comparable fraction.
 *
 * `used / limit` and not `1 - remaining / limit`: a settle past the limit makes `remaining`
 * negative on purpose, and that has to come out as a fraction above 1 rather than be clamped:
 * "spent more than allowed" is the case somebody has to explain.
 */
export function dimensions(budget: any): Dimension[] {
  const found: Dimension[] = []
  for (const allowance of budget?.allowances ?? []) {
    if (allowance?.enabled === false) continue
    for (const [unit, limitKey, usedKey, leftKey] of [
      ["cost", "limit_cost", "used_cost", "remaining_cost"],
      ["requests", "limit_requests", "used_requests", "remaining_requests"],
      ["tokens", "limit_tokens", "used_tokens", "remaining_tokens"],
    ] as const) {
      const limit = amount(allowance[limitKey])
      if (limit === null || limit <= 0) continue
      const used = amount(allowance[usedKey]) ?? 0
      found.push({
        unit,
        limit,
        used,
        left: amount(allowance[leftKey]) ?? limit - used,
        fraction: used / limit,
        scope: allowance.scope ?? "",
        period: allowance.period ?? "period",
        ends: allowance.period_ends ?? null,
        mine: Boolean(allowance.measured_for),
      })
    }
  }
  return found
}

/** The dimension that will refuse first: the one worth a line of its own. */
export function tightest(budget: any): Dimension | null {
  const all = dimensions(budget)
  if (all.length === 0) return null
  return all.reduce((worst, one) => (one.fraction > worst.fraction ? one : worst))
}

/** How loudly a fraction of a limit is said. `error` is reserved for a limit actually reached. */
export function level(fraction: number): Severity {
  if (fraction >= 1) return "error"
  if (fraction >= WARN_AT) return "warning"
  return "info"
}

/** How loudly the whole answer is said: as loudly as its tightest limit. */
export function severity(budget: any): Severity {
  const worst = tightest(budget)
  return worst ? level(worst.fraction) : "info"
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

/**
 * `2026-11-01T00:00:00+01:00` → `1 Nov`. The question that follows a refusal is *when*.
 *
 * The date **as the gateway wrote it**: it writes the moment on the installation's clock, with that
 * clock's offset, so its date is the day the installation means. Converted to this machine's zone
 * or to UTC, a reset at midnight in Berlin would read as the 31st.
 */
export function resets(ends: string | null): string | null {
  if (!ends) return null
  const date = /^(\d{4})-(\d{2})-(\d{2})T/.exec(ends)
  if (!date || Number.isNaN(new Date(ends).getTime())) return null
  return `${Number(date[3])} ${MONTHS[Number(date[2]) - 1]}`
}

/** The clock every period in the answer is on (`time_zone`), or UTC from a gateway older than it. */
export function clock(budget: any): string {
  return `${budget?.time_zone ?? ""}`.trim() || "UTC"
}

/** `1234567` → `1.2M`: a sidebar has 37 columns, and a token count is the widest figure in it. */
export function compact(value: number): string {
  const abs = Math.abs(value)
  if (abs >= 1e7) return `${Math.round(value / 1e6)}M`
  if (abs >= 1e6) return `${(value / 1e6).toFixed(1)}M`
  if (abs >= 1e4) return `${Math.round(value / 1e3)}k`
  if (abs >= 1e3) return `${(value / 1e3).toFixed(1)}k`
  return `${Math.round(value)}`
}

/** One figure of a dimension, as precise as its unit deserves. */
function figure(value: number, unit: Dimension["unit"]): string {
  if (unit === "cost") return value.toFixed(2)
  return unit === "tokens" ? compact(value) : `${Math.round(value)}`
}

/** `37 of 40 requests left`, `49.99 of 50.00 EUR left`, or `3.20 EUR over the limit`. */
export function remaining(worst: Dimension, currency: string): string {
  const label = worst.unit === "cost" ? currency : worst.unit
  const unit = label ? ` ${label}` : ""
  if (worst.left < 0) return `${figure(-worst.left, worst.unit)}${unit} over the limit`
  return `${figure(worst.left, worst.unit)} of ${figure(worst.limit, worst.unit)}${unit} left`
}

/** Whose counter, how long, and when it comes back: `use case · per day · resets 20 Oct`. */
export function whose(worst: Dimension): string {
  const owner = worst.mine ? "yours" : worst.scope === "use_case" ? "use case" : worst.scope
  const when = resets(worst.ends)
  return [owner, `per ${worst.period}`, when ? `resets ${when}` : null].filter(Boolean).join(" · ")
}

/** `██████████████░░░░░░`: full at the limit, and never wider than its cells. */
export function bar(fraction: number, width = BAR_WIDTH): string {
  const filled = Math.max(0, Math.min(width, Math.round(fraction * width)))
  return "█".repeat(filled) + "░".repeat(width - filled)
}

/** `8%`, and `112%` past the limit: the overrun is the figure somebody has to explain. */
export function percent(fraction: number): string {
  return `${Math.round(fraction * 100)}%`
}

/**
 * What a window of the audit trail says was spent: `0.42 EUR`.
 *
 * A `≥` where the record is a floor: a request served on a model with no price, or a stream the
 * caller left before its usage arrived. The gateway counts those apart for exactly this, and a
 * figure shown without the sign would claim to be the whole.
 */
export function money(window: any, currency: string): string {
  if (!window) return "–"
  const cost = amount(window.cost) ?? 0
  const floor = (window.unpriced_requests ?? 0) > 0 || (window.partial_usage_requests ?? 0) > 0
  return `${floor ? "≥" : ""}${cost.toFixed(2)}${currency ? ` ${currency}` : ""}`
}

/** `0.42 EUR · 12 req`: a sidebar row. */
export function spent(window: any, currency: string): string {
  if (!window) return "–"
  return `${money(window, currency)} · ${compact(window.requests ?? 0)} req`
}

/** The one line in the prompt footer when there is no sidebar to show the rest. */
export function brief(budget: any): string {
  const worst = tightest(budget)
  const currency = `${budget?.currency ?? ""}`.trim()
  return [
    "AIRA",
    worst ? `${percent(worst.fraction)} used` : null,
    budget?.today ? `${money(budget.today, currency)} today` : null,
  ]
    .filter(Boolean)
    .join(" · ")
}

/**
 * Whether a reading is worth a toast, given the last one that was.
 *
 * Only a **crossing upwards**, into a warning or into the limit. The sidebar shows the figure all
 * the time, so the toast is for the moment that changes what a developer should do; a figure that
 * went down (a period rolled over) changes nothing they need to be told about.
 */
export function crossed(previous: any, budget: any): Severity | null {
  const rank = { info: 0, warning: 1, error: 2 }
  const now = severity(budget)
  const before = previous ? severity(previous) : "info"
  return now !== "info" && rank[now] > rank[before] ? now : null
}

// ---- where the gateway is -----------------------------------------------------------------------

/** One provider as either OpenCode generation describes it: an address, a key, its headers. */
export type ProviderEntry = { id: string; base: string; key: string; headers: Record<string, unknown> }

export type Location = {
  base: string
  key: string
  headers: Record<string, string>
  /** What is missing, said in the words a developer fixes it with; `null` when nothing is. */
  missing: "address and key" | "key" | "address" | null
}

/** A non-empty string, or `""`. */
function text(value: unknown): string {
  return typeof value === "string" && value ? value : ""
}

/** `{env:NAME}` as OpenCode writes a reference into its configuration, resolved here when it was not. */
export function resolved(value: unknown, env: Record<string, string | undefined>): string {
  const raw = text(value)
  const reference = /^\{env:([A-Za-z0-9_]+)\}$/.exec(raw)
  return reference ? text(env[reference[1]]) : raw
}

/** The gateway's address as the budget endpoint needs it: `/v1beta` and trailing slashes off. */
export function gatewayBase(base: string): string {
  return base.replace(/\/+$/, "").replace(/\/v1beta$/, "")
}

/**
 * The gateway's address, key and use-case header, or what is missing.
 *
 * **The `aira` provider first**, because it is the gateway by name. Another name is accepted only
 * for the **one** provider that has both an address and a key, and a key and an address are never
 * taken from two entries: that pairing sends a credential to a host it was never issued for.
 *
 * The environment wins when set and completes only the `aira` entry. **Never a guessed default
 * address**: unknown is reported as unknown.
 */
export function locate(env: Record<string, string | undefined>, providers: ReadonlyArray<ProviderEntry>): Location {
  const own = providers.find((entry) => entry.id === PROVIDER_ID)
  const complete = providers.filter((entry) => entry.base && entry.key)
  const entry = own ?? (complete.length === 1 ? complete[0] : null)
  const envKey = text(env.AIRA_API_KEY)
  const envBase = text(env.AIRA_BASE_URL)
  const paired = entry !== null && (entry === own || (!envKey && !envBase))
  const base = gatewayBase(envBase || (paired ? entry.base : ""))
  const key = envKey || (paired ? entry.key : "")

  // The use case may be selected by header rather than by `/uc/<slug>` in the address: the budget
  // is asked about the same use case the model requests are attributed to.
  const headers: Record<string, string> = {}
  for (const [name, value] of Object.entries(paired ? entry.headers : {})) {
    if (name.toLowerCase() === "x-aira-use-case" && typeof value === "string") headers[name] = value
  }

  const missing = !key && !base ? "address and key" : !key ? "key" : !base ? "address" : null
  return { base, key, headers, missing }
}

/** What to do about a missing half, said where the sidebar and the dialog say it. */
export function advice(missing: Location["missing"]): string {
  if (missing === "key") return "No API key for the gateway: run opencode auth login aira, or set AIRA_API_KEY."
  if (missing === "address") return "No gateway address: use the configuration the AIRA console generates, or set AIRA_BASE_URL."
  return "No AIRA gateway configured: use the configuration the AIRA console generates, or set AIRA_BASE_URL and AIRA_API_KEY."
}

/**
 * The hosts a key may reach in plain http: this machine's own, where nothing it sends leaves it.
 * Anywhere else the key — the one every model request carries too — is readable on the way.
 */
export const LOOPBACK_HOSTS = ["localhost", "127.0.0.1", "[::1]"]

/** A refusal of the plugin's own, said as it is rather than as a gateway that did not answer. */
export class Refused extends Error {}

/** Why the key may not be sent to `base`, or `null` when it may: https, or http to this machine. */
export function unsafeAddress(base: string): string | null {
  let url: URL
  try {
    url = new URL(base)
  } catch {
    return `${base} is not an address; the API key is not sent to it.`
  }
  if (url.protocol === "https:") return null
  if (url.protocol === "http:" && LOOPBACK_HOSTS.includes(url.hostname)) return null
  return `${url.protocol}//${url.host} is not https: the API key goes only over https, or over plain http to this machine. Use the gateway's https address.`
}

/**
 * Ask the gateway. The error carries the gateway's own reason: a bare status sends nobody anywhere.
 *
 * **The key goes to the gateway and nowhere else.** Not over plain http to another machine, not
 * after a redirect — `fetch` would carry a header of ours to wherever the answer points, another
 * host or the same one over http — and an answer is read only from the origin it was asked of.
 */
export async function read(where: Location, fetcher: typeof fetch = fetch): Promise<any> {
  const refusal = unsafeAddress(where.base)
  if (refusal) throw new Refused(refusal)
  const address = `${where.base}/v1beta/me/budget`
  let response: Response
  try {
    response = await fetcher(address, {
      headers: { ...where.headers, "x-goog-api-key": where.key },
      signal: AbortSignal.timeout(TIMEOUT_MS),
      redirect: "error",
    })
  } catch (error: any) {
    // The runtime's refusal of a redirect, said in the developer's words.
    if (/redirect/i.test(`${error?.code ?? ""} ${error?.message ?? ""}`)) {
      throw new Refused(`${where.base} answered with a redirect; the API key is not sent after one.`)
    }
    throw error
  }
  if (response.redirected || (response.url && new URL(response.url).origin !== new URL(address).origin)) {
    throw new Refused(`${where.base} answered from ${response.url || "another address"}; that answer is not read.`)
  }
  if (!response.ok) {
    const body = await response.text().catch(() => "")
    let reason = body
    try {
      reason = JSON.parse(body)?.error?.message ?? body
    } catch {
      // Not the gateway's envelope: the text is the reason.
    }
    throw new Error(`${response.status}${reason ? ` ${String(reason).slice(0, 160)}` : ""}`)
  }
  return await response.json()
}

// ---- what either OpenCode generation offers, behind one shape -----------------------------------

export type Palette = { text: any; muted: any; warning: any; error: any; success: any }

/** The few things the display needs from OpenCode, written once per plugin generation below. */
export type Host = {
  palette(): Palette
  /** The providers OpenCode has loaded, each with the key it sends; `undefined` until it has them. */
  providers(): Promise<ProviderEntry[] | undefined>
  toast(variant: Severity, title: string, message: string): void
  show(render: () => JSX.Element): void
  close(): void
}

export type Reading = { budget: any; problem: string | null; at: Date | null; busy: boolean }

const WINDOWS = [
  ["today", "Today"],
  ["this_week", "This week"],
  ["this_period", "This month"],
] as const

function tone(palette: Palette, severity: Severity) {
  return severity === "error" ? palette.error : severity === "warning" ? palette.warning : palette.success
}

export function Sidebar(props: { host: Host; reading: () => Reading }) {
  const palette = () => props.host.palette()
  const budget = () => props.reading().budget
  const worst = createMemo(() => tightest(budget()))
  const currency = () => `${budget()?.currency ?? ""}`.trim()
  return (
    <box>
      <text fg={palette().text}>
        <b>AIRA budget</b>
      </text>
      <Show
        when={budget()}
        fallback={
          <text fg={props.reading().problem ? palette().warning : palette().muted}>
            {props.reading().problem ? "Unavailable · /budget says why" : "Reading…"}
          </text>
        }
      >
        <Show when={worst()} fallback={<text fg={palette().muted}>No limit set for you here</text>}>
          {(found) => (
            <box>
              <text>
                <span style={{ fg: tone(palette(), level(found().fraction)) }}>{bar(found().fraction)}</span>
                <span style={{ fg: palette().muted }}> {percent(found().fraction)}</span>
              </text>
              <text fg={palette().text}>{remaining(found(), currency())}</text>
              <text fg={palette().muted}>{whose(found())}</text>
            </box>
          )}
        </Show>
        <For each={WINDOWS}>
          {([key, label]) => (
            <box flexDirection="row" justifyContent="space-between">
              <text fg={palette().muted}>{label}</text>
              <text fg={palette().text}>{spent(budget()?.[key], currency())}</text>
            </box>
          )}
        </For>
        <Show when={budget()?.enforced === false}>
          <text fg={palette().warning}>Not enforced: figures, not stops</text>
        </Show>
        <Show when={props.reading().problem}>
          <text fg={palette().warning}>Not current · /budget says why</text>
        </Show>
      </Show>
    </box>
  )
}

export function Brief(props: { host: Host; reading: () => Reading }) {
  const terminal = useTerminalDimensions()
  const budget = () => props.reading().budget
  const severity_ = () => severity(budget())
  return (
    <Show when={budget() && terminal().width < SIDEBAR_FROM_COLUMNS}>
      <text fg={severity_() === "info" ? props.host.palette().muted : tone(props.host.palette(), severity_())}>
        {brief(budget())}
      </text>
    </Show>
  )
}

/** A table row of fixed columns: one `<text>`, so a narrow dialog truncates rather than wraps. */
function row(cells: ReadonlyArray<[string, number]>): string {
  return cells.map(([value, width]) => value.padEnd(width)).join("")
}

export function Details(props: { host: Host; reading: () => Reading; where: () => string }) {
  const palette = () => props.host.palette()
  const budget = () => props.reading().budget
  const currency = () => `${budget()?.currency ?? ""}`.trim()
  const all = createMemo(() => dimensions(budget()))
  const status = () => {
    const reading = props.reading()
    if (reading.busy) return "Reading…"
    const at = reading.at ? `Read ${reading.at.toTimeString().slice(0, 5)} from ` : ""
    return `${at}${props.where()}`
  }
  return (
    <box paddingLeft={2} paddingRight={2} paddingBottom={1} gap={1}>
      <box flexDirection="row" justifyContent="space-between">
        <text fg={palette().text}>
          <b>AIRA budget</b>
        </text>
        <text fg={palette().muted} onMouseUp={() => props.host.close()}>
          esc
        </text>
      </box>
      <Show when={props.reading().problem}>
        <text fg={palette().warning}>{props.reading().problem}</text>
      </Show>
      <Show when={budget()}>
        <text fg={palette().muted}>
          {[
            budget()?.use_case ? `use case ${budget().use_case}` : "no use case",
            budget()?.person || null,
            budget()?.enforced === false ? "limits not enforced: figures, not stops" : "limits enforced",
          ]
            .filter(Boolean)
            .join(" · ")}
        </text>
        <box>
          <text fg={palette().text}>
            <b>Allowances</b>
          </text>
          <Show when={all().length > 0} fallback={<text fg={palette().muted}>No limit binds you here.</text>}>
            <For each={all()}>
              {(one) => (
                <box paddingLeft={2}>
                  <text fg={palette().muted}>{whose(one)}</text>
                  <text>
                    <span style={{ fg: tone(palette(), level(one.fraction)) }}>{bar(one.fraction)}</span>
                    <span style={{ fg: palette().muted }}> {percent(one.fraction).padStart(4)} </span>
                    <span style={{ fg: palette().text }}>{remaining(one, currency())}</span>
                  </text>
                </box>
              )}
            </For>
          </Show>
        </box>
        <box>
          <text fg={palette().text}>
            <b>{row([["Spent", 14], ["cost", 16], ["requests", 10], ["tokens", 8]])}</b>
          </text>
          <For each={WINDOWS}>
            {([key, label]) => (
              <text fg={palette().text}>
                {row([
                  [`  ${label}`, 14],
                  [money(budget()?.[key], currency()), 16],
                  [compact(budget()?.[key]?.requests ?? 0), 10],
                  [compact(budget()?.[key]?.total_tokens ?? 0), 8],
                ])}
              </text>
            )}
          </For>
          <text fg={palette().muted}>{`  From the audit trail, in ${clock(budget())}. The limits count what enforcement reads.`}</text>
        </box>
      </Show>
      <text fg={palette().muted}>{status()}</text>
    </box>
  )
}

// ---- the behaviour, the same in both generations ------------------------------------------------

/**
 * Reads, remembers and announces the budget. Returns what a generation's glue registers: the
 * sidebar, the footer line, the `/budget` action, the refreshes to call on events, and what stops
 * it when the plugin is unloaded.
 */
export function budgetPlugin(
  host: Host,
  env: Record<string, string | undefined> = process.env,
  delays: ReadonlyArray<number> = SETTLING_MS,
) {
  const [reading, setReading] = createSignal<Reading>({ budget: null, problem: null, at: null, busy: false })
  const [where, setWhere] = createSignal("")
  let inFlight: Promise<void> | null = null
  /** The problem last toasted, so a broken setup is said once and not every minute. */
  let complained: string | null = null
  /** Whether what OpenCode shows is its setup: a reading has succeeded, or a turn has ended. */
  let settled = false
  /** The next try while it is not, and how many tries came before it. */
  let retry: ReturnType<typeof setTimeout> | null = null
  let tries = 0

  function trouble(problem: string) {
    setReading((now) => ({ ...now, problem, busy: false }))
    if (complained !== problem) host.toast("warning", "AIRA budget unavailable", problem)
    complained = problem
  }

  /** Ask again after the next of `delays`, unless a try is already due. */
  function later() {
    if (retry !== null) return
    retry = setTimeout(() => {
      retry = null
      void again()
    }, delays[Math.min(tries, delays.length - 1)])
    tries += 1
  }

  function stopTrying() {
    if (retry !== null) clearTimeout(retry)
    retry = null
  }

  async function refresh(): Promise<void> {
    const providers = await host.providers().catch(() => [])
    const found = locate(env, providers ?? [])
    if (found.missing && (providers === undefined || !settled)) {
      // Not yet: OpenCode has no providers to show, or has not had a turn — what it lists before
      // its first can lack the gateway's own entry or key. Said as "Reading…", and asked again.
      setWhere(found.base || "waiting for OpenCode's providers")
      later()
      return
    }
    setWhere(found.base || "no gateway configured")
    if (found.missing) {
      trouble(advice(found.missing))
      return
    }
    stopTrying()
    setReading((now) => ({ ...now, busy: true }))
    try {
      const budget = await read(found)
      settled = true
      const crossing = crossed(reading().budget, budget)
      setReading({ budget, problem: null, at: new Date(), busy: false })
      complained = null
      if (crossing) {
        const worst = tightest(budget)!
        host.toast(
          crossing,
          crossing === "error" ? "AIRA budget: limit reached" : "AIRA budget running low",
          `${remaining(worst, `${budget?.currency ?? ""}`.trim())} · ${whose(worst)}`,
        )
      }
    } catch (error: any) {
      trouble(error instanceof Refused ? error.message : `${found.base} did not answer: ${error?.message ?? error}`)
    }
  }

  function again(): Promise<void> {
    inFlight ??= refresh().finally(() => {
      inFlight = null
    })
    return inFlight
  }

  /** A turn ended: the counters moved, and what OpenCode shows from now on is its setup. */
  function ended(): Promise<void> {
    settled = true
    return again()
  }

  /** `/budget`: the details, read again as they open. */
  function open() {
    void again()
    host.show(() => <Details host={host} reading={reading} where={where} />)
  }

  /** The plugin is unloaded: no try is left behind it. */
  function dispose() {
    stopTrying()
  }

  return {
    reading,
    again,
    ended,
    open,
    dispose,
    sidebar: () => <Sidebar host={host} reading={reading} />,
    footer: () => <Brief host={host} reading={reading} />,
  }
}

const COMMAND = { id: "aira.budget", title: "AIRA budget", description: "What is left, and what you spent" }

/**
 * The key OpenCode 2 sends to a provider, from wherever it keeps it: `settings.apiKey` (a `{env:…}`
 * reference resolved), the environment variables the provider's configuration names, or the active
 * credential `opencode auth login` stored for it. The last is read through OpenCode's own API, the
 * one `opencode auth export` uses, and goes nowhere but that provider's own address.
 */
async function providersOf(context: any, location: any, env: Record<string, string | undefined>): Promise<ProviderEntry[] | undefined> {
  let infos = context.data.location.provider.list(location)
  if (infos === undefined) {
    await context.data.location.provider.sync(location)
    infos = context.data.location.provider.list(location)
  }
  if (infos === undefined) return undefined
  const configured: Record<string, any> = {}
  const entries = await context.client.config.get({ location }).catch(() => [])
  for (const entry of entries ?? []) Object.assign(configured, entry?.info?.providers ?? {})
  const stored: Record<string, string> = {}
  const credentials = await context.client.credential.list().catch(() => [])
  for (const credential of credentials ?? []) {
    if (credential?.active && credential?.value?.type === "key") stored[credential.integrationID] = text(credential.value.key)
  }
  return infos.map((provider: any) => ({
    id: provider.id,
    base: resolved(provider.settings?.baseURL, env),
    key:
      resolved(provider.settings?.apiKey, env) ||
      (configured[provider.id]?.env ?? []).map((name: string) => text(env[name])).find(Boolean) ||
      stored[provider.integrationID ?? provider.id] ||
      "",
    headers: provider.headers ?? {},
  }))
}

/** OpenCode 2: `setup(context)`. */
async function setup(context: any) {
  const location = () => context.location ?? context.data.location.default()
  const env = process.env
  const host: Host = {
    palette: () => ({
      text: context.theme.text.base,
      muted: context.theme.text.muted,
      warning: context.theme.text.feedback.warning.base,
      error: context.theme.text.feedback.error.base,
      success: context.theme.text.feedback.success.base,
    }),
    providers: () => providersOf(context, location(), env),
    toast: (variant, title, message) =>
      context.ui.toast.show({ variant, title, message, duration: variant === "error" ? 12_000 : 8_000 }),
    show: (render) => {
      context.ui.dialog.show(render)
      context.ui.dialog.set({ size: "large" })
    },
    close: () => context.ui.dialog.clear(),
  }
  const plugin = budgetPlugin(host, env)

  context.ui.slot({ append: "sidebar.content", render: () => plugin.sidebar() })
  context.ui.slot({ append: "prompt.footer.status", render: () => plugin.footer() })
  context.keymap.layer(() => ({
    mode: "global",
    commands: [
      {
        id: COMMAND.id,
        title: COMMAND.title,
        description: COMMAND.description,
        group: "AIRA",
        palette: true,
        slash: { name: "budget" },
        run: () => plugin.open(),
      },
    ],
  }))

  const stops = [
    ...TURN_STARTED.map((type) => context.data.on(type, () => void plugin.again())),
    ...TURN_ENDED.map((type) => context.data.on(type, () => void plugin.ended())),
  ]
  const timer = setInterval(() => void plugin.again(), REFRESH_MS)
  // Started, not awaited: the interface draws whether or not the gateway answers.
  void plugin.again()
  return () => {
    for (const stop of stops) stop()
    clearInterval(timer)
    plugin.dispose()
  }
}

/** OpenCode 1, from the release that gave TUI plugins a sidebar slot: `tui(api)`. */
async function tui(api: any) {
  const env = process.env
  const host: Host = {
    palette: () => ({
      text: api.theme.current.text,
      muted: api.theme.current.textMuted,
      warning: api.theme.current.warning,
      error: api.theme.current.error,
      success: api.theme.current.success,
    }),
    providers: async () => {
      if (!api.state.ready) return undefined
      const configured = api.state.config?.provider ?? {}
      const live = api.state.provider ?? []
      const ids = new Set<string>([...Object.keys(configured), ...live.map((p: any) => p.id)])
      return [...ids].filter(Boolean).map((id) => {
        const options = { ...(live.find((p: any) => p.id === id)?.options ?? {}), ...(configured[id]?.options ?? {}) }
        return {
          id,
          base: resolved(options.baseURL, env),
          key: resolved(options.apiKey, env) || text(live.find((p: any) => p.id === id)?.key),
          headers: options.headers ?? {},
        }
      })
    },
    toast: (variant, title, message) =>
      api.ui.toast({ variant, title, message, duration: variant === "error" ? 12_000 : 8_000 }),
    show: (render) => {
      api.ui.dialog.replace(render)
      api.ui.dialog.setSize?.("large")
    },
    close: () => api.ui.dialog.clear(),
  }
  const plugin = budgetPlugin(host, env)

  api.slots.register({
    // After the built-in context section (100), before MCP (200): the two figures a developer
    // reads together, how full the context is and how much is left to spend.
    order: 150,
    slots: {
      sidebar_content: () => plugin.sidebar(),
      session_prompt_right: () => plugin.footer(),
    },
  })
  if (api.keymap?.registerLayer) {
    api.keymap.registerLayer({
      commands: [
        {
          name: COMMAND.id,
          title: COMMAND.title,
          desc: COMMAND.description,
          category: "AIRA",
          namespace: "palette",
          slashName: "budget",
          run: () => plugin.open(),
        },
      ],
    })
  } else {
    // OpenCode 1 before its keymap API: the command list it had then, which it still accepts.
    api.command.register(() => [
      {
        title: COMMAND.title,
        value: COMMAND.id,
        description: COMMAND.description,
        category: "AIRA",
        slash: { name: "budget" },
        onSelect: () => plugin.open(),
      },
    ])
  }
  api.event.on("session.idle", () => void plugin.ended())
  const timer = setInterval(() => void plugin.again(), REFRESH_MS)
  // Not awaited: the interface draws whether or not the gateway answers. Until OpenCode has synced
  // its configuration there is no gateway to ask, so the first reading waits for that, briefly.
  const ready = setInterval(() => {
    if (!api.state.ready) return
    clearInterval(ready)
    void plugin.again()
  }, 250)
  api.lifecycle.onDispose(() => {
    clearInterval(timer)
    clearInterval(ready)
    plugin.dispose()
  })
}

export default { id: "aira.budget", setup, tui }
