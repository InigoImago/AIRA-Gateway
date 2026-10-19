// The OpenCode budget plugin (`tools/opencode/plugins/aira-budget/tui.tsx`), tested the way OpenCode
// runs it: under Bun, with `solid-js` and `@opentui/*` resolved to one shared copy and Solid's JSX
// compiled (`preload-opencode-plugin.ts`), and rendered by OpenTUI's own test renderer, so what is
// asserted is the frame a developer reads rather than what a function returned.
//
//   node_modules/.bin/bun test --conditions=browser --preload ./preload-opencode-plugin.ts \
//     ./opencode-budget-plugin.test.ts
//
// Run by `tools/tests/test_the_opencode_plugin_holds_its_properties.py`, which `make test-clients`
// and CI run, and which a mutation in the plugin has to turn red.

import { afterEach, beforeEach, describe, expect, test } from "bun:test"
import { createComponent } from "solid-js"
import { testRender } from "@opentui/solid"

const PLUGIN = new URL("../opencode/plugins/aira-budget/tui.tsx", import.meta.url).pathname
const plugin: any = await import(PLUGIN)
const source = await Bun.file(PLUGIN).text()

const PALETTE = { text: "#ffffff", muted: "#888888", warning: "#ffaa00", error: "#ff0000", success: "#00ff00" }
const ENDS = "2026-10-20T00:00:00+00:00"

function allowance(fields: Record<string, unknown>) {
  return { scope: "use_case", period: "day", period_ends: ENDS, enabled: true, measured_for: "", ...fields }
}

function window(cost: string, requests: number, extra: Record<string, unknown> = {}) {
  return { cost, requests, total_tokens: requests * 1000, unpriced_requests: 0, partial_usage_requests: 0, ...extra }
}

function budget(fields: Record<string, unknown> = {}) {
  return {
    use_case: "coding",
    person: "erika",
    enforced: true,
    currency: "EUR",
    allowances: [allowance({ limit_requests: 40, used_requests: 34, remaining_requests: 6 })],
    today: window("0.37", 35),
    this_week: window("3.10", 88),
    this_period: window("7.87", 310),
    ...fields,
  }
}

function host(overrides: Record<string, unknown> = {}) {
  return { palette: () => PALETTE, providers: async () => [], toast() {}, show() {}, close() {}, ...overrides }
}

async function frame(render: () => any, width: number, height = 16): Promise<string> {
  const setup = await testRender(render, { width, height })
  await setup.renderOnce()
  const text = setup.captureCharFrame()
  setup.renderer.destroy()
  return text
}

function lines(text: string): string[] {
  return text.split("\n").map((line) => line.trimEnd()).filter(Boolean)
}

// == what the figures say ==========================================================================

describe("the arithmetic", () => {
  test("the allowance shown is the one that refuses first, across every budget that binds", () => {
    const answer = budget({
      allowances: [
        allowance({ limit_requests: 40, used_requests: 10, remaining_requests: 30 }),
        allowance({ scope: "each_member", measured_for: "erika", period: "month", limit_cost: "50.00", used_cost: "45.00", remaining_cost: "5.00" }),
      ],
    })
    const worst = plugin.tightest(answer)
    expect(worst.unit).toBe("cost")
    expect(worst.mine).toBe(true)
    expect(plugin.remaining(worst, "EUR")).toBe("5.00 of 50.00 EUR left")
    expect(plugin.whose(worst)).toBe("yours · per month · resets 20 Oct")
  })

  test("a settle past the limit is an overrun to explain, not a full bar", () => {
    const worst = plugin.tightest(budget({ allowances: [allowance({ limit_cost: "50.00", used_cost: "53.20", remaining_cost: "-3.20" })] }))
    expect(worst.fraction).toBeGreaterThan(1)
    expect(plugin.remaining(worst, "EUR")).toBe("3.20 EUR over the limit")
    expect(plugin.percent(worst.fraction)).toBe("106%")
    expect(plugin.bar(worst.fraction, 10)).toBe("██████████")
    expect(plugin.level(worst.fraction)).toBe("error")
  })

  test("an uncapped dimension is absent rather than a large number, and a disabled budget binds nobody", () => {
    expect(plugin.dimensions(budget({ allowances: [allowance({ limit_cost: null, remaining_cost: null })] }))).toEqual([])
    expect(plugin.dimensions(budget({ allowances: [allowance({ enabled: false, limit_requests: 5, used_requests: 5 })] }))).toEqual([])
    // A limit of zero is no limit to draw a bar against: dividing by it is the bug, not the figure.
    expect(plugin.dimensions(budget({ allowances: [allowance({ limit_requests: 0, used_requests: 3 })] }))).toEqual([])
    expect(plugin.tightest(budget({ allowances: [] }))).toBeNull()
    expect(plugin.severity(budget({ allowances: [] }))).toBe("info")
  })

  test("warning from four fifths, error only at the limit", () => {
    expect(plugin.level(0.79)).toBe("info")
    expect(plugin.level(0.8)).toBe("warning")
    expect(plugin.level(0.99)).toBe("warning")
    expect(plugin.level(1)).toBe("error")
  })

  test("a toast only for a crossing upwards", () => {
    const at = (used: number) => budget({ allowances: [allowance({ limit_requests: 100, used_requests: used })] })
    expect(plugin.crossed(null, at(10))).toBeNull()
    expect(plugin.crossed(null, at(85))).toBe("warning")
    expect(plugin.crossed(at(10), at(85))).toBe("warning")
    expect(plugin.crossed(at(85), at(90))).toBeNull()
    expect(plugin.crossed(at(90), at(100))).toBe("error")
    expect(plugin.crossed(at(100), at(10))).toBeNull()
  })

  test("spend that is only a floor says so", () => {
    expect(plugin.money(window("0.42", 3), "EUR")).toBe("0.42 EUR")
    expect(plugin.money(window("0.42", 3, { unpriced_requests: 1 }), "EUR")).toBe("≥0.42 EUR")
    expect(plugin.money(window("0.42", 3, { partial_usage_requests: 1 }), "EUR")).toBe("≥0.42 EUR")
    expect(plugin.spent(window("0.42", 12), "EUR")).toBe("0.42 EUR · 12 req")
    expect(plugin.spent(undefined, "EUR")).toBe("–")
  })

  test("figures fit a sidebar", () => {
    expect(plugin.compact(999)).toBe("999")
    expect(plugin.compact(8400)).toBe("8.4k")
    expect(plugin.compact(41_000)).toBe("41k")
    expect(plugin.compact(1_250_000)).toBe("1.3M")
    expect(plugin.compact(12_000_000)).toBe("12M")
    expect(plugin.bar(0.5, 10)).toBe("█████░░░░░")
    expect(plugin.bar(-1, 4)).toBe("░░░░")
    expect(plugin.resets("2026-11-01T00:00:00+00:00")).toBe("1 Nov")
    expect(plugin.resets("not a date")).toBeNull()
    expect(plugin.resets(null)).toBeNull()
  })

  test("the footer line says how much is used and what today cost", () => {
    expect(plugin.brief(budget())).toBe("AIRA · 85% used · 0.37 EUR today")
    expect(plugin.brief(budget({ allowances: [] }))).toBe("AIRA · 0.37 EUR today")
  })
})

// == where the gateway is ===========================================================================

describe("locating the gateway", () => {
  const aira = { id: "aira", base: "https://gw.example/gw/uc/coding/v1beta/", key: "aira_k", headers: { "X-AIRA-Use-Case": "coding", "x-other": "no" } }
  const other = { id: "openai", base: "https://api.openai.com/v1", key: "sk-other", headers: {} }

  test("the aira provider is the gateway, its address without the API version", () => {
    const found = plugin.locate({}, [other, aira])
    expect(found).toEqual({ base: "https://gw.example/gw/uc/coding", key: "aira_k", headers: { "X-AIRA-Use-Case": "coding" }, missing: null })
  })

  test("another name is taken only when it is the one provider with both an address and a key", () => {
    const named = { ...aira, id: "corp-gateway" }
    expect(plugin.locate({}, [named]).key).toBe("aira_k")
    expect(plugin.locate({}, [named, other]).missing).toBe("address and key")
  })

  test("a key from the environment is never paired with somebody else's address", () => {
    const found = plugin.locate({ AIRA_API_KEY: "aira_env" }, [other])
    expect(found.base).toBe("")
    expect(found.missing).toBe("address")
  })

  test("the environment overrides the aira provider", () => {
    const found = plugin.locate({ AIRA_API_KEY: "aira_env", AIRA_BASE_URL: "http://other:8001/v1beta" }, [aira])
    expect([found.base, found.key]).toEqual(["http://other:8001", "aira_env"])
  })

  test("no address is guessed", () => {
    expect(plugin.locate({}, []).missing).toBe("address and key")
    expect(plugin.locate({}, [{ ...aira, key: "" }]).missing).toBe("key")
    expect(plugin.advice("key")).toContain("opencode auth login aira")
  })

  test("an {env:…} reference in a configuration is resolved, and an unset one is empty", () => {
    expect(plugin.resolved("{env:CORP_KEY}", { CORP_KEY: "aira_x" })).toBe("aira_x")
    expect(plugin.resolved("{env:CORP_KEY}", {})).toBe("")
    expect(plugin.resolved("aira_literal", {})).toBe("aira_literal")
  })

  test("the budget is asked with the key and the use case, and a refusal carries the gateway's reason", async () => {
    const seen: any[] = []
    const fetcher: any = async (url: string, init: any) => {
      seen.push([url, init.headers])
      // Bounded: a gateway that never answers must not leave a read pending for ever.
      expect(init.signal).toBeInstanceOf(AbortSignal)
      return new Response(JSON.stringify({ error: { message: "API key is not valid." } }), { status: 401 })
    }
    const where = { base: "https://gw.example/gw/uc/coding", key: "aira_k", headers: { "X-AIRA-Use-Case": "coding" }, missing: null }
    await expect(plugin.read(where, fetcher)).rejects.toThrow("401 API key is not valid.")
    expect(seen).toEqual([["https://gw.example/gw/uc/coding/v1beta/me/budget", { "X-AIRA-Use-Case": "coding", "x-goog-api-key": "aira_k" }]])
  })
})

// == what is drawn ==================================================================================

describe("the sidebar", () => {
  const reading = (fields: Record<string, unknown> = {}) => () => ({ budget: budget(), problem: null, at: null, busy: false, ...fields })

  test("shows the tightest limit as a bar and a sentence, then today, this week and this month, within 37 columns", async () => {
    const drawn = lines(await frame(() => createComponent(plugin.Sidebar, { host: host(), reading: reading() }), 37))
    expect(drawn).toEqual([
      "AIRA budget",
      "█████████████████░░░ 85%",
      "6 of 40 requests left",
      "use case · per day · resets 20 Oct",
      "Today               0.37 EUR · 35 req",
      "This week           3.10 EUR · 88 req",
      "This month         7.87 EUR · 310 req",
    ])
  })

  test("says when the limits stop nothing", async () => {
    const drawn = await frame(() => createComponent(plugin.Sidebar, { host: host(), reading: reading({ budget: budget({ enforced: false }) }) }), 37)
    expect(drawn).toContain("Not enforced: figures, not stops")
  })

  test("says it is reading, and says when it cannot, without inventing a figure", async () => {
    const pending = await frame(() => createComponent(plugin.Sidebar, { host: host(), reading: reading({ budget: null }) }), 37)
    expect(lines(pending)).toEqual(["AIRA budget", "Reading…"])
    const failed = await frame(() => createComponent(plugin.Sidebar, { host: host(), reading: reading({ budget: null, problem: "down" }) }), 37)
    expect(lines(failed)).toEqual(["AIRA budget", "Unavailable · /budget says why"])
    const stale = await frame(() => createComponent(plugin.Sidebar, { host: host(), reading: reading({ problem: "down" }) }), 37)
    expect(stale).toContain("Not current · /budget says why")
  })

  test("a use case nobody capped still shows what was spent", async () => {
    const drawn = await frame(() => createComponent(plugin.Sidebar, { host: host(), reading: reading({ budget: budget({ allowances: [] }) }) }), 37)
    expect(drawn).toContain("No limit set for you here")
    expect(drawn).toContain("This month         7.87 EUR · 310 req")
  })
})

describe("the /budget dialog", () => {
  test("lists every allowance and the three periods with requests and tokens", async () => {
    const answer = budget({
      allowances: [
        allowance({ limit_requests: 40, used_requests: 34, remaining_requests: 6 }),
        allowance({ scope: "each_member", measured_for: "erika", period: "month", period_ends: "2026-11-01T00:00:00+00:00", limit_cost: "50.00", used_cost: "0.07", remaining_cost: "49.93" }),
      ],
    })
    const reading = () => ({ budget: answer, problem: null, at: null, busy: false })
    const drawn = lines(await frame(() => createComponent(plugin.Details, { host: host(), reading, where: () => "https://gw.example" }), 90, 30))
    expect(drawn).toContain("  use case coding · erika · limits enforced")
    expect(drawn).toContain("    use case · per day · resets 20 Oct")
    expect(drawn).toContain("    █████████████████░░░  85% 6 of 40 requests left")
    expect(drawn).toContain("    yours · per month · resets 1 Nov")
    expect(drawn).toContain("  Spent         cost            requests  tokens")
    expect(drawn).toContain("    This month  7.87 EUR        310       310k")
    expect(drawn).toContain("    From the audit trail, in UTC. The limits count what enforcement reads.")
    expect(drawn).toContain("  https://gw.example")
  })

  test("says what is wrong when the gateway cannot be read", async () => {
    const reading = () => ({ budget: null, problem: "https://gw.example did not answer: 503", at: null, busy: false })
    const drawn = await frame(() => createComponent(plugin.Details, { host: host(), reading, where: () => "https://gw.example" }), 90, 10)
    expect(drawn).toContain("https://gw.example did not answer: 503")
  })
})

describe("the prompt footer", () => {
  const reading = () => ({ budget: budget(), problem: null, at: null, busy: false })

  test("appears only where OpenCode hides the sidebar", async () => {
    expect(await frame(() => createComponent(plugin.Brief, { host: host(), reading }), 120, 2)).toContain("AIRA · 85% used · 0.37 EUR today")
    expect((await frame(() => createComponent(plugin.Brief, { host: host(), reading }), 121, 2)).trim()).toBe("")
  })
})

// == both OpenCode generations, one file ============================================================

describe("loading", () => {
  test("one default export carries both generations' entry points under one id", () => {
    expect(plugin.default.id).toBe("aira.budget")
    expect(typeof plugin.default.setup).toBe("function")
    expect(typeof plugin.default.tui).toBe("function")
    expect("server" in plugin.default).toBe(false)
  })

  test("imports only what OpenCode provides, so the file installs by copying it", () => {
    const imports = [...source.matchAll(/^import\s+(?!type\b)[^"']*["']([^"']+)["']/gm)].map((match) => match[1])
    expect(imports.sort()).toEqual(["@opentui/solid", "solid-js"])
  })
})

let fetched: { url: string; headers: Record<string, string> }[] = []
let answers: any[] = []
const realFetch = globalThis.fetch
const realEnv = { key: process.env.AIRA_API_KEY, base: process.env.AIRA_BASE_URL }

beforeEach(() => {
  fetched = []
  answers = []
  delete process.env.AIRA_API_KEY
  delete process.env.AIRA_BASE_URL
  globalThis.fetch = (async (url: string, init: any) => {
    fetched.push({ url, headers: init.headers })
    return new Response(JSON.stringify(answers.shift() ?? budget()), { status: 200 })
  }) as any
})

afterEach(() => {
  globalThis.fetch = realFetch
  if (realEnv.key === undefined) delete process.env.AIRA_API_KEY
  else process.env.AIRA_API_KEY = realEnv.key
  if (realEnv.base === undefined) delete process.env.AIRA_BASE_URL
  else process.env.AIRA_BASE_URL = realEnv.base
})

const settle = (ms = 20) => new Promise((resolve) => setTimeout(resolve, ms))

function openCode2(options: { infos?: any[]; config?: any[]; credentials?: any[] } = {}) {
  const seen = { slots: [] as any[], layers: [] as any[], on: [] as string[], off: [] as string[], toasts: [] as any[], dialogs: 0, handlers: {} as Record<string, () => void> }
  const context = {
    location: { directory: "/project" },
    theme: { text: { base: "#fff", muted: "#888", feedback: { warning: { base: "#fa0" }, error: { base: "#f00" }, success: { base: "#0f0" } } } },
    data: {
      location: {
        default: () => ({}),
        provider: {
          list: () => options.infos ?? [{ id: "aira", integrationID: "aira", settings: { baseURL: "https://gw.example/gw/uc/coding/v1beta" }, headers: {} }],
          sync: async () => {},
        },
      },
      on: (type: string, handler: () => void) => {
        seen.on.push(type)
        seen.handlers[type] = handler
        return () => seen.off.push(type)
      },
    },
    client: {
      config: { get: async () => options.config ?? [] },
      credential: { list: async () => options.credentials ?? [{ integrationID: "aira", active: true, value: { type: "key", key: "aira_from_store" } }] },
    },
    ui: {
      slot: (claim: any) => seen.slots.push(claim),
      toast: { show: (toast: any) => seen.toasts.push(toast) },
      dialog: { show: () => seen.dialogs++, set() {}, clear() {} },
    },
    keymap: { layer: (factory: () => any) => seen.layers.push(factory()) },
  }
  return { context, seen }
}

describe("OpenCode 2", () => {
  test("adds to the sidebar and the prompt footer, offers /budget, and listens for the end of a turn", async () => {
    const { context, seen } = openCode2()
    const cleanup = await plugin.default.setup(context)
    await settle()
    expect(seen.slots.map((slot) => slot.append)).toEqual(["sidebar.content", "prompt.footer.status"])
    const [command] = seen.layers[0].commands
    expect([command.id, command.palette, command.slash.name]).toEqual(["aira.budget", true, "budget"])
    expect(seen.on.sort()).toEqual([...plugin.TURN_ENDED].sort())
    command.run()
    expect(seen.dialogs).toBe(1)
    cleanup()
    expect(seen.off.sort()).toEqual([...plugin.TURN_ENDED].sort())
  })

  test("asks with the key OpenCode keeps for the provider, and reads again when a turn ends", async () => {
    const { context, seen } = openCode2()
    const cleanup = await plugin.default.setup(context)
    await settle()
    expect(fetched).toEqual([{ url: "https://gw.example/gw/uc/coding/v1beta/me/budget", headers: { "x-goog-api-key": "aira_from_store" } }])
    seen.handlers["session.execution.succeeded"]()
    await settle()
    expect(fetched.length).toBe(2)
    cleanup()
  })

  test("the configuration's own key comes before the environment variable it names, which comes before the store", async () => {
    process.env.CORP_AIRA_KEY = "aira_from_env_name"
    try {
      const named = openCode2({ config: [{ type: "document", info: { providers: { aira: { env: ["CORP_AIRA_KEY"] } } } }] })
      ;(await plugin.default.setup(named.context))()
      const inline = openCode2({ infos: [{ id: "aira", integrationID: "aira", settings: { baseURL: "https://gw.example/v1beta", apiKey: "aira_inline" }, headers: {} }] })
      ;(await plugin.default.setup(inline.context))()
      await settle()
      expect(fetched.map((call) => call.headers["x-goog-api-key"])).toEqual(["aira_from_env_name", "aira_inline"])
    } finally {
      delete process.env.CORP_AIRA_KEY
    }
  })

  test("an inactive credential, or one for another provider, is not the gateway's key", async () => {
    const { context, seen } = openCode2({
      credentials: [
        { integrationID: "aira", active: false, value: { type: "key", key: "aira_old" } },
        { integrationID: "openai", active: true, value: { type: "key", key: "sk-other" } },
      ],
    })
    const cleanup = await plugin.default.setup(context)
    await settle()
    expect(fetched).toEqual([])
    // Said once: the same broken setup at the end of every later turn is not news.
    seen.handlers["session.execution.succeeded"]()
    await settle()
    expect(seen.toasts).toEqual([{ variant: "warning", title: "AIRA budget unavailable", message: plugin.advice("key"), duration: 8000 }])
    cleanup()
  })

  test("a crossing is toasted once, and a broken setup is said once", async () => {
    answers = [budget(), budget(), budget({ allowances: [allowance({ limit_requests: 40, used_requests: 40, remaining_requests: 0 })] })]
    const { context, seen } = openCode2()
    const cleanup = await plugin.default.setup(context)
    await settle()
    seen.handlers["session.execution.succeeded"]()
    await settle()
    seen.handlers["session.execution.succeeded"]()
    await settle()
    expect(seen.toasts.map((toast) => [toast.variant, toast.title])).toEqual([
      ["warning", "AIRA budget running low"],
      ["error", "AIRA budget: limit reached"],
    ])
    cleanup()
  })
})

function openCode1(options: { keymap?: boolean; ready?: boolean } = {}) {
  const seen = { slots: [] as any[], layers: [] as any[], commands: [] as any[], events: {} as Record<string, () => void>, toasts: [] as any[], dialogs: 0, disposers: [] as (() => void)[] }
  const api = {
    theme: { current: { text: "#fff", textMuted: "#888", warning: "#fa0", error: "#f00", success: "#0f0" } },
    state: {
      ready: options.ready ?? true,
      config: { provider: { aira: { options: { baseURL: "https://gw.example/v1beta", headers: { "x-aira-use-case": "coding" } } } } },
      // What OpenCode resolved: the key from its credential store or the environment.
      provider: [{ id: "aira", key: "aira_resolved", options: {} }],
    },
    ui: { toast: (toast: any) => seen.toasts.push(toast), dialog: { replace: () => seen.dialogs++, clear() {}, setSize() {} } },
    slots: { register: (registration: any) => seen.slots.push(registration) },
    keymap: options.keymap === false ? undefined : { registerLayer: (layer: any) => seen.layers.push(layer) },
    command: { register: (list: () => any[]) => seen.commands.push(...list()) },
    event: { on: (type: string, handler: () => void) => (seen.events[type] = handler) },
    lifecycle: { onDispose: (dispose: () => void) => seen.disposers.push(dispose) },
  }
  return { api, seen, dispose: () => seen.disposers.forEach((dispose) => dispose()) }
}

describe("OpenCode 1", () => {
  test("adds to the sidebar after the context section, and to the prompt line", async () => {
    const { api, seen, dispose } = openCode1()
    await plugin.default.tui(api)
    expect(seen.slots[0].order).toBe(150)
    expect(Object.keys(seen.slots[0].slots).sort()).toEqual(["session_prompt_right", "sidebar_content"])
    dispose()
  })

  test("offers /budget through the keymap, or through the command list of a release before it", async () => {
    const current = openCode1()
    await plugin.default.tui(current.api)
    const [command] = current.seen.layers[0].commands
    expect([command.name, command.slashName, command.namespace]).toEqual(["aira.budget", "budget", "palette"])
    command.run()
    expect(current.seen.dialogs).toBe(1)
    current.dispose()

    const older = openCode1({ keymap: false })
    await plugin.default.tui(older.api)
    expect(older.seen.commands.map((entry) => [entry.value, entry.slash.name])).toEqual([["aira.budget", "budget"]])
    older.dispose()
  })

  test("waits for OpenCode's configuration, then asks with the key OpenCode resolved and the use case", async () => {
    const { api, seen, dispose } = openCode1({ ready: false })
    await plugin.default.tui(api)
    await settle(300)
    expect(fetched).toEqual([])
    api.state.ready = true
    await settle(300)
    expect(fetched).toEqual([{ url: "https://gw.example/v1beta/me/budget", headers: { "x-aira-use-case": "coding", "x-goog-api-key": "aira_resolved" } }])
    seen.events["session.idle"]()
    await settle()
    expect(fetched.length).toBe(2)
    dispose()
  })
})
