import { expect, test } from '@playwright/test';
import { USERS, createUseCase, login, logout, uniqueSlug } from './support';

test.describe('Use-case management', () => {
  test('creates a use case, lands on its settings, and adds a member', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('flow');
    await createUseCase(page, slug, 'Flow probe');

    // Regression (zoneless): state reset from the HTTP callback has to schedule a re-render. The
    // window used to be a form that kept the submitted text; now the proof is that the window is
    // gone and the page moved on.
    await expect(page.locator('[role="dialog"]')).toHaveCount(0);
    await expect(page.locator('h2')).toContainText('Flow probe');

    // Adding somebody moved into the access panel with `FRD-209`: a grant names a **group or a
    // person**, and the picker is how either is chosen. Typing a username into a bare field was
    // the shape that let a grant name somebody who does not exist.
    await page.goto(`/use-cases/${slug}?tab=members`);
    await page.fill('[data-testid="access-search"]', 'ucuser');
    const results = page.locator('[data-testid="access-results"]');
    await expect(results).toBeVisible({ timeout: 20_000 });
    await results.locator('button').first().click();
    await page.click('[data-testid="access-grant"]');

    await expect(page.locator('table')).toContainText('ucuser');
    await expect(page.locator('[role="status"]')).toContainText('granted');
  });

  test('a name that matches nobody cannot be granted at all', async ({ page }) => {
    // This used to submit and read back the server's refusal. `FRD-209` moved the boundary
    // earlier: a grant names something **chosen from the directory**, so a name that matches
    // nothing never reaches a request. That is the better failure — the earlier one was a round
    // trip to be told what the picker already knew.
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('unknown');
    await createUseCase(page, slug, 'Unknown member probe');

    await page.goto(`/use-cases/${slug}?tab=members`);
    await page.fill('[data-testid="access-search"]', 'nobody-here-at-all');

    await expect(page.locator('[data-testid="access-no-match"]')).toBeVisible({ timeout: 20_000 });
    await expect(page.locator('[data-testid="access-grant"]')).toBeDisabled();
  });

  test('issues a key once and revokes it after confirmation', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('keys');
    await createUseCase(page, slug, 'Key probe');

    await page.goto(`/use-cases/${slug}?tab=keys`);
    await page.click('button:has-text("Issue key")');
    // Said **before** the button (`FRD-604`): everything done with this key is recorded under the
    // issuer's name, and the person clicking is the one who has to know it. In an agentic use case
    // this is the whole accountability chain — an agent goes wrong, and the credential leads to a
    // person. Asserted in a real browser because that is where the sentence either exists or does
    // not; a component test cannot tell a shipped template from a stale build.
    await expect(page.getByTestId('key-responsibility')).toContainText('your name');
    await page.fill('#key-label', 'e2e');
    await page.click('form button[type="submit"]');

    const secret = page.locator('.secret');
    await expect(secret).toBeVisible();
    await expect(page.getByTestId('key-issued-responsibility')).toContainText(
      'attributed to your name',
    );
    const plaintext = (await secret.textContent())?.trim() ?? '';
    expect(plaintext).toMatch(/^aira_[0-9a-f]+_[0-9a-f]+$/);
    await expect(page.locator('table')).toContainText('active');

    // Revoking asks first; declining must leave the key alone.
    page.once('dialog', (dialog) => dialog.dismiss());
    await page.click('button[aria-label^="Revoke key"]');
    await expect(page.locator('table')).toContainText('active');

    page.once('dialog', (dialog) => dialog.accept());
    await page.click('button[aria-label^="Revoke key"]');
    await expect(page.locator('table')).toContainText('revoked');
    await expect(page.locator('[role="status"]')).toContainText('revoked');
  });

  test('the plaintext key is never returned again', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('once');
    await createUseCase(page, slug, 'Once probe');

    await page.goto(`/use-cases/${slug}?tab=keys`);
    await page.click('button:has-text("Issue key")');
    await page.click('form button[type="submit"]');
    await expect(page.locator('.secret')).toBeVisible();

    await page.reload();
    await expect(page.locator('.secret')).toHaveCount(0);
    await expect(page.locator('table')).toContainText('aira_');
    await expect(page.locator('table')).not.toContainText(/aira_[0-9a-f]+_[0-9a-f]{20,}/);
  });

  test('a governance role sees every use case but cannot mint a key for one', async ({ page }) => {
    // ADR-0007: organisation-wide read visibility must not imply data-plane access.
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('gov');
    await createUseCase(page, slug, 'Governance probe');
    await logout(page);

    await login(page, USERS.governance);
    await page.goto(`/use-cases/${slug}?tab=keys`);
    await expect(page.locator('h2')).toContainText('Governance probe');

    // The console does not offer it at all any more (FRD-206): an action nobody can carry out
    // reads as a broken system, not as a boundary. It says who may, instead.
    await expect(page.locator('button:has-text("Issue key")')).toHaveCount(0);
    await expect(page.locator('[data-testid="keys-readonly"]')).toContainText('members');
    await expect(page.locator('.secret')).toHaveCount(0);
  });

  test('budget limits are shown even when consumption is present', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('budget');
    await createUseCase(page, slug, 'Budget probe');

    await page.goto(`/use-cases/${slug}?tab=budgets`);
    await page.click('button:has-text("Add budget")');
    await page.fill('#budget-tokens', '1000');
    await page.click('form button[type="submit"]');

    await expect(page.locator('text=/0 \\/ 1000/')).toBeVisible();
    await expect(page.locator('[role="progressbar"]')).toHaveCount(1);
  });

  test('an invalid technical id is refused before the request is sent', async ({ page }) => {
    // The creation window is a Global Administrator's (`ADR-0017`).
    await login(page, USERS.globalAdmin);
    await page.goto('/use-cases');
    await page.click('button:has-text("New use case")');
    await page.fill('#uc-name', 'Invalid');
    await page.fill('#uc-slug', 'Not A Slug');

    await expect(page.locator('#uc-slug-error')).toContainText('Lowercase letters');
    await expect(page.locator('button[type="submit"][form="uc-create-form"]')).toBeDisabled();
  });
});
