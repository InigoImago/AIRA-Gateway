import { expect, test } from '@playwright/test';
import { USERS, login, logout } from './support';

test.describe('Authentication', () => {
  test('completes the real Keycloak code flow and lands in the SPA', async ({ page }) => {
    await login(page, USERS.useCaseAdmin);
    await expect(page.locator('h1')).toContainText('AIRA Gateway');
    await expect(page).toHaveURL(/\/use-cases/);
  });

  test('an unauthenticated visit is sent to Keycloak, not shown the app', async ({ page }) => {
    await page.goto('/use-cases');
    await expect(page.locator('#kc-login')).toBeVisible({ timeout: 30_000 });
    expect(page.url()).toContain('/realms/aira/protocol/openid-connect/auth');
  });

  test('the authorization request uses PKCE with S256', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('#kc-login')).toBeVisible({ timeout: 30_000 });
    // ADR-0007: a public client without PKCE is the precondition for code interception.
    expect(page.url()).toContain('code_challenge_method=S256');
    expect(page.url()).toContain('code_challenge=');
  });

  test('the console reflects the realm roles carried by the token', async ({ page }) => {
    // These used to be disabled navigation tabs pointing at screens that do not exist. They were
    // removed — a tab that cannot be clicked reads as broken navigation — and the property they
    // encoded moved to a chip per role in the header, which is where somebody asking "why can I
    // not do this" is looking.
    await login(page, USERS.globalAdmin);
    await expect(page.locator('.aira-user__role[data-role="global-admin"]')).toBeVisible();
    await logout(page);

    await login(page, USERS.governance);
    await expect(page.locator('.aira-user__role[data-role="it-steuerung"]')).toBeVisible();
    await expect(page.locator('[data-role="global-admin"]')).toHaveCount(0);
  });

  test('logout ends the session and protects the app again', async ({ page }) => {
    await login(page, USERS.useCaseAdmin);
    await logout(page);
    await page.goto('/use-cases');
    await expect(page.locator('#kc-login')).toBeVisible({ timeout: 30_000 });
  });
});
