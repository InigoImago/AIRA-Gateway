import { Page, expect } from '@playwright/test';

/** Demo accounts seeded into the Keycloak realm (deploy/compose/keycloak/realms). */
export const USERS = {
  globalAdmin: { username: 'admin', password: 'demo-password' },
  useCaseAdmin: { username: 'ucadmin', password: 'demo-password' },
  useCaseUser: { username: 'ucuser', password: 'demo-password' },
  governance: { username: 'itgov', password: 'demo-password' },
  // `it-security`. A separate account from `itgov` on purpose: who may *see* every use case and
  // who may *stop* traffic are different questions, and the console keeps them apart.
  security: { username: 'itsec', password: 'demo-password' },
} as const;

/**
 * Complete the real Keycloak authorization-code flow.
 *
 * Deliberately driven through the browser rather than a password grant: the dev realm has the
 * direct-access grant disabled (ADR-0007), so this exercises exactly the flow a user takes,
 * including the PKCE challenge the SPA generates.
 */
export async function login(page: Page, user: { username: string; password: string }) {
  await page.goto('/');

  // Either the app redirects to Keycloak (first visit) or an existing session lands straight in
  // the SPA. Waiting for whichever appears avoids racing the redirect.
  const loginButton = page.locator('#kc-login');
  const userName = page.locator('.aira-user__name');
  await expect(loginButton.or(userName).first()).toBeVisible({
    timeout: 30_000,
  });

  if (await loginButton.isVisible()) {
    await page.fill('#username', user.username);
    await page.fill('#password', user.password);
    await loginButton.click();
  }

  await expect(userName).toHaveText(user.username, { timeout: 30_000 });
}

/** Log out so the next login starts from a clean Keycloak session. */
export async function logout(page: Page) {
  await page.click('.aira-user button');
  await expect(page.locator('#kc-login').or(page.locator('#username')).first()).toBeVisible({
    timeout: 30_000,
  });
}

/**
 * Assert the document is not wider than the viewport.
 *
 * This is the objective form of "the page overflows": if any element pushes the document past
 * the viewport width, the whole page scrolls sideways. A tolerance of 1px absorbs sub-pixel
 * rounding at fractional device scales.
 */
export async function expectNoHorizontalOverflow(page: Page, context: string) {
  const overflow = await page.evaluate(() => {
    const doc = document.documentElement;
    // Report the widest offenders so a failure names the element, not just the number.
    const guilty: string[] = [];
    for (const el of Array.from(document.querySelectorAll<HTMLElement>('body *'))) {
      const rect = el.getBoundingClientRect();
      if (rect.width > 0 && rect.right > doc.clientWidth + 1) {
        const id = el.id ? `#${el.id}` : '';
        const cls =
          el.className && typeof el.className === 'string'
            ? `.${el.className.trim().split(/\s+/).join('.')}`
            : '';
        guilty.push(`${el.tagName.toLowerCase()}${id}${cls} (right=${Math.round(rect.right)})`);
      }
    }
    return {
      scrollWidth: doc.scrollWidth,
      clientWidth: doc.clientWidth,
      guilty: guilty.slice(0, 5),
    };
  });

  expect(
    overflow.scrollWidth,
    `${context}: document scrolls horizontally (${overflow.scrollWidth}px in a ${overflow.clientWidth}px viewport). Widest offenders: ${overflow.guilty.join(', ') || 'none identified'}`,
  ).toBeLessThanOrEqual(overflow.clientWidth + 1);
}

/**
 * Create a use case through the UI, returning its slug.
 *
 * Creation is a window reached from a button, and saving ends on the new use case's **settings**
 * (FRD-206 FR-8) — one with no members, no budget and no limits is not finished, and the list is
 * what makes it look finished. The technical id is filled in from the name; it is typed here
 * anyway because these tests need a slug they chose.
 */
export async function createUseCase(page: Page, slug: string, name: string) {
  await page.goto('/use-cases');
  await page.click('button:has-text("New use case")');
  await page.fill('#uc-name', name);
  await page.fill('#uc-slug', slug);
  await page.click('button[type="submit"][form="uc-create-form"]');
  await expect(page).toHaveURL(new RegExp(`/use-cases/${slug}`), { timeout: 30_000 });
  return slug;
}

/**
 * Grant a Keycloak group path on a use case, through the API.
 *
 * Shared because a fixture is not a walkthrough: a test whose *subject* is the picker drives the
 * picker, and a test that merely needs somebody to administer something says so in one line. It
 * lived inside the access spec until a fresh database showed why the others needed it — several
 * tests assumed `demo-uc` existed and that `ucadmin` administered it, which was true only because
 * an earlier run had left it behind.
 */
export async function grantGroup(page: Page, slug: string, path: string, role = 'user') {
  const status = await page.evaluate(
    async ({ slug, path, role }) => {
      const token = sessionStorage.getItem('access_token') ?? localStorage.getItem('access_token');
      const response = await fetch(`/api/v1/use-cases/${slug}/groups/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ group_path: path, role }),
      });
      return response.status;
    },
    { slug, path, role },
  );
  expect(status, `could not grant ${path} on ${slug}`).toBeLessThan(300);
}

/**
 * Release every approved model to a use case, through the API (`FRD-308`).
 *
 * A new use case may call **nothing**, so any test that creates one and then sends real traffic
 * has to do what an administrator now has to do. Deliberately **not** folded into
 * `createUseCase`: the step would then be invisible in every test, and the one thing this suite
 * should say out loud is that a use case without a release is a use case that refuses everything.
 */
export async function releaseAllModels(page: Page, slug: string) {
  const status = await page.evaluate(async (slug) => {
    const token = sessionStorage.getItem('access_token') ?? localStorage.getItem('access_token');
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };
    const catalog = await (await fetch('/api/v1/models/', { headers })).json();
    const approved = catalog
      .filter((model: { approved?: boolean }) => model.approved !== false)
      .map((model: { name: string }) => model.name);
    const response = await fetch(`/api/v1/use-cases/${slug}/`, {
      method: 'PATCH',
      headers,
      body: JSON.stringify({ allowed_models: approved }),
    });
    return response.status;
  }, slug);
  expect(status, `could not release models on ${slug}`).toBeLessThan(300);
}

/**
 * Wait until the **gateway** agrees the caller reaches this use case.
 *
 * A group grant travels to the gateway over Kafka and is then cached in-process for five seconds
 * (`FRD-209`), so a grant made a moment ago is real in Management and not yet true in the data
 * plane. Polled rather than slept: a fixed wait is either too short on a loaded machine or wasted
 * on an idle one, and both read as flakiness.
 *
 * Asked with the cheapest thing that exercises the same rule — an empty dry run, which reaches the
 * membership check and stops.
 */
export async function awaitGatewayMembership(page: Page, slug: string) {
  await expect
    .poll(
      async () =>
        page.evaluate(async (slug) => {
          const token =
            sessionStorage.getItem('access_token') ?? localStorage.getItem('access_token');
          const response = await fetch('/gw/v1beta/pipeline:dryRun', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
            body: JSON.stringify({ use_case: slug, user: 'ping', pipeline: {} }),
          });
          return response.status;
        }, slug),
      { timeout: 30_000, intervals: [500] },
    )
    .not.toBe(403);
}

/** A slug that is unique per run so reruns do not collide on the unique constraint. */
/**
 * The Save/Add button of whichever form is open.
 *
 * Written when budgets, rate limits and anomaly rules moved into windows. A window's action row is
 * the dialog's, not the form's, so its submit sits **outside** the `<form>` and is tied back to it
 * by `form="…"` — which is what an action row is for, and what makes `form button[type=submit]`
 * stop finding it. Six specs would otherwise have failed for a reason that has nothing to do with
 * what they are about.
 */
export async function submitOfOpenForm(page: Page) {
  // The window wins when one is open, and it is asked for **first** rather than as one half of a
  // comma-separated selector: those resolve in document order, so a page-level form rendered above
  // the dialog would be submitted instead — and the assertion after it would still pass, because
  // both buttons say "Add".
  const inWindow = page.locator('.modal button[type="submit"]');
  return (await inWindow.count())
    ? inWindow.first()
    : page.locator('form button[type="submit"]').first();
}

export function uniqueSlug(prefix: string) {
  return `${prefix}-${Math.random().toString(36).slice(2, 8)}`;
}

/**
 * Assert that the controls of an inline form line up.
 *
 * A row of fields is bottom-aligned only as long as every field is equally tall. As soon as one
 * carries a hint under its input it grows, its control is pushed up, and the row turns into a
 * staircase — visible at a glance to a person, invisible to a DOM assertion and to jsdom, which
 * has no layout at all. Controls are grouped by the row they landed in, since these forms wrap.
 */
export async function expectFormControlsAligned(page: Page, context: string) {
  // **Wait for something to check.** Called right after a `goto`, this used to run before the
  // panel had rendered, find no rows, and pass — which is how a 43 px staircase on the access
  // panel survived a case literally named `'grant access'`. A guard that reports green about an
  // empty page is the thing it guards against, one level up.
  await page.locator('form.form-inline, .filter-row').first().waitFor({ timeout: 20_000 });

  const rows = await page.evaluate(() => {
    const out: { group: number; line: number; controls: string[] }[] = [];

    document.querySelectorAll('form.form-inline, .filter-row').forEach((row, group) => {
      // **A line is a set of flex items whose boxes overlap vertically**, not a band of pixels.
      //
      // This used to group controls by `round(top / 40)`, which is a guess at "same row" that
      // fails in exactly the case the guard exists for: a staircase **taller than the band reads
      // as two rows**. It let 12 px through once, and then 43 px on the access panel — a
      // misalignment a person spotted immediately while the suite stayed green.
      //
      // The flex items themselves always overlap on a line, however their inner controls are
      // aligned, so they are what says where the lines are; the controls are what is compared.
      const items = Array.from(row.children) as HTMLElement[];
      const lines: { top: number; bottom: number; items: HTMLElement[] }[] = [];
      for (const item of items) {
        const box = item.getBoundingClientRect();
        if (!box.width && !box.height) continue;
        const line = lines.find((l) => box.top < l.bottom && box.bottom > l.top);
        if (line) {
          line.top = Math.min(line.top, box.top);
          line.bottom = Math.max(line.bottom, box.bottom);
          line.items.push(item);
        } else {
          lines.push({ top: box.top, bottom: box.bottom, items: [item] });
        }
      }

      lines.forEach((line, index) => {
        const controls: string[] = [];
        for (const item of line.items) {
          const inside = item.matches('input, select, button')
            ? [item]
            : Array.from(
                item.querySelectorAll<HTMLElement>('input:not([type=checkbox]), select, button'),
              );
          for (const element of inside) {
            const rect = element.getBoundingClientRect();
            if (rect.width === 0) continue;
            // An info hint's trigger lives *inside a `<label>`* and therefore sits at label height
            // by construction, one line above the control it explains. Counting it as a row
            // control reports a staircase for every labelled field that carries an explanation —
            // which is not the defect this guard was written for. Excluded by *where it is*, not
            // by what it is called, so a real button placed in a row is still compared.
            if (element.tagName === 'BUTTON' && element.closest('label')) continue;
            controls.push(`${element.id || element.tagName.toLowerCase()}@${Math.round(rect.top)}`);
          }
        }
        if (controls.length > 1) out.push({ group, line: index, controls });
      });
    });
    return out;
  });

  expect(
    rows.length,
    `${context}: nothing with two controls on a line was found to compare`,
  ).toBeGreaterThan(0);

  for (const { group, line, controls } of rows) {
    const tops = controls.map((c) => Number(c.split('@')[1]));
    const spread = Math.max(...tops) - Math.min(...tops);
    expect(
      spread,
      `${context}: row group ${group}, line ${line} is a staircase — ${controls.join(', ')}`,
    ).toBeLessThanOrEqual(2);
  }
}

/**
 * Make sure a use case exists, creating it only if it really is absent.
 *
 * Deliberately not `if ((await locator.count()) === 0) create(...)`: the list loads
 * asynchronously, so that count is taken while the table is still a spinner and answers 0 for
 * every slug. The create then fails on the unique constraint and the test blames the wrong
 * thing. Wait for the list to have finished loading first, then decide.
 */
export async function ensureUseCase(page: Page, slug: string, name: string) {
  await page.goto('/use-cases');
  // Either rows or the empty state — both mean the request came back.
  await expect(page.locator('table.table').or(page.locator('.empty')).first()).toBeVisible({
    timeout: 30_000,
  });

  // Searched, not scanned. The list is paged at the server now (`FRD-208`), so "not on this page"
  // and "does not exist" are different facts — and taking the first for the second made this
  // helper try to create a use case that was already there, three pages further on.
  const search = page.locator('[data-testid="use-case-search"]');
  if (await search.count()) {
    await search.fill(slug);
    await expect(
      page
        .locator(`code:text-is("${slug}")`)
        .or(page.locator('[data-testid="use-case-no-match"]'))
        .first(),
    ).toBeVisible({ timeout: 30_000 });
  }

  if ((await page.locator(`code:text-is("${slug}")`).count()) === 0) {
    await createUseCase(page, slug, name);
  }
  return slug;
}
