import { expect, test } from '@playwright/test';
import { USERS, createUseCase, login, uniqueSlug } from './support';

/**
 * What an incident actually does, in a real browser (`FRD-131` FR-7, `FRD-502`).
 *
 * The Python tests prove the gateway bounds the answer and the unit tests prove the component
 * builds the right query. Neither can see what this sees: whether a person investigating is
 * *offered* the controls, and whether pressing one changes the question rather than only the
 * checkbox. `FRD-206` and `FRD-207` are both about that gap — a declaration that is silently
 * inert looks identical to a working one until somebody uses it.
 */
test.describe('Traces — the controls an investigation reaches for', () => {
  test('a filter asks the server, and returns to the first page', async ({ page }) => {
    /**
     * Asserted by **watching the request**, not by counting rows: a browser-side filter over one
     * loaded page would satisfy "the right rows are on screen" and still answer a busy
     * installation with whatever happened to arrive. The `FRD-208` lesson, applied to the filter
     * this one added.
     */
    // A use-case administrator, because these two filters are everybody's. IT Security cannot
    // *create* a use case — an authority it does not have, and the console correctly does not
    // offer it — so the incident case below borrows one instead.
    await login(page, USERS.useCaseAdmin);
    const slug = await createUseCase(page, uniqueSlug('traces'), 'Trace filters');

    await page.goto(`/use-cases/${slug}?tab=traces`);
    await expect(page.getByTestId('tools-only')).toBeVisible();

    const asked = page.waitForRequest(
      (request) => request.url().includes('/traces') && request.url().includes('tools_only=true'),
    );
    await page.getByTestId('tools-only').check();
    await asked;

    // And the two are independent questions, so turning the second one on keeps the first.
    const both = page.waitForRequest(
      (request) =>
        request.url().includes('tools_only=true') && request.url().includes('mine=true'),
    );
    await page.getByTestId('mine-only').check();
    await both;
  });

  test('the address column is offered to IT Security and to nobody else', async ({ page }) => {
    /**
     * "Which machine is doing this" is the first question of an incident and the last thing a use
     * case's own members need. The server decides — the console only has to stop promising it, or
     * it is `FRD-206`'s defect again: a control that 403s the moment it is used.
     */
    // Created by the role that may create one, and read by the role that may investigate. IT
    // Security sees every use case without being a member of any — that is what oversight is.
    await login(page, USERS.useCaseAdmin);
    const slug = await createUseCase(page, uniqueSlug('addr'), 'Address column');

    await page.goto(`/use-cases/${slug}?tab=traces`);
    await expect(page.getByTestId('tools-only')).toBeVisible();
    await expect(page.getByTestId('trace-source-ip')).toHaveCount(0);

    // The same screen, as IT Security. A **fresh context**, not `clearCookies()`: Keycloak's SSO
    // session lives in its own cookie jar, so a second login in this one would silently continue
    // as the first user — and the assertion would then be about the wrong person entirely.
    const investigator = await page.context().browser()!.newContext();
    const investigatorPage = await investigator.newPage();
    await login(investigatorPage, USERS.security);
    await investigatorPage.goto(`/use-cases/${slug}?tab=traces`);
    await expect(investigatorPage.getByTestId('trace-source-ip')).toBeVisible();

    // …and the row that gained a field still reads as one row. `console-usability` measures this
    // as `it-steuerung`, who is shown five controls; IT Security is shown six, so the widest case
    // is the one no other test covers. Adding two fields to that row is exactly how `FRD-207`'s
    // finding came back the first time.
    const offset = await investigatorPage.evaluate(() => {
      const box = (selector: string) => document.querySelector(selector)!.getBoundingClientRect();
      const check = box('[data-testid="refusals-only"]');
      const select = box('#trace-outcome');
      return Math.abs(check.top + check.height / 2 - (select.top + select.height / 2));
    });
    expect(offset).toBeLessThan(4);

    await investigator.close();
  });
});

test.describe('The OpenCode configuration', () => {
  test('a just-issued key can be taken away as a working config', async ({ page }) => {
    /**
     * Built at issuance because the plaintext exists for exactly that moment. This is the layer
     * that can tell "renders a button" from "produces a file" — the distinction `FRD-206` shipped
     * a defect on when an info button was a `title` attribute and showed nothing at all.
     */
    await login(page, USERS.useCaseAdmin);
    const slug = await createUseCase(page, uniqueSlug('opencode'), 'Assistant access');

    await page.goto(`/use-cases/${slug}?tab=keys`);
    await page.click('button:has-text("+ Issue key")');
    await page.fill('#key-label', 'e2e-opencode');
    await page.click('button[type="submit"]:has-text("Issue")');
    await expect(page.getByText(/shown only once/i)).toBeVisible({ timeout: 30_000 });

    const download = page.waitForEvent('download');
    await page.getByTestId('download-opencode').click();
    const file = await download;
    expect(file.suggestedFilename()).toBe('opencode.json');

    const stream = await file.createReadStream();
    const chunks: Buffer[] = [];
    for await (const chunk of stream) chunks.push(chunk as Buffer);
    const config = JSON.parse(Buffer.concat(chunks).toString('utf8'));

    // The three things that decide whether it runs: where to talk, what to say, who is calling.
    expect(config.provider.aira.options.baseURL).toContain('/gw/v1beta');
    expect(config.provider.aira.options.apiKey).toMatch(/^aira_/);
    expect(config.model).toMatch(/^aira\//);
  });

  test('the configuration disappears with the key it carries', async ({ page }) => {
    /** It contains the credential. Leaving the buttons on screen after "Done" would mean the key
     *  is retrievable from a page that has just said it is not. */
    await login(page, USERS.useCaseAdmin);
    const slug = await createUseCase(page, uniqueSlug('once'), 'Shown once');

    await page.goto(`/use-cases/${slug}?tab=keys`);
    await page.click('button:has-text("+ Issue key")');
    await page.fill('#key-label', 'e2e-once');
    await page.click('button[type="submit"]:has-text("Issue")');
    await expect(page.getByTestId('download-opencode')).toBeVisible({ timeout: 30_000 });

    await page.click('button:has-text("Done")');

    await expect(page.getByTestId('download-opencode')).toHaveCount(0);
    await expect(page.getByTestId('copy-opencode')).toHaveCount(0);
  });
});

test.describe('Reading what was actually sent', () => {
  test('an incident role opens a request and is told the read was recorded', async ({ page }) => {
    /**
     * The layer that can tell "renders a panel" from "shows the reader the prompt". Three defects
     * in this area were invisible to every other layer: a button behind a horizontal scroll, an
     * info panel that opened empty, and a 200 rendered in red.
     */
    // Its own traffic, with its own key. A first version opened whichever row happened to be at
    // the top and failed against rows another suite had left there — no payload, dated 2031. A
    // test that depends on ambient data is flaky by construction, and worse, it is flaky in a way
    // that looks like a product defect.
    await login(page, USERS.useCaseAdmin);
    const slug = await createUseCase(page, uniqueSlug('payload'), 'Reading a prompt');
    await page.goto(`/use-cases/${slug}?tab=keys`);
    await page.click('button:has-text("+ Issue key")');
    await page.fill('#key-label', 'e2e-payload');
    await page.click('button[type="submit"]:has-text("Issue")');
    const key = await page.locator('code.secret').innerText();
    await page.click('button:has-text("Done")');

    // A key reaches the gateway over Kafka (`FRD-205`), so it is valid within a moment rather than
    // immediately. Polled rather than slept: a fixed wait is either too short on a loaded machine
    // or wasted on an idle one, and both read as flakiness.
    const send = () =>
      page.request.post('http://localhost:8001/v1beta/models/qwen3:0.6b:generateContent', {
        headers: { 'x-goog-api-key': key.trim(), 'content-type': 'application/json' },
        data: {
          contents: [{ parts: [{ text: 'A sentence this test can look for.' }] }],
          generationConfig: { maxOutputTokens: 8 },
        },
        timeout: 180_000,
      });
    await expect
      .poll(async () => (await send()).status(), { timeout: 60_000, intervals: [1000] })
      .toBe(200);

    // A **fresh context**, not `clearCookies()`. Keycloak's SSO session lives in its own cookie
    // jar, so the second login silently continues as the first user — this test asserted a
    // permission about IT Security while signed in as the use-case administrator, which is the
    // second time this trap has been walked into today. Written down here rather than remembered.
    const investigator = await page.context().browser()!.newContext();
    const investigatorPage = await investigator.newPage();
    await login(investigatorPage, USERS.security);
    await investigatorPage.goto(`/use-cases/${slug}?tab=traces`);

    const opener = investigatorPage.locator('[data-testid^="open-payload-"]').first();
    await expect(opener).toBeVisible({ timeout: 60_000 });

    // Visible **without scrolling the table sideways** — the complaint that moved it to the first
    // column. `isVisible` is not enough for that; the check is that it sits inside the viewport.
    const box = await opener.boundingBox();
    const width = investigatorPage.viewportSize()?.width ?? 1280;
    expect(box, 'the control that opens a request has no box').not.toBeNull();
    expect(box!.x + box!.width).toBeLessThanOrEqual(width);

    await opener.click();
    await expect(investigatorPage.getByTestId('payload-recorded')).toBeVisible();
    // The prompt itself, not merely a panel: the distinction three defects this week lived in.
    await expect(investigatorPage.getByTestId('payload-request')).toContainText(
      'A sentence this test can',
    );
    await investigator.close();
  });

  test('a served request is not shown as a failure', async ({ page }) => {
    /** Reported from the running console: a **200 in red**. A status column that calls a success
     *  a problem is the one thing it must never do. */
    await login(page, USERS.security);
    await page.goto('/requests');
    await expect(page.locator('tbody tr').first()).toBeVisible({ timeout: 30_000 });

    const served = page.locator('.badge--success', { hasText: 'served' });
    await expect(served.first()).toBeVisible();
  });

  test('an info hint shows something when you point at it', async ({ page }) => {
    /** The defect: `text="…"` is not an input on `InfoHint`, so three panels opened blank. No unit
     *  test could see it — the component renders whatever the test projects into it. */
    await login(page, USERS.security);
    await page.goto('/requests');

    const hint = page.locator('.info-hint__button').first();
    await expect(hint).toBeVisible({ timeout: 30_000 });
    await hint.hover();

    const panel = page.locator('.info-hint__panel').first();
    await expect(panel).toBeVisible();
    await expect(panel).not.toBeEmpty();
  });

  test('a use-case member is never offered the cross-use-case screen', async ({ page }) => {
    await login(page, USERS.useCaseAdmin);

    await expect(page.getByTestId('nav-requests')).toHaveCount(0);
  });
});

test.describe('The list holds still and fits', () => {
  test('the request table does not scroll sideways', async ({ page }) => {
    /**
     * Eleven columns scrolled horizontally, which is how the control that opens a request ended up
     * off screen. Four columns is the design; this is the assertion that keeps it one. Measured on
     * the scroller itself — the page not scrolling is a weaker property, because `.table-wrap`
     * scrolls inside itself by design and hides the overflow from the document.
     */
    await login(page, USERS.security);
    await page.goto('/requests');
    await expect(page.locator('tbody tr').first()).toBeVisible({ timeout: 30_000 });

    const overflow = await page.locator('.table-wrap').first().evaluate((el) => ({
      scroll: el.scrollWidth,
      client: el.clientWidth,
    }));

    expect(
      overflow.scroll,
      `the request table needs ${overflow.scroll}px in ${overflow.client}px`,
    ).toBeLessThanOrEqual(overflow.client + 1);
  });

  test('a search field keeps focus while it is being typed into', async ({ page }) => {
    /**
     * Reported from the running console: *"wenn ich 2 character reinschreibe, dann fängt er an zu
     * suchen und ich fliege aus dem Feld raus"*. The input sat inside the `@else` of
     * `@if (loading())`, so the query it started tore down the block that contained it.
     *
     * Only a browser can see this. The component test types into a field and asserts a request was
     * made; it has no notion of which element the caret is in.
     */
    await login(page, USERS.globalAdmin);
    await page.goto('/use-cases');

    const search = page.getByTestId('use-case-search');
    await search.click();
    await search.type('de', { delay: 120 });
    // Past the debounce, so the query has fired and the answer has come back.
    await page.waitForTimeout(1500);

    await expect(search).toBeFocused();
    await expect(search).toHaveValue('de');

    // And typing can simply continue, which is the thing that was actually broken.
    await page.keyboard.type('mo');
    await expect(search).toHaveValue('demo');
  });
});

test.describe('Authoring a rule that applies everywhere', () => {
  test('IT Security can create a global rule from the console', async ({ page }) => {
    /**
     * The capability the server has had since `FRD-500` and the console never offered — so the
     * only global rules that existed anywhere were the ones a seed had written into the database.
     * Asserted end to end because that is where the gap was: the API worked all along.
     */
    await login(page, USERS.security);
    await page.goto('/security');
    await page.click('[role="tab"]:has-text("Rules")');

    await page.getByTestId('new-global-rule').click();

    const name = `e2e global ${Date.now()}`;
    await page.locator('[data-testid="new-global-rule-name"]').fill(name);
    await page.locator('[data-testid="new-global-rule-threshold"]').fill('55');
    await page.locator('[data-testid="new-global-rule-save"]').click();

    await expect(page.locator('[role="status"]')).toContainText('every use case', {
      timeout: 15_000,
    });

    await page.getByTestId('rule-search').fill(name);
    const row = page.locator(`tr:has-text("${name}")`);
    await expect(row).toBeVisible({ timeout: 20_000 });
    await expect(row).toContainText('everywhere');

    // Removed again. A rule is a **policy**, not a row: every tick evaluates every rule, so one
    // left behind produces findings for every caller that crosses it, forever. Fifty-six of one
    // installation's sixty-one global rules turned out to be residue from runs of this suite.
    await row.locator('[data-testid^="rule-toggle-"]').click();
    // Deleting a rule asks first (`FRD-206`: destructive actions confirm), and Playwright
    // auto-*dismisses* dialogs — so without this the click does nothing and the rule survives,
    // which is how the leftovers accumulated in the first place.
    page.once('dialog', (dialog) => dialog.accept());
    await page.locator('[data-testid^="rule-delete-"]').first().click();
    await expect(page.locator(`tr:has-text("${name}")`)).toHaveCount(0, { timeout: 15_000 });
  });

  test('a role that may stop nothing is not offered the button', async ({ page }) => {
    await login(page, USERS.governance);
    await page.goto('/security');
    await page.click('[role="tab"]:has-text("Rules")');

    await expect(page.getByTestId('new-global-rule')).toHaveCount(0);
  });
});

test.describe('Navigation that hides nothing', () => {
  test('the tab strip becomes a list rather than scrolling out of view', async ({ page }) => {
    /**
     * Scrolling was the old answer and it is the wrong one for navigation: a tab that has scrolled
     * out of view is a section the reader does not know exists. Measured, not eyeballed — every
     * entry must be inside the viewport, which is the property that was actually broken.
     */
    await login(page, USERS.useCaseAdmin);
    const slug = await createUseCase(page, uniqueSlug('tabs'), 'Tab layout');
    await page.setViewportSize({ width: 900, height: 900 });
    await page.goto(`/use-cases/${slug}`);
    await expect(page.locator('[role="tab"]').first()).toBeVisible();

    const tabs = page.locator('[role="tab"]');
    const count = await tabs.count();
    expect(count, 'a strip of one proves nothing').toBeGreaterThan(3);

    for (let index = 0; index < count; index += 1) {
      const box = await tabs.nth(index).boundingBox();
      expect(box, `tab ${index} has no box`).not.toBeNull();
      expect(box!.x + box!.width, `tab ${index} runs past the viewport`).toBeLessThanOrEqual(901);
    }

    // A list, not a row: the entries sit under one another.
    const first = await tabs.nth(0).boundingBox();
    const last = await tabs.nth(count - 1).boundingBox();
    expect(last!.y).toBeGreaterThan(first!.y);
  });

  test('and stays a row where there is room for one', async ({ page }) => {
    await login(page, USERS.useCaseAdmin);
    const slug = await createUseCase(page, uniqueSlug('wide'), 'Wide tabs');
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto(`/use-cases/${slug}`);
    await expect(page.locator('[role="tab"]').first()).toBeVisible();

    const tabs = page.locator('[role="tab"]');
    const first = await tabs.nth(0).boundingBox();
    const second = await tabs.nth(1).boundingBox();

    expect(second!.y).toBe(first!.y);
  });
});
