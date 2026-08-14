import { expect, test } from '@playwright/test';
import {
  USERS,
  createUseCase,
  expectNoHorizontalOverflow,
  login,
  uniqueSlug,
  submitOfOpenForm,
} from './support';

/**
 * Request-rate limits (FRD-405) through the real UI.
 *
 * A budget says how much may be spent, a rate limit how fast. The screen has to make that
 * distinction legible, because a limit whose purpose is unclear is a limit nobody sets — and the
 * gateway cannot protect a use case whose administrator never configured one.
 */
test.describe('Rate limits', () => {
  test('a limit can be set and is listed with its effective burst', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('rate');
    await createUseCase(page, slug, 'Rate probe');

    await page.goto(`/use-cases/${slug}?tab=rate-limits`);
    await expect(page.locator('.empty')).toContainText('as fast as it likes');

    await page.click('button:has-text("Add rate limit")');
    await page.fill('#rl-rpm', '120');
    await (await submitOfOpenForm(page)).click();

    const row = page.locator('table tbody tr').first();
    await expect(row).toContainText('Whole use case');
    await expect(row).toContainText('120');
    await expect(row).toContainText('Active');
  });

  test('an unset burst is shown as the per-minute figure, not as zero', async ({ page }) => {
    // Otherwise the table reads as "nothing may arrive at once", which is not what was saved.
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('burst');
    await createUseCase(page, slug, 'Burst probe');

    await page.goto(`/use-cases/${slug}?tab=rate-limits`);
    await page.click('button:has-text("Add rate limit")');
    await page.fill('#rl-rpm', '90');
    await (await submitOfOpenForm(page)).click();

    const cells = page.locator('table tbody tr').first().locator('td');
    await expect(cells.nth(1)).toHaveText('90');
    await expect(cells.nth(2)).toHaveText('90');
  });

  test('an invalid limit is refused with a reason, not just a dead button', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('invalid-rate');
    await createUseCase(page, slug, 'Invalid rate probe');

    await page.goto(`/use-cases/${slug}?tab=rate-limits`);
    await page.click('button:has-text("Add rate limit")');
    await expect(page.locator('.field__hint--error')).toContainText('how many requests per minute');
    await expect(await submitOfOpenForm(page)).toBeDisabled();

    // Zero would switch the use case off rather than configure it.
    await page.fill('#rl-rpm', '0');
    await expect(page.locator('.field__hint--error')).toContainText('At least 1');
  });

  test('a limit can be removed again', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('remove-rate');
    await createUseCase(page, slug, 'Remove rate probe');

    await page.goto(`/use-cases/${slug}?tab=rate-limits`);
    await page.click('button:has-text("Add rate limit")');
    await page.fill('#rl-rpm', '60');
    await (await submitOfOpenForm(page)).click();
    await expect(page.locator('table tbody tr').first()).toContainText('60');

    page.once('dialog', (dialog) => dialog.accept());
    await page.click('[aria-label^="Remove the rate limit"]');
    await expect(page.locator('.empty')).toContainText('as fast as it likes');
  });

  test('the tab is deep-linkable and does not overflow its width', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('rate-layout');
    await createUseCase(page, slug, 'Rate layout probe');

    await page.goto(`/use-cases/${slug}?tab=rate-limits`);
    await expect(page.locator('#tab-rate-limits')).toHaveAttribute('aria-selected', 'true');

    await page.click('button:has-text("Add rate limit")');
    await expectNoHorizontalOverflow(page, 'rate-limits tab');
  });
});
