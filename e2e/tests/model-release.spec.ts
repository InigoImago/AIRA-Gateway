import { expect, test } from '@playwright/test';
import { USERS, login } from './support';

/**
 * Which models a use case may call (`FRD-308`).
 *
 * In a browser because the decision crosses both planes and a queue: the console writes it to
 * Management, an event carries it to the gateway's read-model, and the gateway enforces it at
 * every hop of routing and fallback. A component test can prove the checkbox works and nothing
 * about whether the model is actually refused.
 */
test.describe('Model release', () => {
  test('says which models a use case may call, and names the empty case', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    await page.goto('/use-cases/kundenservice');

    const panel = page.getByText('Models this use case may call');
    await expect(panel).toBeVisible({ timeout: 20_000 });
    await panel.scrollIntoViewIfNeeded();

    // Either state is a real answer; what must never happen is neither being said.
    const released = page.getByTestId('release-summary');
    const nothing = page.getByTestId('nothing-released');
    expect((await released.count()) + (await nothing.count())).toBe(1);

    if (await nothing.count()) {
      await expect(nothing).toContainText('cannot call anything');
    } else {
      await expect(released).toContainText('and no others');
    }
  });

  test('releases a model and takes it away again', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    await page.goto('/use-cases/personalwesen');
    await page.getByText('Models this use case may call').scrollIntoViewIfNeeded();

    // Driven through the picker, keyboard first — which is the half a mouse-only test never
    // reaches, and the half with no fallback underneath it.
    const search = page.getByTestId('release-picker-search');
    await expect(search).toBeVisible({ timeout: 20_000 });
    const before = await page.getByTestId('release-picker-chosen').locator('.chip').count();

    await search.click();
    await search.press('ArrowDown');
    await search.press('Enter');
    // Opening is not moving: ArrowDown into a closed list lands on the **first** option, so
    // exactly one thing changed.
    await expect(page.getByTestId('release-picker-chosen').locator('.chip')).toHaveCount(
      before === 0 ? 1 : before === 1 ? 0 : before - 1,
    );

    // Escape first, deliberately: the list floats over what follows and stays open so several
    // models can be picked in one go, so a reader closes it before reaching for Save. That is the
    // combobox contract, and asserting it here is what keeps Escape working.
    await search.press('Escape');
    await page.getByTestId('save-release').click();
    // The page's **one** banner (`role="status"`), not any callout: this screen also carries a
    // standing warning about payload storage, and matching both would make the assertion pass on
    // text that was already there before the click.
    await expect(page.locator('[role="status"]')).toContainText(/released|refused/, {
      timeout: 20_000,
    });

    // Put it back, so the run leaves the demo as it found it.
    await search.click();
    await search.press('ArrowDown');
    await search.press('Enter');
    await search.press('Escape');
    await page.getByTestId('save-release').click();
    await expect(page.getByTestId('save-release')).toBeDisabled({ timeout: 20_000 });
  });

  test('offers no way to change it to somebody who only oversees', async ({ page }) => {
    // IT Steuerung sees every figure and writes nothing anywhere (PRD §154). Read-only means
    // **inert, not un-saveable**: the list is still worth reading (`FRD-206`).
    await login(page, USERS.governance);
    await page.goto('/use-cases/kundenservice');

    await expect(page.getByTestId('release-readonly')).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId('save-release')).toHaveCount(0);
  });
});

test.describe('The pipeline builder is bounded by the release', () => {
  test('offers only released models, and says so when there are none', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    await page.goto('/use-cases/kundenservice/pipeline');
    await expect(page.locator('.node--step').first()).toBeVisible({ timeout: 20_000 });

    await page.click('button:has-text("+ Model Routing (LLM)")');
    // By what the node *is*, not by position: `.last()` picked whichever node the graph happened
    // to render last, which is not the one just added.
    await page.locator('.node--step').filter({ hasText: 'Model Routing' }).click();

    // A `<select>`, not a text box: free text offered exactly what the server refuses, and here it
    // also invited naming a model this use case has no right to (`FRD-308`).
    const classifier = page.locator('#insp-route-model');
    await expect(classifier).toBeVisible();
    const options = await classifier.locator('option').allTextContents();
    expect(options.length).toBeGreaterThan(1);
    // Every option is something the use case may call — asserted against the release itself.
    const released = await page.evaluate(async () => {
      const token = sessionStorage.getItem('access_token') ?? localStorage.getItem('access_token');
      const uc = await (
        await fetch('/api/v1/use-cases/kundenservice/', {
          headers: { Authorization: `Bearer ${token}` },
        })
      ).json();
      return uc.allowed_models as string[];
    });
    for (const option of options.slice(1)) expect(released).toContain(option);
  });

  test('a dry run follows the membership rule a request does', async ({ page }) => {
    /**
     * A Global Administrator is deliberately a member of nothing (`ADR-0007`), and a dry run calls
     * a real model charged to the use case — so it gets the same answer a request would. Asserted
     * because the console has to *say* this rather than sending somebody to check the gateway's
     * OIDC configuration, which is working.
     */
    await login(page, USERS.globalAdmin);
    const status = await page.evaluate(async () => {
      const token = sessionStorage.getItem('access_token') ?? localStorage.getItem('access_token');
      const response = await fetch('/gw/v1beta/pipeline:dryRun', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ use_case: 'kundenservice', user: 'hi', pipeline: {} }),
      });
      return { code: response.status, body: await response.text() };
    });

    expect(status.code).toBe(403);
    expect(status.body).toContain('Not a member');
  });

  test('a dry run refuses a model the use case may not call', async ({ page }) => {
    /**
     * The escape this closed, driven through the browser: the dry run runs the real engine, so an
     * LLM-backed step calls a real model — and it used to do that for any model named in the body,
     * with no use case, no release check and no audit row.
     */
    // `ucadmin` administers `kundenservice`, so the membership rule passes and the *release* rule
    // is what answers — which is the one under test.
    await login(page, USERS.useCaseAdmin);
    const status = await page.evaluate(async () => {
      const token = sessionStorage.getItem('access_token') ?? localStorage.getItem('access_token');
      const response = await fetch('/gw/v1beta/pipeline:dryRun', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({
          use_case: 'kundenservice',
          user: 'hi',
          pipeline: {
            steps: [
              {
                type: 'model_route',
                config: { model: 'definitely-not-released', categories: [] },
              },
            ],
          },
        }),
      });
      return { code: response.status, body: await response.text() };
    });

    expect(status.code).toBe(400);
    expect(status.body).toContain('definitely-not-released');
  });
});
