import { Page, expect, test } from '@playwright/test';
import { forget, remember } from '../created';
import { USERS, api, login } from './support';

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

/**
 * The roles bound to {@link GROUP}, as the API states them.
 *
 * A group carries one role, so one an operator bound there makes this test impossible rather than
 * something to clear away: removing *every* role on the group took theirs with it. It is a
 * precondition, checked before anything is changed; the test removes exactly the role it made.
 */
async function boundTo(page: Page, group: string): Promise<string[]> {
  const listed = await api<{ slug: string; group_paths: string[]; builtin: boolean }[]>(
    page,
    'GET',
    '/api/v1/roles/',
  );
  expect(listed.status).toBe(200);
  return listed.body
    .filter((role) => !role.builtin && role.group_paths.includes(group))
    .map((role) => role.slug);
}

test.describe('Platform administration — roles', () => {
  test('a role bound to a checked group gives its members exactly what it grants', async ({
    browser,
  }) => {
    const admin = await browser.newContext();
    const adminPage = await admin.newPage();
    await login(adminPage, USERS.globalAdmin);
    const taken = await boundTo(adminPage, GROUP);
    test.skip(taken.length > 0, `${GROUP} already carries a role of this installation's`);

    // Before: the member of the group is offered no register.
    const before = await browser.newContext();
    const beforePage = await before.newPage();
    await login(beforePage, USERS.useCaseUser);
    await expect(beforePage.locator('.aira-nav a[href="/register"]')).toHaveCount(0);
    await before.close();

    const label = `E2E roles ${Date.now().toString(36)}`;
    let made = '';
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
      const saved = adminPage.waitForResponse(
        (response) =>
          response.url().endsWith('/api/v1/roles/') && response.request().method() === 'POST',
      );
      await editor.getByTestId('save-role').click();
      made = ((await (await saved).json()) as { slug: string }).slug;
      remember('role', made);

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
      if (made) {
        const removed = await api(adminPage, 'DELETE', `/api/v1/roles/${made}/`);
        expect([204, 404]).toContain(removed.status);
        forget((entry) => entry.kind === 'role' && entry.name === made);
      }
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
