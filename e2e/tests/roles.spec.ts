import { Page, expect, test } from '@playwright/test';
import { USERS, login } from './support';

/**
 * Roles as data, in a real browser (`FRD-614`).
 *
 * The API tests prove a role is checked, stored and enforced. Only a browser shows that a Global
 * Administrator can do it from Platform → Roles — check the group, name the role, tick a box — and
 * that a person in that group then sees what the role grants, while the platform roles that may
 * not manage roles are shown the page read-only.
 */

/** A realm group `ucuser` is in and no role is bound to. */
const GROUP = '/abteilungen/kundendienst';

/** Remove any role left bound to {@link GROUP}, through the API with the console's own token. */
async function unbind(page: Page): Promise<void> {
  await page.evaluate(async (group) => {
    const token =
      sessionStorage.getItem('access_token') ?? localStorage.getItem('access_token') ?? '';
    const headers = { Authorization: `Bearer ${token}` };
    const roles = await (await fetch('/api/v1/roles/', { headers })).json();
    for (const role of roles as { slug: string; group_paths: string[]; builtin: boolean }[]) {
      if (!role.builtin && role.group_paths.includes(group)) {
        await fetch(`/api/v1/roles/${role.slug}/`, { method: 'DELETE', headers });
      }
    }
  }, GROUP);
}

test.describe('Platform administration — roles', () => {
  test('a role bound to a checked group gives its members exactly what it grants', async ({
    browser,
  }) => {
    const admin = await browser.newContext();
    const adminPage = await admin.newPage();
    await login(adminPage, USERS.globalAdmin);
    await unbind(adminPage);

    // Before: the member of the group is offered no register.
    const before = await browser.newContext();
    const beforePage = await before.newPage();
    await login(beforePage, USERS.useCaseUser);
    await expect(beforePage.locator('.aira-nav a[href="/register"]')).toHaveCount(0);
    await before.close();

    const label = `E2E roles ${Date.now().toString(36)}`;
    try {
      await adminPage.locator('.aira-user').getByTestId('platform-admin').click();
      await adminPage.getByTestId('platform-nav-roles').click();
      await expect(adminPage).toHaveURL(/\/platform\/roles$/);
      await expect(adminPage.getByTestId('roles-table')).toBeVisible();

      await adminPage.getByTestId('new-role').click();
      const editor = adminPage.getByTestId('role-editor');
      await editor.getByTestId('role-group').fill(GROUP);
      await editor.getByTestId('check-group').click();
      await expect(editor.getByTestId('group-check-result')).toBeVisible();
      await expect(editor.getByTestId('group-check-result')).not.toContainText('could not');
      await editor.getByTestId('role-label').fill(label);
      await editor.getByRole('checkbox', { name: /Every figure/ }).check();
      await editor.getByTestId('save-role').click();

      await expect(adminPage.getByTestId('roles-table')).toContainText(label);
      await expect(adminPage.getByTestId('role-changes')).toContainText(label);

      // After: the same person, a new session, is offered the register.
      const after = await browser.newContext();
      const afterPage = await after.newPage();
      await login(afterPage, USERS.useCaseUser);
      await expect(afterPage.locator('.aira-nav a[href="/register"]')).toBeVisible();
      await expect(afterPage.locator('.aira-nav a[href="/security"]')).toHaveCount(0);
      await after.close();
    } finally {
      await unbind(adminPage);
      await admin.close();
    }
  });

  test('a group Keycloak does not have cannot be saved', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    await page.goto('/platform/roles');
    await page.getByTestId('new-role').click();
    const editor = page.getByTestId('role-editor');
    await editor.getByTestId('role-group').fill('/abteilungen/does-not-exist');
    await editor.getByTestId('check-group').click();
    await expect(editor.getByTestId('group-check-result')).toContainText('does-not-exist');
    await editor.getByTestId('role-label').fill('Never saved');
    await expect(editor.getByTestId('save-role')).toBeDisabled();
  });

  test('IT Steuerung reads the roles and is offered no change', async ({ page }) => {
    await login(page, USERS.governance);
    await page.goto('/platform/roles');
    await expect(page.getByTestId('roles-table')).toBeVisible();
    await expect(page.getByTestId('roles-readonly')).toBeVisible();
    await expect(page.getByTestId('new-role')).toHaveCount(0);
    await expect(page.getByTestId('edit-role')).toHaveCount(0);
    await expect(page.getByTestId('badge-fixed').first()).toBeVisible();
  });
});
