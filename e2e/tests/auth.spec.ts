import { Page, expect, test } from '@playwright/test';
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

  test('a broken token is re-obtained silently, and never reported as bad credentials', async ({
    page,
  }) => {
    // Reported from the running console: a token going invalid produced "invalid credentials" on
    // every panel at once. That reads as the *backend rejecting you* rather than as a session that
    // ran out — and in a console whose purpose is evidence, the next thing doubted is the figures
    // on the same page.
    //
    // Only a browser can test this: the token has to be a real one, stored where the app stores
    // it, and then broken. With the Keycloak session still alive the round trip is invisible,
    // which is the better outcome and still the same mechanism.
    await login(page, USERS.useCaseAdmin);
    await page.goto('/use-cases');
    await breakStoredToken(page);

    await page.reload();

    await expect(page.locator('.aira-user__name')).toHaveText('ucadmin', { timeout: 30_000 });
    await expect(page.locator('body')).not.toContainText('invalid credentials');
    // And it puts you back where you were, rather than on the front page.
    await expect(page).toHaveURL(/\/use-cases/);
  });

  test('a session ended in Keycloak lands on the login form, not on an error', async ({
    page,
    request,
  }) => {
    // The case the report actually described: Keycloak restarted, so the SSO session is gone and
    // there is nothing left to renew from. Ending the user's sessions through the admin API is
    // the same state without the restart.
    await login(page, USERS.useCaseAdmin);
    await page.goto('/use-cases');

    const admin = await request.post(
      'http://localhost:8080/realms/master/protocol/openid-connect/token',
      { form: { grant_type: 'password', client_id: 'admin-cli', username: 'admin', password: 'admin' } },
    );
    const token = (await admin.json()).access_token as string;
    const users = await request.get('http://localhost:8080/admin/realms/aira/users?username=ucadmin', {
      headers: { Authorization: `Bearer ${token}` },
    });
    const userId = (await users.json())[0].id as string;
    await request.post(`http://localhost:8080/admin/realms/aira/users/${userId}/logout`, {
      headers: { Authorization: `Bearer ${token}` },
    });

    await breakStoredToken(page);
    await page.reload();

    await expect(page.locator('#kc-login')).toBeVisible({ timeout: 30_000 });
    await expect(page.locator('body')).not.toContainText('invalid credentials');
  });
});

/** Keep the token's shape and destroy its signature — what the server sees when one expires. */
async function breakStoredToken(page: Page) {
  await page.evaluate(() => {
    const stored = window.sessionStorage.getItem('access_token');
    if (stored) window.sessionStorage.setItem('access_token', stored.slice(0, -6) + 'broken');
  });
}
