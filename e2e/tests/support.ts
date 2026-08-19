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
  const inWindow = page.locator('.modal button[type="submit"]').first();
  const inPage = page.locator('form button[type="submit"]').first();

  // **Waited for, not sampled.** This used to branch on `await inWindow.count()`, which is a single
  // immediate poll taken the moment the opening click returned — so on a slow render it saw zero,
  // chose the page-level selector, and waited 45 s for something that cannot exist: a window's
  // submit button lives in the modal footer with `form="…"`, outside any `<form>`. The button was
  // on screen the whole time. Reproduced by restarting the management container first, which is
  // the only reason it ever showed up: warm, the modal wins the race every time.
  await expect(inWindow.or(inPage)).toBeVisible();
  return (await inWindow.isVisible()) ? inWindow : inPage;
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

/**
 * No control in a form has been squeezed into a column of single words.
 *
 * The catalog editor was reported as "everything packed into one row" after importing a model.
 * Nothing was misplaced: the vendor's note is a `<p class="callout grow">`, `.grow` is `flex: 1` —
 * which means `flex-basis: 0` — and an item with no basis **contributes nothing to the wrap
 * calculation**, so it never moves to a line of its own. It was squeezed instead, and `min-width:
 * 0` let that run to the end: **67 px wide and 4818 px tall**, dragging the model-id field down to
 * 30 px beside it.
 *
 * Asserted as a **ratio**, because that is the shape of the failure and not the identity of the
 * element. Any element much taller than it is wide is text wrapping one word per line, whatever
 * produced it — a note, a hint, a legend, the next thing somebody adds. A threshold of 8 is loose
 * enough for an ordinary tall field (a select with a three-line hint is about 3) and nowhere near
 * the 72 this produced.
 */
export async function expectNoSqueezedControls(page: Page, context: string) {
  await page.locator('form.form-inline, .filter-row').first().waitFor({ timeout: 20_000 });

  const squeezed = await page.evaluate(() => {
    const bad: { text: string; width: number; height: number }[] = [];
    document.querySelectorAll('form.form-inline, .filter-row').forEach((row) => {
      for (const item of Array.from(row.children) as HTMLElement[]) {
        const box = item.getBoundingClientRect();
        if (box.width < 1 || box.height < 1) continue; // hidden
        if (box.height > box.width * 8) {
          bad.push({
            text: (item.textContent ?? '').trim().slice(0, 60),
            width: Math.round(box.width),
            height: Math.round(box.height),
          });
        }
      }
    });
    return bad;
  });

  expect(
    squeezed,
    `${context}: a control is far taller than it is wide, which is text wrapping one word per line`,
  ).toEqual([]);
}

/**
 * The two decisions at the foot of a window do not touch.
 *
 * Reported on one window — "Save and Cancel are too close together" — and true of every window in
 * the console. `<ng-content select="[modal-foot]">` projects **one** element, the caller's
 * wrapper, so `.modal__foot` had a single flex item, the `gap` declared on it applied to nothing,
 * and the buttons inside a plain block sat at **0 px apart**. Measured, not guessed.
 *
 * Asserted as a minimum distance rather than as a CSS rule, because the rule is not the property:
 * mis-clicking Cancel loses what somebody typed and mis-clicking the primary commits it, and the
 * next arrangement that puts them a thumb-width apart should fail too.
 */
export async function expectFooterActionsApart(page: Page, testid: string, minimum = 8) {
  const buttons = page.locator(`[data-testid="${testid}"] .modal__foot button`);
  await buttons.first().waitFor({ timeout: 20_000 });

  const boxes = await buttons.evaluateAll((els) =>
    els.map((e) => {
      const r = e.getBoundingClientRect();
      return { text: (e.textContent ?? '').trim(), left: r.x, right: r.x + r.width };
    }),
  );

  expect(boxes.length, `${testid}: no footer buttons to compare`).toBeGreaterThan(1);
  for (let i = 1; i < boxes.length; i += 1) {
    const gap = boxes[i].left - boxes[i - 1].right;
    expect(
      gap,
      `${testid}: "${boxes[i - 1].text}" and "${boxes[i].text}" are ${Math.round(gap)} px apart`,
    ).toBeGreaterThanOrEqual(minimum);
  }
}

/**
 * Open the model editor on an empty declaration, whichever way the console gets there.
 *
 * There were two buttons on the models page — "Add from a provider…" and "+ Add model", the second
 * opening an empty form — and every spec that needed a model clicked the second. The owner's
 * verdict on the pair: *"the Add model button, where you have to type everything yourself, is by
 * now unnecessary — we have adding from a provider"*, and behind it the sharper complaint that the
 * form asked eight questions of which five were already answered by choosing a provider.
 *
 * So there is one entrance now, and it starts by asking where the model lives. That makes reaching
 * an empty form a **sequence** rather than a click, and a sequence written out in nine specs is
 * nine copies of a decision — the shape this project keeps paying for. Here it is once:
 * choose a provider that publishes no list (so the console offers to take a name), or, on an
 * installation with no upstream at all, the by-name button that replaces the offer.
 */
export async function openModelEditor(page: Page, provider = 'mock') {
  await page.getByTestId('add-model').click();

  const byName = page.getByTestId('add-by-name');
  const picker = page.getByTestId('browse-provider');
  await expect(byName.or(picker).first()).toBeVisible({ timeout: 20_000 });

  if (await byName.isVisible()) {
    await byName.click();
  } else {
    await picker.selectOption(provider);
    // Either route to a name, depending on whether this platform publishes a list — the point of
    // both being present is that neither the reader nor this helper has to know which it is.
    const manual = page.getByTestId('add-manually');
    const unlisted = page.getByTestId('name-it-yourself');
    await expect(manual.or(unlisted).first()).toBeVisible({ timeout: 20_000 });
    await ((await manual.isVisible()) ? manual : unlisted).click();
  }
  await expect(page.locator('#model-name')).toBeVisible({ timeout: 20_000 });
}

/**
 * Bring one tab of the model editor into view.
 *
 * The editor was a single column of eighteen fields until 2026-08-18 and is now three tabs —
 * identity, capabilities, price — because the fields answer three different questions and were
 * interleaved. Every spec that opened the editor and went straight to a field went red, all nine
 * of them at once, with `element(s) not found` and no hint that the element exists one click away.
 *
 * A helper rather than a click in each spec: the next tab added moves fields again, and the specs
 * that care about *what a field does* should not each carry a map of where it lives.
 */
export async function openEditorTab(page: Page, tab: 'identity' | 'capabilities' | 'price') {
  const strip = page.getByTestId(`tab-${tab}`);
  await strip.waitFor({ timeout: 20_000 });
  await strip.click();
  await expect(strip).toHaveAttribute('aria-selected', 'true');
}

/**
 * Delete a model this suite catalogued.
 *
 * Without it the browser suite **grows the catalogue on every run**: `cost-budgets.spec.ts` saved
 * two models per pass and removed neither, and a stack that had been tested a few times held five
 * `priced-…` and `unpriced-…` entries nobody had declared. That is not only untidiness — the
 * console's own warnings count over the whole catalogue ("N models have no price on file"), so
 * test residue makes a real figure meaningless, and the residue never stops accumulating.
 *
 * Tolerant of an already-absent row: a test that failed before saving must not fail again while
 * cleaning up, or the failure a reader sees is the tidying rather than the defect.
 */
export async function removeModel(page: Page, name: string) {
  await page.getByTestId('model-search').fill(name);
  // **The row has to be opened first, and waited for.** Remove lives inside the expanded row, not
  // in the list, and removing one model reloads the table — so a second call that checked
  // `count() === 0` immediately after typing found nothing, returned, and left the model behind.
  //
  // Both mistakes have the same shape and it is worth naming: *a locator that matches nothing and
  // a cleanup that is not needed look identical*. The leftovers went 12 → 13 after the run meant
  // to fix them, which is the only reason it was noticed. Wait for the row, and treat a genuine
  // absence as the one case where waiting is allowed to fail.
  const open = page.getByTestId(`open-model-${name}`);
  try {
    await open.waitFor({ state: 'visible', timeout: 10_000 });
  } catch {
    return; // already gone — the test failed before it saved, and tidying must not fail again
  }
  await open.click();
  const remove = page.getByTestId(`remove-${name}`);
  await expect(remove).toBeVisible({ timeout: 15_000 });
  page.once('dialog', (dialog) => dialog.accept());
  await remove.click();
  await expect(page.getByTestId(`open-model-${name}`)).toHaveCount(0, { timeout: 15_000 });
}
