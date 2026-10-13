import { Page, expect, test } from '@playwright/test';
import { forget, remember } from '../created';
import { USERS, api, expectNoHorizontalOverflow, login, logout, submitOfOpenForm } from './support';

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
 * The installation's own budgets, as the API states them.
 *
 * They are the operator's, not this suite's: a test may add one and must remove exactly that one.
 * With one already configured, the half of this test that asks for "nothing configured" cannot be
 * asked — and adding one for the same period would **overwrite** it, since a period is all that
 * tells two apart — so an existing one is a precondition, checked before anything is changed.
 */
async function installationBudgets(page: Page): Promise<{ id: number }[]> {
  const listed = await api<{ id: number }[]>(page, 'GET', '/api/v1/installation-budgets/');
  expect(listed.status).toBe(200);
  return listed.body;
}

test.describe('Installation budget', () => {
  test('a Global Administrator sets it; governance sees it and is offered nothing', async ({
    page,
  }) => {
    await login(page, USERS.globalAdmin);
    await page.goto('/reporting');
    const card = page.locator('[data-testid="installation-budget"]');
    await expect(card).toBeVisible();
    test.skip(
      (await installationBudgets(page)).length > 0,
      'this installation has an installation budget of its own, which this test must not touch',
    );
    // Nothing configured says so in words, because a blank card and "no limit" look the same.
    await expect(card.getByTestId('no-installation-budget')).toBeVisible();

    await card.getByTestId('add-installation-budget').click();
    // The console's pattern: everything that creates, creates in a window.
    await expect(page.locator('[data-testid="installation-budget-editor"]')).toBeVisible();
    await page.selectOption('#installation-period', 'month');
    await page.fill('#installation-cost', '20.00');
    const saved = page.waitForResponse(
      (response) =>
        response.url().includes('/api/v1/installation-budgets/') &&
        response.request().method() === 'POST',
    );
    await (await submitOfOpenForm(page)).click();
    const id = String(((await (await saved).json()) as { id: number }).id);
    remember('installation-budget', id);

    // In the installation's currency, after the amount — never a `$` of the console's own, which on
    // a EUR or CHF installation is a figure in the wrong currency.
    await expect(card).toContainText(/20\.00 [A-Z]{3}/);
    await expect(card).not.toContainText('$');
    await expect(card.getByTestId('no-installation-budget')).toHaveCount(0);
    await expectNoHorizontalOverflow(page, 'reporting with an installation budget');

    // The same figure, a different session. `itgov` carries `it-steuerung` only.
    await logout(page);
    await login(page, USERS.governance);
    await page.goto('/reporting');
    const readOnly = page.locator('[data-testid="installation-budget"]');
    await expect(readOnly).toContainText(/20\.00 [A-Z]{3}/);
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

    // Removed through the screen that offers it — this one, by its id, and nothing else.
    page.once('dialog', (dialog) => dialog.accept());
    await page.getByTestId(`remove-installation-budget-${id}`).click();
    await expect(page.getByTestId(`remove-installation-budget-${id}`)).toHaveCount(0, {
      timeout: 15_000,
    });
    forget((entry) => entry.kind === 'installation-budget' && entry.name === id);
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
