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
    await login(page, USERS.useCaseAdmin);
    const slug = uniqueSlug('retention');
    await createUseCase(page, slug, 'Retention probe');

    await page.goto(`/use-cases/${slug}`);
    await expect(page.locator('text=Days of payload retention')).toBeVisible();
    await expect(page.locator('#retention-days')).toHaveValue('7');
    await expect(page.locator('text=/deleted after this many days/')).toBeVisible();
    // The distinction that makes the design defensible.
    await expect(page.locator('text=/metadata/i')).toBeVisible();
  });

  test('the period can be changed and the change is confirmed', async ({ page }) => {
    await login(page, USERS.useCaseAdmin);
    const slug = uniqueSlug('change');
    await createUseCase(page, slug, 'Change probe');

    await page.goto(`/use-cases/${slug}`);
    await page.fill('#retention-days', '1');
    await page.click('button:has-text("Save")');

    await expect(page.locator('[role="status"]')).toContainText('kept for 1 day');
    await page.reload();
    await expect(page.locator('#retention-days')).toHaveValue('1');
  });

  test('an impossible period is refused before it is sent', async ({ page }) => {
    await login(page, USERS.useCaseAdmin);
    const slug = uniqueSlug('range');
    await createUseCase(page, slug, 'Range probe');

    await page.goto(`/use-cases/${slug}`);
    await page.fill('#retention-days', '0');
    await expect(page.locator('.field__hint--error')).toContainText('Between 1 and 3650');

    const save = page.locator('form:has(#retention-days) button[type="submit"]');
    await expect(save).toBeDisabled();
  });
});
