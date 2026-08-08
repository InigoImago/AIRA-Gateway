import { expect, test } from '@playwright/test';
import { USERS, createUseCase, login, uniqueSlug } from './support';

/**
 * A key can be given an end date, from the console (`ADR-0015`).
 *
 * The Python tests prove the gateway stops honouring an expired key and the unit tests prove the
 * component passes the value. This proves the thing neither can: that a person administering a
 * use case is *offered* the choice, and that what they type survives the round trip. A capability
 * the server has and the console never surfaces is a capability nobody uses — which is the
 * complaint `FRD-206` was written about.
 */
test.describe('API key expiry', () => {
  test('a key issued with a lifetime shows its end date', async ({ page }) => {
    await login(page, USERS.useCaseAdmin);
    const slug = await createUseCase(page, uniqueSlug('expiry'), 'Key expiry');

    await page.goto(`/use-cases/${slug}?tab=keys`);
    await page.click('button:has-text("+ Issue key")');
    await page.fill('#key-label', 'e2e-expiring');
    await page.fill('#key-expiry', '30');
    await page.click('button[type="submit"]:has-text("Issue")');

    // The plaintext is revealed once. Asserting it keeps this test honest about having actually
    // issued something rather than about a row that was already there.
    await expect(page.getByText(/shown only once/i)).toBeVisible({ timeout: 30_000 });
    await page.click('button:has-text("Done")');

    const row = page.locator('tr', { hasText: 'e2e-expiring' });
    await expect(row).toBeVisible();
    await expect(row).toContainText(/\d{4}-\d{2}-\d{2}/);
  });

  test('leaving the field empty still produces a bounded key', async ({ page }) => {
    /**
     * This test asserted the opposite until the bound landed — that an empty field meant "never".
     * It is the case that decides whether anybody has to *remember* to set a lifetime, and nobody
     * does, so the server fills in the configured default and the row shows a date either way.
     */
    await login(page, USERS.useCaseAdmin);
    const slug = await createUseCase(page, uniqueSlug('default'), 'Key with the default life');

    await page.goto(`/use-cases/${slug}?tab=keys`);
    await page.click('button:has-text("+ Issue key")');
    await page.fill('#key-label', 'e2e-default');
    await page.click('button[type="submit"]:has-text("Issue")');
    await expect(page.getByText(/shown only once/i)).toBeVisible({ timeout: 30_000 });
    await page.click('button:has-text("Done")');

    const row = page.locator('tr', { hasText: 'e2e-default' });
    await expect(row).toContainText(/\d{4}-\d{2}-\d{2}/);
    // And the console does not offer the reader something the server refuses: no key issued from
    // here can say "never".
    await expect(row).not.toContainText(/no end date/i);
  });

  test('the form states the policy the server enforces', async ({ page }) => {
    /** A number the console invents would be confidently wrong the first time an installation
     *  changed the setting — and the reader would then face a refusal they cannot explain. */
    await login(page, USERS.useCaseAdmin);
    const slug = await createUseCase(page, uniqueSlug('policy'), 'Key policy shown');

    await page.goto(`/use-cases/${slug}?tab=keys`);
    await page.click('button:has-text("+ Issue key")');

    await expect(page.getByText(/keys always expire/i)).toBeVisible();
    await expect(page.locator('#key-expiry')).toHaveAttribute('placeholder', '30');
  });
});
