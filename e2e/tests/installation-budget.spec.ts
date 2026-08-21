import { expect, test } from '@playwright/test';
import { USERS, expectNoHorizontalOverflow, login, logout, submitOfOpenForm } from './support';

/**
 * The installation's own budget (`FRD-610` §3.1) through the real browser and the real stack.
 *
 * What only this layer can show: the **read/write split is two different Keycloak sessions**, and
 * both halves survive the round trip through the realm, the token, the DRF permission and the
 * console's own predicate. The hermetic spec constructs roles directly, so it can prove the
 * component offers the right controls — it cannot prove that `itgov` actually arrives carrying
 * `it-steuerung`, which is the half that has been wrong before (`LESSONS.md` §7: a comparison that
 * answers the same for both sides proves neither).
 */

/**
 * Remove whatever this run left behind, so a re-run starts from the same place.
 *
 * **The id is read first and waited on afterwards.** Written as a loop over
 * `…first()`, this raced its own page: the panel reloads after each removal, so the button
 * detached mid-click and Playwright retried against an element that no longer existed. Taking one
 * row's testid and waiting for *that* row to disappear is a wait for the thing rather than a
 * sample of it — `LESSONS.md` §7, in a test I wrote a day after adding that line.
 */
async function clearLimits(page: import('@playwright/test').Page) {
  await page.goto('/reporting');
  const card = page.locator('[data-testid="installation-budget"]');
  await expect(card).toBeVisible();
  for (;;) {
    const remove = card.locator('button[data-testid^="remove-installation-budget-"]').first();
    if ((await remove.count()) === 0) break;
    const testid = await remove.getAttribute('data-testid');
    page.once('dialog', (dialog) => dialog.accept());
    await remove.click();
    await expect(page.locator(`[data-testid="${testid}"]`)).toHaveCount(0, { timeout: 15_000 });
  }
}

test.describe('Installation budget', () => {
  test('a Global Administrator sets it; governance sees it and is offered nothing', async ({
    page,
  }) => {
    await login(page, USERS.globalAdmin);
    await clearLimits(page);

    const card = page.locator('[data-testid="installation-budget"]');
    // Nothing configured says so in words, because a blank card and "no limit" look the same.
    await expect(card.getByTestId('no-installation-budget')).toBeVisible();

    await card.getByTestId('add-installation-budget').click();
    // The console's pattern: everything that creates, creates in a window.
    await expect(page.locator('[data-testid="installation-budget-editor"]')).toBeVisible();
    await page.selectOption('#installation-period', 'month');
    await page.fill('#installation-cost', '20.00');
    await (await submitOfOpenForm(page)).click();

    await expect(card).toContainText('$20.000000');
    await expect(card.getByTestId('no-installation-budget')).toHaveCount(0);
    await expectNoHorizontalOverflow(page, 'reporting with an installation budget');

    // The same figure, a different session. `itgov` carries `it-steuerung` only.
    await logout(page);
    await login(page, USERS.governance);
    await page.goto('/reporting');
    const readOnly = page.locator('[data-testid="installation-budget"]');
    await expect(readOnly).toContainText('$20.000000');
    await expect(readOnly.getByTestId('add-installation-budget')).toHaveCount(0);
    await expect(
      readOnly.locator('button[data-testid^="remove-installation-budget-"]'),
    ).toHaveCount(0);

    // And the positive half of the comparison, in the same test: an administrator is still
    // offered the control, so "offered nothing" is about the role and not about the page.
    await logout(page);
    await login(page, USERS.globalAdmin);
    await page.goto('/reporting');
    await expect(
      page.locator('[data-testid="installation-budget"] [data-testid="add-installation-budget"]'),
    ).toBeVisible();
    await clearLimits(page);
  });

  test('a use-case user is not shown the card at all', async ({ page }) => {
    // Not a refusal: what the installation spends on its own diagnostics is nothing a use-case
    // member can act on, and a 403 would tell them there is something here to want.
    await login(page, USERS.useCaseUser);
    await page.goto('/reporting');

    await expect(page.locator('h2:has-text("Reporting")')).toBeVisible();
    await expect(page.locator('[data-testid="installation-budget"]')).toHaveCount(0);
  });
});
