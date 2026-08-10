import { expect, test } from '@playwright/test';
import {
  USERS,
  createUseCase,
  expectNoHorizontalOverflow,
  grantGroup,
  login,
  logout,
  uniqueSlug,
} from './support';

/**
 * Granting access to a Keycloak group, through the browser (`FRD-209`).
 *
 * The properties this layer exists for: the picker is an interaction (typing, waiting, choosing —
 * none of which jsdom has), and the panel's job is to make a grant that reaches nobody *look*
 * different from one that works. Both are things a person sees and a unit test cannot.
 */

const DEPARTMENT = '/abteilungen/kundendienst';

/** Grant a group through the API, so a test can start from a known state. */
test.describe('Access by group', () => {
  test('a granted group is listed as a group, with how far it reaches', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('grant');
    await createUseCase(page, slug, 'Group grant probe');
    await grantGroup(page, slug, DEPARTMENT);

    await page.goto(`/use-cases/${slug}?tab=members`);
    const row = page.locator(`tr:has(code:text-is("${DEPARTMENT}"))`);
    await expect(row).toBeVisible({ timeout: 20_000 });
    await expect(row).toContainText('group');
    await expect(row).toContainText('user');
    await expectNoHorizontalOverflow(page, 'the access panel');
  });

  test('a grant that reaches nobody says so, instead of looking like a working one', async ({
    page,
  }) => {
    // A path matching nobody is silently inert: nothing fails, nobody gets access, and an access
    // list that showed it identically to a working grant could not be audited.
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('empty');
    await createUseCase(page, slug, 'Empty grant probe');
    await grantGroup(page, slug, '/abteilungen/nobody-is-in-this');

    await page.goto(`/use-cases/${slug}?tab=members`);
    const row = page.locator('tr:has(code:text-is("/abteilungen/nobody-is-in-this"))');
    await expect(row).toBeVisible({ timeout: 20_000 });
    await expect(row).toContainText('nobody yet');
  });

  test('the picker finds a group and grants it with the chosen role', async ({ page }) => {
    // Typing, waiting for the debounce, and choosing from a list are interactions jsdom has no
    // concept of — and the picker refusing to grant until something is *chosen* is the property
    // that stops a grant naming a group that does not exist.
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('picker');
    await createUseCase(page, slug, 'Picker probe');
    // The directory falls back to what the console already knows when no admin client is
    // configured — which is this stack. Granting it somewhere first is what makes it findable,
    // and is exactly the "re-grant an existing group" case the fallback is meant to cover.
    const other = uniqueSlug('seed');
    await createUseCase(page, other, 'Seed for the directory');
    await grantGroup(page, other, DEPARTMENT);

    await page.goto(`/use-cases/${slug}?tab=members`);
    await expect(page.locator('[data-testid="access-search"]')).toBeVisible({ timeout: 20_000 });

    // Nothing chosen yet: the button is inert.
    await expect(page.locator('[data-testid="access-grant"]')).toBeDisabled();

    await page.fill('[data-testid="access-search"]', 'kundendienst');
    const results = page.locator('[data-testid="access-results"]');
    await expect(results).toBeVisible({ timeout: 20_000 });
    await expect(results).toContainText('group');

    await results.locator('button').first().click();
    await page.selectOption('[data-testid="access-role"]', 'admin');
    await page.click('[data-testid="access-grant"]');

    await expect(page.locator('[role="status"]')).toContainText('Everyone in that group', {
      timeout: 20_000,
    });
    const row = page.locator(`tr:has(code:text-is("${DEPARTMENT}"))`);
    await expect(row).toContainText('admin');
    // The picker clears itself, so the next grant does not start from the last one's text.
    await expect(page.locator('[data-testid="access-search"]')).toHaveValue('');
  });

  test('a person can still be granted, and is labelled as a person', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('person');
    await createUseCase(page, slug, 'Person grant probe');

    await page.goto(`/use-cases/${slug}?tab=members`);
    await page.fill('[data-testid="access-search"]', 'ucuser');
    const results = page.locator('[data-testid="access-results"]');
    await expect(results).toBeVisible({ timeout: 20_000 });

    await results.locator('button').first().click();
    await page.click('[data-testid="access-grant"]');

    const row = page.locator('tr:has(code:text-is("ucuser"))');
    await expect(row).toBeVisible({ timeout: 20_000 });
    await expect(row).toContainText('person');
  });

  test('revoking a group takes it off the list', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('revoke');
    await createUseCase(page, slug, 'Revoke probe');
    await grantGroup(page, slug, DEPARTMENT);

    await page.goto(`/use-cases/${slug}?tab=members`);
    const row = page.locator(`tr:has(code:text-is("${DEPARTMENT}"))`);
    await expect(row).toBeVisible({ timeout: 20_000 });

    // Revoking asks first — it changes who may reach the models.
    page.once('dialog', (dialog) => {
      expect(dialog.message()).toContain('Anybody granted separately keeps theirs');
      void dialog.accept();
    });
    await row.locator(`[aria-label="Revoke ${DEPARTMENT}"]`).click();

    await expect(page.locator('[role="status"]')).toContainText('revoked', { timeout: 20_000 });
    await expect(row).toHaveCount(0);
  });

  test('a member who does not administer the use case is offered no way to grant', async ({
    page,
  }) => {
    // `FRD-206`: an action nobody can carry out reads as a broken system rather than a boundary.
    await login(page, USERS.useCaseUser);
    await page.goto('/use-cases/demo-uc?tab=members');

    await expect(page.locator('[data-testid="access-readonly"]')).toBeVisible({ timeout: 20_000 });
    await expect(page.locator('[data-testid="access-search"]')).toHaveCount(0);
    await expect(page.locator('[data-testid="access-grant"]')).toHaveCount(0);
  });

  test('the directory says when it is answering from what the console already knows', async ({
    page,
  }) => {
    // "No results" from a directory nobody could reach reads exactly like "no such group", and
    // those are different answers to act on. This stack has no admin client, so it is the
    // degraded case — and it has to say so.
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('degraded');
    await createUseCase(page, slug, 'Degraded directory probe');

    await page.goto(`/use-cases/${slug}?tab=members`);
    await page.fill('[data-testid="access-search"]', 'zzz-no-such-thing');

    await expect(page.locator('[data-testid="access-no-match"]')).toBeVisible({ timeout: 20_000 });
    await expect(page.locator('body')).toContainText('could not be searched');
  });
});
