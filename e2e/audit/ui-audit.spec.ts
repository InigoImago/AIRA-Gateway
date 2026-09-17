import { Page, Response, test } from '@playwright/test';
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { USERS, login } from '../tests/support';
import { Finding, accessibilityChecks, focusChecks, layoutChecks } from './checks';

/**
 * Walks the whole console for every role and records what it finds (`make ui-audit`).
 *
 * **Discovered, not listed.** It starts from the navigation, clicks every tab, follows one link of
 * every kind inside a page (one use case stands for all of them), and opens every window a button
 * offers. `app.routes.ts` is read only to report routes the walk never reached — a route nobody can
 * navigate to is a finding in itself.
 *
 * **It changes nothing.** It clicks only buttons whose names open something (`OPENS`) and never one
 * that acts (`ACTS`); a browser `confirm()` is dismissed; a window is closed with Escape or Cancel.
 */

const OUT = resolve(__dirname, '..', 'ui-audit-report');
const ROUTES_TS = resolve(__dirname, '../../management/frontend/src/app/app.routes.ts');
const DESKTOP = { width: 1440, height: 900 };
const PHONE = { width: 390, height: 844 };

/** A button that opens something to look at. */
const OPENS =
  /^(\+\s*)?(new|add|issue|edit|create|browse|declare|view|show|details|open|grant|invite|change|compare|configure|set up)\b/i;
/** A button that does something. Checked second, so "Add and save" is never pressed. */
const ACTS =
  /delete|remove|revoke|purge|retire|restore|suspend|lift|block|run|send|save|approve|reject|import|reset|log ?out|sign out|acknowledge|generate|rotate|test|submit|confirm|release|withdraw|deprecate|request|content|prompt|payload|response/i;
// The last five: opening a request reads its stored prompt, and every read is recorded (`ADR-0016`)
// — an audit that looked at content would leave a trail of reads nobody made.

/** How long one state may take before it is reported and skipped. */
const STATE_BUDGET_MS = 90_000;

/** `AIRA_AUDIT_ROLES=global-admin,use-case-user` audits a subset. */
const ONLY = (process.env.AIRA_AUDIT_ROLES ?? '').split(',').filter(Boolean);

interface Visit {
  role: string;
  state: string;
  url: string;
  pattern: string;
  screenshots: string[];
  findings: Finding[];
}

const visits: Visit[] = [];

function slug(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 80);
}

/** `/use-cases/kundenservice?tab=keys` → `/use-cases/:id`. */
function pattern(url: string): string {
  const path = new URL(url, 'http://x').pathname;
  const parts = path.split('/').filter(Boolean);
  return (
    '/' +
    parts.map((part, index) => (index > 0 && !KNOWN_SEGMENTS.has(part) ? ':id' : part)).join('/')
  );
}

/** Literal path segments of the routes, so `/platform/roles` is not taken for `/platform/:id`. */
const KNOWN_SEGMENTS = new Set(
  [...readFileSync(ROUTES_TS, 'utf8').matchAll(/path:\s*'([^']*)'/g)]
    .flatMap((m) => m[1].split('/'))
    .filter((part) => part && !part.startsWith(':')),
);

/** Wait for the page to stop asking the server — or give up after a while and look anyway. */
async function settle(page: Page) {
  await page.waitForLoadState('networkidle', { timeout: 8_000 }).catch(() => undefined);
  await page.waitForTimeout(250);
}

class Recorder {
  private errors: string[] = [];
  private failed: string[] = [];
  throttled = false;

  constructor(private readonly page: Page) {
    page.on('console', (message) => {
      if (message.type() === 'error') this.errors.push(message.text().slice(0, 300));
    });
    page.on('pageerror', (error) => this.errors.push(`uncaught: ${error.message.slice(0, 300)}`));
    page.on('response', (response: Response) => {
      const url = response.url();
      if (!/\/(api|gw)\//.test(url)) return;
      if (response.status() === 429) this.throttled = true;
      else if (response.status() >= 400) {
        this.failed.push(
          `${response.request().method()} ${new URL(url).pathname} → ${response.status()}`,
        );
      }
    });
    page.on('dialog', (dialog) => dialog.dismiss().catch(() => undefined));
  }

  reset() {
    this.errors = [];
    this.failed = [];
    this.throttled = false;
  }

  drain(): Finding[] {
    const findings: Finding[] = [
      ...this.errors.map((text) => ({
        check: 'console-error',
        severity: 'high' as const,
        message: text,
        target: 'console',
      })),
      ...this.failed.map((text) => ({
        check: 'failed-request',
        severity: 'medium' as const,
        message: text,
        target: 'network',
      })),
    ];
    this.reset();
    return findings;
  }
}

async function record(page: Page, recorder: Recorder, role: string, state: string, scope: string) {
  let timer: NodeJS.Timeout | undefined;
  const budget = new Promise<'timeout'>((done) => {
    timer = setTimeout(() => done('timeout'), STATE_BUDGET_MS);
  });
  const outcome = await Promise.race([
    capture(page, recorder, role, state, scope).catch((error: unknown) => String(error)),
    budget,
  ]);
  clearTimeout(timer);
  if (outcome !== undefined) {
    visits.push({
      role,
      state,
      url: page.url(),
      pattern: pattern(page.url()),
      screenshots: [],
      findings: [
        {
          check: 'audit-error',
          severity: 'low',
          message:
            outcome === 'timeout' ? `state took over ${STATE_BUDGET_MS} ms` : outcome.slice(0, 300),
          target: scope,
        },
      ],
    });
  }
}

async function capture(page: Page, recorder: Recorder, role: string, state: string, scope: string) {
  const base = `${slug(role)}__${slug(state)}`;
  const findings: Finding[] = [...recorder.drain()];

  await page.setViewportSize(DESKTOP);
  await page.waitForTimeout(150);
  const desktop = join('screens', `${base}__desktop.png`);
  await page.screenshot({ path: join(OUT, desktop), fullPage: scope === 'body' });
  findings.push(...(await layoutChecks(page, scope, false)));
  findings.push(
    ...(await accessibilityChecks(page, scope).catch((error) => [
      {
        check: 'audit-error',
        severity: 'low' as const,
        message: `axe: ${String(error).slice(0, 200)}`,
        target: scope,
      },
    ])),
  );

  await page.setViewportSize(PHONE);
  await page.waitForTimeout(250);
  const phone = join('screens', `${base}__phone.png`);
  await page.screenshot({ path: join(OUT, phone), fullPage: scope === 'body' });
  findings.push(
    ...(await layoutChecks(page, scope, true)).map((f) => ({
      ...f,
      message: `[phone] ${f.message}`,
    })),
  );

  await page.setViewportSize(DESKTOP);
  // Focus last: it moves focus, which a screenshot would otherwise show.
  if (scope === 'body') findings.push(...(await focusChecks(page, 'main')));
  findings.push(...recorder.drain());

  visits.push({
    role,
    state,
    url: page.url(),
    pattern: pattern(page.url()),
    screenshots: [desktop, phone],
    findings,
  });
}

/** Every visible tab in `scope`, by name. */
async function tabs(page: Page, scope: string): Promise<string[]> {
  return page
    .locator(`${scope} [role=tab]`)
    .evaluateAll((els) =>
      els
        .filter((el) => (el as HTMLElement).offsetParent !== null)
        .map((el) => (el.textContent ?? '').trim().replace(/\s+/g, ' ')),
    );
}

async function openers(page: Page): Promise<string[]> {
  const names = await page
    .locator('main button:visible')
    .evaluateAll((els) =>
      els
        .filter((el) => !(el as HTMLButtonElement).disabled)
        .map((el) =>
          (el.getAttribute('aria-label') || el.textContent || '').trim().replace(/\s+/g, ' '),
        ),
    );
  return [...new Set(names.filter((name) => OPENS.test(name) && !ACTS.test(name)))];
}

async function closeWindow(page: Page) {
  const dialog = page.locator('[role=dialog]');
  await page.keyboard.press('Escape');
  if (await dialog.isVisible().catch(() => false)) {
    const cancel = dialog.getByRole('button', { name: /cancel|close|✕/i }).first();
    await cancel.click({ timeout: 2_000 }).catch(() => undefined);
  }
}

/** Screenshots and checks of a page, each of its tabs, and each window its buttons open. */
async function walkState(
  page: Page,
  recorder: Recorder,
  role: string,
  name: string,
  seen: Set<string>,
): Promise<string[]> {
  await record(page, recorder, role, name, 'body');
  const navigations: string[] = [];

  for (const opener of await openers(page)) {
    const button = page.locator('main').getByRole('button', { name: opener, exact: true }).first();
    const before = page.url();
    await button.click({ timeout: 3_000 }).catch(() => undefined);
    const dialog = page.locator('[role=dialog]');
    const opened = await dialog.waitFor({ state: 'visible', timeout: 1_500 }).then(
      () => true,
      () => false,
    );
    if (opened) {
      await settle(page);
      await record(page, recorder, role, `${name} › window: ${opener}`, '[role=dialog]');
      for (const tab of await tabs(page, '[role=dialog]')) {
        await dialog
          .getByRole('tab', { name: tab, exact: true })
          .click()
          .catch(() => undefined);
        await page.waitForTimeout(200);
        await record(
          page,
          recorder,
          role,
          `${name} › window: ${opener} › ${stripCount(tab)}`,
          '[role=dialog]',
        );
      }
      await closeWindow(page);
    } else if (page.url() !== before) {
      // A button that navigates: its destination joins the queue like a link.
      const target = new URL(page.url()).pathname;
      if (!seen.has(pattern(target))) navigations.push(target);
      await page.goto(before);
      await settle(page);
    }
    recorder.reset();
  }
  return navigations;
}

/** Visit one page and walk it; returns the links inside it, for the caller's queue. */
async function walkPage(
  page: Page,
  recorder: Recorder,
  role: string,
  path: string,
  seen: Set<string>,
): Promise<string[]> {
  if (seen.has(pattern(path))) return [];
  seen.add(pattern(path));
  const key = pattern(path);

  for (let attempt = 0; attempt < 3; attempt++) {
    recorder.reset();
    await page.goto(path);
    await settle(page);
    if (!recorder.throttled) break;
    // Management bounds requests per person; wait out the window rather than report its refusals.
    await page.waitForTimeout(20_000);
  }

  const found: string[] = [];
  const tabNames = await tabs(page, 'main');
  if (tabNames.length === 0) {
    found.push(...(await walkState(page, recorder, role, key, seen)));
  }
  for (const tab of tabNames) {
    await page
      .locator('main [role=tab]', { hasText: tab })
      .first()
      .click()
      .catch(() => undefined);
    await settle(page);
    found.push(...(await walkState(page, recorder, role, `${key} › ${stripCount(tab)}`, seen)));
  }

  const links = await page
    .locator('main a[href^="/"]')
    .evaluateAll((els) => els.map((el) => el.getAttribute('href') ?? ''));
  return [...found, ...links];
}

/** "Members 3" → "Members": a tab's count changes between runs, its name does not. */
function stripCount(tab: string): string {
  return tab.replace(/\s+\d+$/, '');
}

const ROLES = [
  ['global-admin', USERS.globalAdmin],
  ['it-security', USERS.security],
  ['it-steuerung', USERS.governance],
  ['use-case-admin', USERS.useCaseAdmin],
  ['use-case-user', USERS.useCaseUser],
] as const;

test.beforeAll(() => {
  rmSync(OUT, { recursive: true, force: true });
  mkdirSync(join(OUT, 'screens'), { recursive: true });
});

for (const [role, user] of ROLES.filter(([role]) => !ONLY.length || ONLY.includes(role))) {
  test(`audit as ${role}`, async ({ page }) => {
    await page.setViewportSize(DESKTOP);
    const recorder = new Recorder(page);
    await login(page, user);
    await settle(page);

    // The main navigation first: its lists are where a representative row should come from.
    const start: string[] = [];
    for (const area of ['.aira-nav', '.aira-header', '.aira-footer']) {
      start.push(
        ...(await page
          .locator(`${area} a[href]`)
          .evaluateAll((els) => els.map((el) => el.getAttribute('href') ?? ''))),
      );
    }
    // Breadth first: every page of the navigation before any link inside one, so the use case
    // that stands for all of them is one the list offers, not one a log row still names.
    const seen = new Set<string>();
    const queue = [...new Set(start)].filter((href) => href.startsWith('/'));
    while (queue.length) {
      const path = queue.shift()!;
      queue.push(...(await walkPage(page, recorder, role, path, seen)));
    }
  });
}

// After every role, not once at the end: a run stopped half-way still leaves what it saw.
test.afterEach(() => {
  writeFileSync(join(OUT, 'findings.json'), JSON.stringify(visits, null, 2));
  writeFileSync(join(OUT, 'report.md'), report(visits));
});

/** The same finding in many states is one line with a count, not a page of repeats. */
function report(all: Visit[]): string {
  const routes = [...readFileSync(ROUTES_TS, 'utf8').matchAll(/path:\s*'([^']+)'/g)].map(
    (m) => m[1],
  );
  const reached = new Set(all.map((v) => v.pattern));
  const unreached = routes
    .map((route) => '/' + route.replace(/:[a-z]+/gi, ':id'))
    // A child route (`roles` under `platform`) is reached when a visited path ends with it.
    .filter(
      (route) =>
        ![...reached].some((p) => p === route || p.startsWith(route + '/') || p.endsWith(route)),
    )
    .filter((route) => !['/model-tests'].includes(route));

  const grouped = new Map<string, { finding: Finding; states: string[] }>();
  for (const visit of all) {
    for (const finding of visit.findings) {
      // One shape, one line: row text, row numbers and measurements differ between rows of the
      // same defect and would otherwise list it once per row.
      const target = finding.target
        .replace(/"[^"]*"/g, '')
        .replace(/:nth-child\(\d+\)/g, '')
        .replace(/data-testid=[^\]]*/g, (m) => m.replace(/-[a-z0-9]*\d[a-z0-9.:-]*$/i, '-…'));
      const message = finding.message.replace(/^\[phone\] /, '').replace(/\d+(\.\d+)?/g, '#');
      const id = `${finding.severity}|${finding.check}|${message}|${target}`;
      const entry = grouped.get(id) ?? { finding, states: [] };
      entry.states.push(
        `${visit.role}: ${visit.state}${finding.message.startsWith('[phone]') ? ' (phone)' : ''}`,
      );
      grouped.set(id, entry);
    }
  }
  const order = { high: 0, medium: 1, low: 2 };
  const entries = [...grouped.values()].sort(
    (a, b) =>
      order[a.finding.severity] - order[b.finding.severity] ||
      a.finding.check.localeCompare(b.finding.check),
  );

  const lines = [
    '# UI audit',
    '',
    `${all.length} states across ${new Set(all.map((v) => v.role)).size} roles, ${entries.length} distinct findings.`,
    '',
    '## Routes the walk never reached',
    '',
    ...(unreached.length ? unreached.map((r) => `- \`${r}\``) : ['- none']),
    '',
    '## Findings',
    '',
  ];
  for (const { finding, states } of entries) {
    lines.push(
      `- **${finding.severity}** \`${finding.check}\` — ${finding.message.replace(/^\[phone\] /, '')}`,
    );
    lines.push(`  - target: \`${finding.target}\``);
    lines.push(
      `  - in ${states.length} state(s): ${[...new Set(states)].slice(0, 4).join('; ')}${states.length > 4 ? '; …' : ''}`,
    );
  }
  lines.push('', '## States', '');
  for (const visit of all) {
    lines.push(
      `- ${visit.role}: ${visit.state} — ${visit.findings.length} finding(s) — ${visit.screenshots.join(', ')}`,
    );
  }
  return lines.join('\n') + '\n';
}
