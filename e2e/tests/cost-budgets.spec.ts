import { expect, test } from '@playwright/test';
import {
  USERS,
  createUseCase,
  login,
  logout,
  openEditorTab,
  removeModel,
  submitOfOpenForm,
  uniqueSlug,
} from './support';

/**
 * Cost-based budgeting (FRD-403) through the real UI.
 *
 * The point being guarded: a budget expressed in tokens says nothing about spend, so the tab now
 * leads with money — and consumption that cannot be priced must never read as zero.
 */
test.describe('Cost budgets', () => {
  test('a spend limit can be set and is shown as money', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('spend');
    await createUseCase(page, slug, 'Spend probe');

    await page.goto(`/use-cases/${slug}?tab=budgets`);
    await page.click('button:has-text("Add budget")');
    await page.fill('#budget-cost', '250.00');
    await (await submitOfOpenForm(page)).click();

    await expect(page.locator('text=/\\/ 250.000000/')).toBeVisible();
    await expect(page.locator('[role="progressbar"]')).toHaveCount(1);
  });

  test('a budget still requires at least one limit, and refuses a non-amount', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('invalid');
    await createUseCase(page, slug, 'Invalid probe');

    await page.goto(`/use-cases/${slug}?tab=budgets`);
    await page.click('button:has-text("Add budget")');
    await expect(page.locator('.field__hint--error')).toContainText('Set a spend limit');

    await page.fill('#budget-cost', 'viel');
    await expect(page.locator('.field__hint--error')).toContainText('must be an amount');
    await expect(await submitOfOpenForm(page)).toBeDisabled();
  });

  test('the catalog lists prices and flags models that cannot be costed', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    await page.goto('/models');

    await page.click('button:has-text("Add model")');
    const model = uniqueSlug('priced');
    await page.fill('#model-name', model);
    // The name is on Identity and the prices are on Price — three tabs since 2026-08-18, because
    // eighteen fields in one column interleaved what a model *is* with what it *costs*.
    await openEditorTab(page, 'price');
    await page.fill('#model-input', '0.075');
    await page.fill('#model-output', '0.30');
    // Adding a model requires having *looked* first (`FRD-506`): the catalog is what the gateway
    // enforces, and "I did not know it was unreachable" is the one outcome a single button can
    // rule out. What the answer *is* does not block — declaring a model before its credential
    // arrives is the ordinary order of work.
    await page.click('[data-testid="editor-check"]');
    await page.click('button[type="submit"][form="model-editor-form"]');

    await page.getByTestId('model-search').fill(model);
    await expect(page.locator(`code:has-text("${model}")`)).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('table')).toContainText('0.075');

    // A model without a price is catalogued but marked, because its traffic cannot be costed.
    await page.click('button:has-text("Add model")');
    const unpriced = uniqueSlug('unpriced');
    await page.fill('#model-name', unpriced);
    // Adding a model requires having *looked* first (`FRD-506`): the catalog is what the gateway
    // enforces, and "I did not know it was unreachable" is the one outcome a single button can
    // rule out. What the answer *is* does not block — declaring a model before its credential
    // arrives is the ordinary order of work.
    await page.click('[data-testid="editor-check"]');
    await page.click('button[type="submit"][form="model-editor-form"]');

    // Searched for, not scrolled to. The catalog is paged, and an installation with a few hundred
    // models puts a freshly added one well off the first page — the same thing that happened to
    // the rules tests the day paging landed there.
    await page.getByTestId('model-search').fill(unpriced);
    const row = page.locator('tr', { hasText: unpriced });
    await expect(row).toBeVisible({ timeout: 15_000 });
    // Targeted by text rather than by position: the catalog now also flags models nobody has
    // *declared* (FRD-114), so `.first()` would assert about whichever badge happens to render
    // earlier — which is a fact about the column order, not about pricing.
    await expect(row.locator('.badge--warning', { hasText: 'no price' })).toBeVisible();
    await expect(
      page.locator('.callout--warning', { hasText: 'left out of every spend figure' }),
    ).toBeVisible();

    // Removed again, both of them. This suite used to leave every model it catalogued behind, so a
    // stack tested a few times held five entries nobody had declared — and the console's "N models
    // have no price on file" counts the whole catalogue, so the residue turns a real figure into
    // noise. Cleaning up is part of the test, not tidiness.
    await removeModel(page, model);
    await removeModel(page, unpriced);
  });

  test('a one-sided price is refused before it can distort a figure', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    await page.goto('/models');
    await page.click('button:has-text("Add model")');
    await page.fill('#model-name', uniqueSlug('half'));
    await openEditorTab(page, 'price');
    await page.fill('#model-input', '1.00');

    await expect(page.locator('.field__hint--error')).toContainText('both');
    await expect(page.locator('button[type="submit"][form="model-editor-form"]')).toBeDisabled();
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

/**
 * What a use case consumed, with **no budget at all** (`FRD-603`).
 *
 * An e2e test rather than a component one for the reason this project has paid for twice: the
 * defect was never in the arithmetic. The figures existed in `request_logs` all along and nothing
 * put them on screen, and only a browser can tell "the panel is wired" from "the panel renders".
 *
 * It runs against **`smoke-test`**, which is the use case the defect was reported on: seeded on
 * every installation, deliberately unlimited, and one `ucadmin` is in the Keycloak group for. No
 * figure is asserted — that would test the traffic — only that the figures are *there*, which is
 * exactly what was missing.
 *
 * On the **overview** since 2026-08-09: consumption is a fact about the use case, not about its
 * limits, and the overview is where somebody looks to see where it stands.
 */
test.describe('Consumption without a budget', () => {
  test('an unlimited use case still shows what it has used', async ({ page }) => {
    await login(page, USERS.useCaseAdmin);
    await page.goto('/use-cases/smoke-test?tab=overview');

    const card = page.getByTestId('consumption');
    await expect(card).toBeVisible();
    // Both windows: a month figure alone cannot say whether something is running away right now.
    await expect(card).toContainText('This month');
    await expect(card).toContainText('Today');
    await expect(page.getByTestId('consumption-month-tokens')).toBeVisible();
    await expect(page.getByTestId('consumption-month-cost')).toBeVisible();

    // A figure, not an em dash: the unknown states are for a gateway that could not be reached or
    // a report this caller may not fill, and neither is true here.
    await expect(page.getByTestId('consumption-month-requests')).not.toHaveText('—');
    await expect(page.getByTestId('consumption-down')).toHaveCount(0);
    await expect(page.getByTestId('consumption-scope')).toHaveCount(0);

    // And the budgets tab is unchanged by the move: no budget is still no budget, and it no
    // longer has to carry a figure that was never about limits.
    await page.goto('/use-cases/smoke-test?tab=budgets');
    await expect(page.locator('text=No budgets yet')).toBeVisible();
    await expect(page.getByTestId('consumption')).toHaveCount(0);
  });

  /**
   * The other half, and it is the one this test round actually discovered.
   *
   * Creating a use case in the console makes you its administrator in Management; it does **not**
   * put you in the Keycloak group the gateway takes membership from, because AIRA never writes to
   * the directory (`FRD-209`). So the administrator of a brand-new use case is, to the gateway,
   * nobody — and the panel says so **naming the group**, rather than showing zeroes that would
   * read as "this use case has consumed nothing".
   *
   * That distinction is the whole of `in_scope`, and this is the only test that reaches it through
   * a real token.
   */
  test('a use case the gateway does not have you in says so, rather than showing zero', async ({
    page,
  }) => {
    // Created by a Global Administrator, because only they may (`ADR-0017`) — and then **read by
    // somebody who is not oversight**, because that is the whole property: a Global Administrator
    // sees every use case's figures, so asserting this as one would assert nothing.
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('unlinked');
    await createUseCase(page, slug, 'Unlinked probe');

    await logout(page);
    await login(page, USERS.useCaseAdmin);
    await page.goto(`/use-cases/${slug}?tab=overview`);

    await expect(page.getByTestId('consumption-scope')).toContainText(`/use-cases/${slug}`);
    await expect(page.getByTestId('consumption-month-requests')).toHaveCount(0);
  });
});
