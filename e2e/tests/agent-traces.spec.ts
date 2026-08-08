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
