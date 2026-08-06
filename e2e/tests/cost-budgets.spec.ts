import { expect, test } from '@playwright/test';
import { USERS, createUseCase, login, logout, uniqueSlug } from './support';

/**
 * Cost-based budgeting (FRD-403) through the real UI.
 *
 * The point being guarded: a budget expressed in tokens says nothing about spend, so the tab now
 * leads with money — and consumption that cannot be priced must never read as zero.
 */
test.describe('Cost budgets', () => {
  test('a spend limit can be set and is shown as money', async ({ page }) => {
    await login(page, USERS.useCaseAdmin);
    const slug = uniqueSlug('spend');
    await createUseCase(page, slug, 'Spend probe');

    await page.goto(`/use-cases/${slug}?tab=budgets`);
    await page.click('button:has-text("Add budget")');
    await page.fill('#budget-cost', '250.00');
    await page.click('form button[type="submit"]');

    await expect(page.locator('text=/\\/ 250.000000/')).toBeVisible();
    await expect(page.locator('[role="progressbar"]')).toHaveCount(1);
  });

  test('a budget still requires at least one limit, and refuses a non-amount', async ({ page }) => {
    await login(page, USERS.useCaseAdmin);
    const slug = uniqueSlug('invalid');
    await createUseCase(page, slug, 'Invalid probe');

    await page.goto(`/use-cases/${slug}?tab=budgets`);
    await page.click('button:has-text("Add budget")');
    await expect(page.locator('.field__hint--error')).toContainText('Set a spend limit');

    await page.fill('#budget-cost', 'viel');
    await expect(page.locator('.field__hint--error')).toContainText('must be an amount');
    await expect(page.locator('form button[type="submit"]')).toBeDisabled();
  });

  test('the catalog lists prices and flags models that cannot be costed', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    await page.goto('/models');

    await page.click('button:has-text("Add model")');
    const model = uniqueSlug('priced');
    await page.fill('#model-name', model);
    await page.fill('#model-input', '0.075');
    await page.fill('#model-output', '0.30');
    await page.click('form button[type="submit"]');

    await expect(page.locator(`code:has-text("${model}")`)).toBeVisible();
    await expect(page.locator('table')).toContainText('0.075');

    // A model without a price is catalogued but marked, because its traffic cannot be costed.
    await page.click('button:has-text("Add model")');
    const unpriced = uniqueSlug('unpriced');
    await page.fill('#model-name', unpriced);
    await page.click('form button[type="submit"]');
    // Targeted by text rather than by position: the catalog now also flags models nobody has
    // *declared* (FRD-114), so `.first()` would assert about whichever badge happens to render
    // earlier — which is a fact about the column order, not about pricing.
    await expect(page.locator('.badge--warning', { hasText: 'no price' }).first()).toBeVisible();
    await expect(
      page.locator('.callout--warning', { hasText: 'left out of every spend figure' }),
    ).toBeVisible();
  });

  test('a one-sided price is refused before it can distort a figure', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    await page.goto('/models');
    await page.click('button:has-text("Add model")');
    await page.fill('#model-name', uniqueSlug('half'));
    await page.fill('#model-input', '1.00');

    await expect(page.locator('.field__hint--error')).toContainText('both');
    await expect(page.locator('form button[type="submit"]')).toBeDisabled();
  });

  test('only a global admin can change prices', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    await page.goto('/models');
    await expect(page.locator('button:has-text("Add model")')).toBeVisible();
    await logout(page);

    await login(page, USERS.useCaseAdmin);
    await page.goto('/models');
    // The catalog is readable — the budget figures depend on it — but not editable.
    await expect(page.locator('h2')).toContainText('Models');
    await expect(page.locator('button:has-text("Add model")')).toHaveCount(0);
    await expect(page.locator('[aria-label^="Remove "]')).toHaveCount(0);
  });
});
