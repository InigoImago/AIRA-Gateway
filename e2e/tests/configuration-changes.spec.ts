import { expect, test } from '@playwright/test';
import { USERS, createUseCase, login, logout, uniqueSlug } from './support';

/**
 * The configuration history, in a real browser (`FRD-632`).
 *
 * The Management tests prove a change writes its row and that the history is served to the roles
 * that may see people only. Only a browser shows that a person granted through the console's own
 * picker is the change the history then names — who added whom, with which role — that the menu
 * leads there, and that a role that may not see people is not offered the page.
 */

test.describe('Platform administration — configuration changes', () => {
  test('a person added through the picker is a change naming who added whom', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('history');
    await createUseCase(page, slug, 'History probe');

    await page.goto(`/use-cases/${slug}?tab=members`);
    await page.fill('[data-testid="access-search"]', 'ucuser');
    const results = page.locator('[data-testid="access-results"]');
    await expect(results).toBeVisible({ timeout: 20_000 });
    await results.locator('button').first().click();
    await page.click('[data-testid="access-grant"]');
    await expect(page.locator('tr:has(code:text-is("ucuser"))')).toBeVisible({ timeout: 20_000 });

    await page.goto('/platform/configuration-changes');
    await expect(page.getByTestId('platform-nav-configuration-changes')).toHaveClass(/is-active/);
    await page.getByTestId('history-use-case').fill(slug);
    await page.getByTestId('history-use-case').press('Enter');

    const member = page.locator('tr', { has: page.getByTestId('history-person') }).filter({
      hasText: 'ucuser',
    });
    await expect(member.first()).toBeVisible({ timeout: 15_000 });
    await expect(member.first().getByTestId('history-actor')).toHaveText('admin');
    await expect(member.first().getByTestId('history-what')).toContainText('Member');
    await expect(member.first().getByTestId('history-action')).toHaveText('Created');

    // The use case itself is in the history too, made by the same person.
    const created = page.locator('tr', { has: page.getByTestId('history-what') }).filter({
      hasText: 'Use case',
    });
    await expect(created.first().getByTestId('history-actor')).toHaveText('admin');
  });

  test('a role that may not see people is not offered the history', async ({ page }) => {
    await login(page, USERS.governance);
    await page.goto('/platform');
    await expect(page.getByTestId('platform-nav-roles')).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId('platform-nav-configuration-changes')).toHaveCount(0);
    await logout(page);
  });
});
