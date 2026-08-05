import { expect, test } from '@playwright/test';
import { USERS, createUseCase, login, logout, uniqueSlug } from './support';

test.describe('Use-case management', () => {
  test('creates a use case, adds a member, and clears the form', async ({ page }) => {
    await login(page, USERS.useCaseAdmin);
    const slug = uniqueSlug('flow');
    await createUseCase(page, slug, 'Flow probe');

    // Regression (zoneless): the inputs used to keep the submitted text after a successful POST.
    await expect(page.locator('#uc-slug')).toHaveValue('');
    await expect(page.locator('#uc-name')).toHaveValue('');

    await page.goto(`/use-cases/${slug}?tab=members`);
    await page.click('button:has-text("Add member")');
    await page.fill('#member-user', 'ucuser');
    await page.click('form button[type="submit"]');

    await expect(page.locator('table')).toContainText('ucuser');
    await expect(page.locator('[role="status"]')).toContainText('ucuser was added');
  });

  test('explains why an unknown username cannot be added', async ({ page }) => {
    await login(page, USERS.useCaseAdmin);
    const slug = uniqueSlug('unknown');
    await createUseCase(page, slug, 'Unknown member probe');

    await page.goto(`/use-cases/${slug}?tab=members`);
    await page.click('button:has-text("Add member")');
    await page.fill('#member-user', 'nobody-here');
    await page.click('form button[type="submit"]');

    // The server's own wording, not a generic failure — and the form keeps the input.
    await expect(page.locator('[role="alert"]')).toContainText('nobody-here');
    await expect(page.locator('#member-user')).toHaveValue('nobody-here');
  });

  test('issues a key once and revokes it after confirmation', async ({ page }) => {
    await login(page, USERS.useCaseAdmin);
    const slug = uniqueSlug('keys');
    await createUseCase(page, slug, 'Key probe');

    await page.goto(`/use-cases/${slug}?tab=keys`);
    await page.click('button:has-text("Issue key")');
    await page.fill('#key-label', 'e2e');
    await page.click('form button[type="submit"]');

    const secret = page.locator('.secret');
    await expect(secret).toBeVisible();
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
    await login(page, USERS.useCaseAdmin);
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
    await login(page, USERS.useCaseAdmin);
    const slug = uniqueSlug('gov');
    await createUseCase(page, slug, 'Governance probe');
    await logout(page);

    await login(page, USERS.governance);
    await page.goto(`/use-cases/${slug}?tab=keys`);
    await expect(page.locator('h2')).toContainText('Governance probe');

    await page.click('button:has-text("Issue key")');
    await page.click('form button[type="submit"]');

    await expect(page.locator('[role="alert"]')).toContainText('members of this use case');
    await expect(page.locator('.secret')).toHaveCount(0);
  });

  test('budget limits are shown even when consumption is present', async ({ page }) => {
    await login(page, USERS.useCaseAdmin);
    const slug = uniqueSlug('budget');
    await createUseCase(page, slug, 'Budget probe');

    await page.goto(`/use-cases/${slug}?tab=budgets`);
    await page.click('button:has-text("Add budget")');
    await page.fill('#budget-tokens', '1000');
    await page.click('form button[type="submit"]');

    await expect(page.locator('text=/0 \\/ 1000/')).toBeVisible();
    await expect(page.locator('[role="progressbar"]')).toHaveCount(1);
  });

  test('an invalid slug is refused before the request is sent', async ({ page }) => {
    await login(page, USERS.useCaseAdmin);
    await page.goto('/use-cases');
    await page.fill('#uc-slug', 'Not A Slug');
    await page.fill('#uc-name', 'Invalid');

    await expect(page.locator('#uc-slug-error')).toContainText('Lowercase letters');
    await expect(page.locator('button[type="submit"]')).toBeDisabled();
  });
});
