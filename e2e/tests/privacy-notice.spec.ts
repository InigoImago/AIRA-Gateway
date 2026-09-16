import { expect, test } from '@playwright/test';
import { USERS, login } from './support';

/**
 * The privacy notice in a real browser against the real API (`FRD-625`).
 *
 * The demo accounts have acknowledged it by the time any spec runs — `login` answers it — so the
 * window is made due by rewriting `due` on the server's own response. Everything else is real: the
 * text, the version, the languages, and the acknowledgement the server records and checks.
 */
test.describe('privacy notice', () => {
  test('is readable at any time from the footer, in every language it exists in', async ({
    page,
  }) => {
    await login(page, USERS.useCaseUser);

    await page.getByTestId('footer-privacy').click();
    await expect(page).toHaveURL(/\/privacy$/);
    await page.getByTestId('privacy-language-de').click();
    await expect(page.locator('h2')).toHaveText('Datenschutzhinweise');
    await expect(page.locator('[data-section="no_monitoring"]')).toContainText('BetrVG');
    await expect(page.locator('[data-activity="request_record"]')).toContainText(
      'Quell-IP-Adresse',
    );

    await page.getByTestId('privacy-language-en').click();
    await expect(page.locator('h2')).toHaveText('Privacy notice');
    await expect(page.locator('[data-section="access"]')).toContainText('IT Security:');
    await expect(page.getByTestId('privacy-acknowledged')).toBeVisible();
  });

  test('blocks the console until it is acknowledged, and records the acknowledgement', async ({
    page,
  }) => {
    await login(page, USERS.governance);
    await page.route('**/api/v1/privacy-notice', async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      await route.fulfill({ response, json: { ...body, due: 'month' } });
    });
    const acknowledged = page.waitForResponse(
      (r) =>
        r.url().includes('/privacy-notice/acknowledgements') && r.request().method() === 'POST',
    );

    await page.goto('/use-cases');
    const dialog = page.getByTestId('privacy-notice');
    await expect(dialog).toBeVisible();
    await expect(dialog.getByTestId('privacy-due')).not.toBeEmpty();
    // It must be answered: Escape does not close it, and there is no ✕.
    await page.keyboard.press('Escape');
    await expect(dialog).toBeVisible();
    await expect(page.getByTestId('privacy-notice-close')).toHaveCount(0);

    await dialog.getByTestId('privacy-acknowledge').click();
    expect((await acknowledged).status()).toBeLessThan(300);
    await expect(dialog).toBeHidden();
  });
});
