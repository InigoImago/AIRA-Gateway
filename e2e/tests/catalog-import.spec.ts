import { expect, test } from '@playwright/test';
import { USERS, login } from './support';

/**
 * Importing what the adapters already serve (`FRD-507`).
 *
 * In a browser because the two halves live in different planes: the list comes from the **gateway**
 * through the `/gw` proxy with the browser's own token, and the form it fills is Management's. No
 * component test can tell a working proxy from a stubbed one.
 */
test.describe('Catalog import', () => {
  test('lists what the gateway serves and fills in only where a model lives', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    await page.goto('/models');

    await page.getByTestId('discover-models').click();
    await expect(page.getByTestId('discover-summary')).toBeVisible({ timeout: 20_000 });

    // Something served and not catalogued, or the run has nothing to prove. `mock-1` is always
    // there in a local stack and is never in the catalog — it is a test double (`FRD-307`).
    const row = page.locator('tr').filter({ hasText: 'mock-1' }).first();
    await expect(row).toBeVisible();

    await page.getByTestId('import-mock-1').click();

    await expect(page.locator('#model-name')).toHaveValue('mock-1');
    // The boundary: a price nobody set is not zero, and a capability is a measurement.
    await expect(page.locator('#model-input')).toHaveValue('');
    await expect(page.getByTestId('model-approved')).not.toBeChecked();
  });
});
