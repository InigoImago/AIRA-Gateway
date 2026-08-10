import { expect, test } from '@playwright/test';
import { USERS, createUseCase, login, uniqueSlug } from './support';

/**
 * Payload retention (FRD-404) through the UI.
 *
 * The period is a promise about personal data, so it has to be visible to whoever is
 * accountable for the use case — not buried in a configuration file.
 */
test.describe('Retention', () => {
  test('a new use case keeps payloads for a week, stated on its overview', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('retention');
    await createUseCase(page, slug, 'Retention probe');

    await page.goto(`/use-cases/${slug}`);
    await expect(page.locator('text=Days of payload retention')).toBeVisible();
    await expect(page.locator('#retention-days')).toHaveValue('7');
    await expect(
      page.locator('text=/Deleted automatically once the period has passed/'),
    ).toBeVisible();
    // The distinction that makes the design defensible.
    await expect(page.locator('text=/metadata/i')).toBeVisible();
  });

  test('the period can be changed and the change is confirmed', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('change');
    await createUseCase(page, slug, 'Change probe');

    await page.goto(`/use-cases/${slug}`);
    await page.fill('#retention-days', '1');
    await page.click('button:has-text("Save storage settings")');

    await expect(page.locator('[role="status"]')).toContainText('kept for 1 day');
    await page.reload();
    await expect(page.locator('#retention-days')).toHaveValue('1');
  });

  test('an impossible period is refused before it is sent', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('range');
    await createUseCase(page, slug, 'Range probe');

    await page.goto(`/use-cases/${slug}`);
    await page.fill('#retention-days', '0');
    await expect(page.locator('.field__hint--error')).toContainText('Between 1 and 3650');

    const save = page.locator('form:has(#retention-days) button[type="submit"]');
    await expect(save).toBeDisabled();
  });
});

test.describe('Payload storage', () => {
  test('storage can be switched off entirely, with the consequence spelled out', async ({
    page,
  }) => {
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('nostore');
    await createUseCase(page, slug, 'No-store probe');

    await page.goto(`/use-cases/${slug}`);
    await expect(page.locator('#retention-days')).toBeVisible();

    await page.uncheck('input[type="checkbox"][name="store_payloads"]');
    // With nothing kept there is no period to ask for.
    await expect(page.locator('#retention-days')).toHaveCount(0);
    // Addressed by its own id rather than as "the warning on this page": the overview carries
    // more than one callout now, and a selector that means "whichever one there is" breaks the
    // next time a panel is added. It did — `FRD-603`'s consumption card.
    await expect(page.getByTestId('storage-off-warning')).toContainText(
      'Nothing a caller sends or receives is written',
    );

    await page.click('button:has-text("Save storage settings")');
    await expect(page.locator('[role="status"]')).toContainText('no longer stored');

    await page.reload();
    await expect(page.locator('input[type="checkbox"][name="store_payloads"]')).not.toBeChecked();
    await expect(page.locator('text=Payload storage')).toBeVisible();
  });

  test('storage can be switched back on and the period reappears', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('restore');
    await createUseCase(page, slug, 'Restore probe');

    await page.goto(`/use-cases/${slug}`);
    await page.uncheck('input[type="checkbox"][name="store_payloads"]');
    await page.click('button:has-text("Save storage settings")');
    await expect(page.locator('[role="status"]')).toContainText('no longer stored');

    await page.check('input[type="checkbox"][name="store_payloads"]');
    await expect(page.locator('#retention-days')).toBeVisible();
    await page.fill('#retention-days', '3');
    await page.click('button:has-text("Save storage settings")');
    await expect(page.locator('[role="status"]')).toContainText('kept for 3 day');
  });
});
