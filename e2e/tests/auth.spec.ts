import { KEYCLOAK_URL } from '../stack';
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
      `${KEYCLOAK_URL}/realms/master/protocol/openid-connect/token`,
      {
        form: {
          grant_type: 'password',
          client_id: 'admin-cli',
          username: 'admin',
          password: 'admin',
        },
      },
    );
    const token = (await admin.json()).access_token as string;
    const users = await request.get(`${KEYCLOAK_URL}/admin/realms/aira/users?username=ucadmin`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    const userId = (await users.json())[0].id as string;
    await request.post(`${KEYCLOAK_URL}/admin/realms/aira/users/${userId}/logout`, {
      headers: { Authorization: `Bearer ${token}` },
    });

    await breakStoredToken(page);
    await page.reload();

    await expect(page.locator('#kc-login')).toBeVisible({ timeout: 30_000 });
    await expect(page.locator('body')).not.toContainText('invalid credentials');
  });

  /**
   * **The loop, in a real browser against real Keycloak.**
   *
   * Reported from use: you can authenticate perfectly well, this installation refuses the token
   * Keycloak issues, and the page flickers through the login round trip — throwing an error each
   * time — until the account is locked out.
   *
   * The condition is reproduced rather than simulated: a **real** authorization-code flow, a
   * **real** SSO session that stays alive, and only the first-party API forced to refuse. That is
   * what a mismatched audience or issuer, a clock too far apart (`FRD-134`) or a session this
   * deployment no longer recognises actually looks like from the browser — Keycloak keeps saying
   * yes, and a new token is refused exactly like the last one.
   *
   * Forcing the refusal at the network layer rather than by breaking the realm is deliberate:
   * `AIRA_OIDC_AUDIENCE` or the realm's mappers are shared by every other spec and by the stack
   * itself, and a test that mutates them leaves the deployment broken when it fails halfway. What
   * is under test is the **console's** behaviour when the API refuses a session, and that needs no
   * deployment to be broken.
   *
   * Before the fix this test does not fail — it never finishes: the count below climbs until
   * Keycloak's brute-force limit stops it.
   */
  test('stops signing in again when the API refuses a session Keycloak keeps accepting', async ({
    page,
  }) => {
    await login(page, USERS.useCaseAdmin);

    // The thing that was happening over and over. Counted rather than timed, because "it settled
    // down" is not a property — "it stopped after a bounded number of round trips" is.
    let authorizations = 0;
    page.on('request', (request) => {
      if (request.url().includes('/protocol/openid-connect/auth')) authorizations += 1;
    });

    await page.route(/\/(api|gw)\//, (route) =>
      route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'this installation does not accept that token' }),
      }),
    );

    await page.goto('/use-cases');

    // It stops, and says why, instead of going round.
    await expect(page.getByTestId('login-loop')).toBeVisible({ timeout: 60_000 });
    await expect(page.getByTestId('login-loop')).toContainText('did not help');
    // Three attempts are allowed inside the window; the fourth is the one that stops. Anything
    // above that is the loop this test exists to prevent, and the number is what makes the
    // assertion about the *loop* rather than about a screen appearing eventually.
    expect(authorizations).toBeLessThanOrEqual(4);

    // And the routes are not rendered behind it: every screen there needs the token being refused,
    // so showing them would fill the page with failures that share one cause and name none of it.
    await expect(page.locator('.aira-use-case-list')).toHaveCount(0);

    // **The way out has to end the session at Keycloak.** With the refusal lifted, a local-only
    // logout would be signed straight back in by the SSO session — the loop with an extra step.
    // Landing on the login form is what proves the provider session is gone.
    await page.unroute(/\/(api|gw)\//);
    await page.getByRole('button', { name: /sign out completely/i }).click();

    await expect(page.locator('#kc-login').or(page.locator('#username')).first()).toBeVisible({
      timeout: 30_000,
    });
  });
});

/** Keep the token's shape and destroy its signature — what the server sees when one expires. */
async function breakStoredToken(page: Page) {
  await page.evaluate(() => {
    const stored = window.sessionStorage.getItem('access_token');
    if (stored) window.sessionStorage.setItem('access_token', stored.slice(0, -6) + 'broken');
  });
}
