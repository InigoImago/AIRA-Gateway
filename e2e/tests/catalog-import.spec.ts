import { expect, test } from '@playwright/test';
import { USERS, login } from './support';

/**
 * Importing what the adapters already serve, and asking the vendor what it offers (`FRD-507`).
 *
 * In a browser because the two halves live in different planes: the lists come from the **gateway**
 * through the `/gw` proxy with the browser's own token, and the form they fill is Management's. No
 * component test can tell a working proxy from a stubbed one — and the stage C flow is three
 * network hops (providers → offerings → save) with a role gate on the first two.
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

  test('browses what a provider offers and takes one model into the editor', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    await page.goto('/models');

    await page.getByTestId('browse-provider-models').click();

    // The provider list is what **this gateway** is configured with, fetched over `/gw` with the
    // browser's own token. A hard-coded vocabulary would render identically and mean something
    // else — which is the whole reason this test is in a browser.
    const provider = page.getByTestId('browse-provider');
    await expect(provider).toBeVisible({ timeout: 20_000 });
    // Chosen, not assumed: a stack with more than one askable provider preselects none, which is
    // the whole reason the preselect is conditional. Written first without this and it failed
    // exactly there — against a gateway that has three.
    await provider.selectOption('mock');
    await expect(page.getByTestId('offerings-count')).toBeVisible({ timeout: 20_000 });

    // The list itself, not a dropdown: one real key answered with 50 models.
    await page.getByTestId('offered-mock-1').click();

    await expect(page.locator('#model-name')).toHaveValue('mock-1');
    // Copied, because the vendor stated them; the console names what it took.
    await expect(page.getByTestId('vendor-filled')).toContainText('Filled in from mock');
    // Left, because a price nobody set is not zero and a capability is a measurement.
    await expect(page.locator('#model-input')).toHaveValue('');
    await expect(page.locator('#model-output')).toHaveValue('');
    await expect(page.getByTestId('model-approved')).not.toBeChecked();
  });

  test('names the vendor in the editor rather than only its routing identifier', async ({
    page,
  }) => {
    await login(page, USERS.globalAdmin);
    await page.goto('/models');
    await page.getByTestId('add-model').click();

    const provider = page.getByTestId('provider-select');
    await expect(provider).toBeVisible({ timeout: 20_000 });
    await provider.selectOption('mock');

    // Cataloguing a model under this provider is enough to reach it (`FRD-507` stage B), and the
    // form says so — the field that separates a working import from a convincing decoration.
    await expect(page.getByTestId('provider-note')).toContainText('enough to reach it');
  });

  test('does not offer the vendor question to a role that may not declare a model', async ({
    page,
  }) => {
    // IT Security investigates across every use case and writes nothing (PRD §154). The editor is
    // Global-Admin-only, so the question is whether the *screen* stops before the control — an
    // action nobody can carry out is worse than an absent one (`FRD-206`).
    await login(page, USERS.security);
    await page.goto('/models');

    await expect(page.getByRole('heading', { name: 'Models & prices' })).toBeVisible();
    await expect(page.getByTestId('add-model')).toHaveCount(0);
    await expect(page.getByTestId('browse-provider-models')).toHaveCount(0);
    await expect(page.getByTestId('discover-models')).toHaveCount(0);
  });
});
