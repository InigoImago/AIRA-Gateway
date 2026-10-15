import { Page, expect, test } from '@playwright/test';
import { USERS, createUseCase, login, uniqueSlug } from './support';

/** Accept the next confirmation and hand back what it asked. */
function acceptNextDialog(page: Page): Promise<string> {
  return new Promise((resolve) =>
    page.once('dialog', (dialog) => {
      resolve(dialog.message());
      void dialog.accept();
    }),
  );
}

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
    // Shortening the period deletes what is older on the next run, so the console asks first —
    // and Playwright dismisses a dialog unless told otherwise, which would cancel the save.
    const asked = acceptNextDialog(page);
    await page.click('button:has-text("Save storage settings")');

    expect(await asked).toContain('1 day(s) instead of 7');
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

    await page.check('[data-testid="content-mode-not-stored"]');
    // With nothing kept there is no period to ask for.
    await expect(page.locator('#retention-days')).toHaveCount(0);
    // Addressed by its own id rather than as "the warning on this page": the overview carries
    // more than one callout now, and a selector that means "whichever one there is" breaks the
    // next time a panel is added. It did — `FRD-603`'s consumption card.
    await expect(page.getByTestId('storage-off-warning')).toContainText(
      'Nothing a caller sends or receives is written',
    );

    const asked = acceptNextDialog(page);
    await page.click('button:has-text("Save storage settings")');
    expect(await asked).toContain('Stop storing prompts and responses');
    await expect(page.locator('[role="status"]')).toContainText('no longer stored');

    await page.reload();
    await expect(page.getByTestId('content-mode-not-stored')).toBeChecked();
    await expect(page.locator('text=Payload storage')).toBeVisible();
  });

  test('storage can be switched back on and the period reappears', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('restore');
    await createUseCase(page, slug, 'Restore probe');

    await page.goto(`/use-cases/${slug}`);
    await page.check('[data-testid="content-mode-not-stored"]');
    const asked = acceptNextDialog(page);
    await page.click('button:has-text("Save storage settings")');
    await asked;
    await expect(page.locator('[role="status"]')).toContainText('no longer stored');

    // Switching storage back on deletes nothing, so nothing is asked.
    // Oversight-only: a mode that stores, and the one strict data mode — the default — offers.
    await page.check('[data-testid="content-mode-oversight"]');
    await expect(page.locator('#retention-days')).toBeVisible();
    await page.fill('#retention-days', '3');
    await page.click('button:has-text("Save storage settings")');
    await expect(page.locator('[role="status"]')).toContainText('kept for 3 day');
  });
});
