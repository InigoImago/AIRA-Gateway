import { Page, expect, test } from '@playwright/test';
import { forget } from '../created';
import { createRealmUser, deleteRealmUser, logoutRealmUser } from './realm';
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

  test('a session ended in Keycloak lands on the login form, not on an error', async ({ page }) => {
    // The case the report actually described: Keycloak restarted, so the SSO session is gone and
    // there is nothing left to renew from. Ending the user's sessions through the admin API is
    // the same state without the restart.
    //
    // **For a person of this test's own.** Ending every session of a shared demo account signs
    // out whoever else is using it — a person at the console, or the next spec — and each admin
    // call is checked, or a refused one would leave the test asserting about a session that never
    // ended.
    const person = await createRealmUser(
      `e2e-session-${Date.now().toString(36)}`,
      `E2e-${Math.random().toString(36).slice(2, 12)}-9x`,
    );
    try {
      await login(page, person);
      await page.goto('/use-cases');

      await logoutRealmUser(person.id);
      await breakStoredToken(page);
      await page.reload();

      await expect(page.locator('#kc-login')).toBeVisible({ timeout: 30_000 });
      await expect(page.locator('body')).not.toContainText('invalid credentials');
    } finally {
      await deleteRealmUser(person.id);
      forget((entry) => entry.kind === 'keycloak-user' && entry.name === person.id);
    }
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

  /**
   * The same loop with **one** plane refusing, which is the realistic shape of it.
   *
   * Two planes validate tokens separately (the gateway per issuer and audience, management its
   * own), so a mismatch usually refuses on one of them. Here management accepts the token and the
   * gateway refuses it: the shell's own `/api/v1/me` answers on every pass. An answer from the
   * plane that accepts says nothing about the one that refuses — counted as "the loop is over", it
   * reset the guard on every page and the console went round for ever.
   */
  test('stops signing in again when only the gateway refuses the session', async ({ page }) => {
    await login(page, USERS.useCaseAdmin);

    let authorizations = 0;
    page.on('request', (request) => {
      if (request.url().includes('/protocol/openid-connect/auth')) authorizations += 1;
    });
    await page.route(/\/gw\//, (route) =>
      route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ error: { message: 'this gateway does not accept that token' } }),
      }),
    );

    // The reporting page asks the gateway on arrival, and the shell asks management for `/me`.
    await page.goto('/reporting');

    await expect(page.getByTestId('login-loop')).toBeVisible({ timeout: 60_000 });
    await expect(page.getByTestId('login-loop')).toContainText('did not help 3 times');
    expect(authorizations).toBeLessThanOrEqual(4);
  });

  /**
   * **A session renews itself without leaving the page** (`auth.config.ts`, `useSilentRefresh`).
   *
   * The renewal ran through a hidden iframe that loaded the issuer, which the console's own content
   * policy refuses to frame. Every renewal failed quietly, the token lapsed, and the next API call
   * sent the reader through a full-page login — every five minutes with the realm's default
   * lifetime, and on a live page without anybody touching anything. Whatever was typed into a form
   * went with it.
   *
   * The browser's clock is moved past three quarters of the token's 300-second lifetime, which is
   * when the client renews. The proof is that the page is the same page afterwards — a marker set
   * on `window` survives, which no navigation allows — that the renewal was a refresh-token grant,
   * that nobody was sent to the authorization endpoint, and that the API takes the renewed token.
   */
  test('renews the session with the refresh token, without leaving the page', async ({ page }) => {
    await page.clock.install();
    await login(page, USERS.useCaseAdmin);
    await page.goto('/use-cases');
    await expect(page.locator('.aira-user__name')).toHaveText('ucadmin', { timeout: 30_000 });

    await page.evaluate(() => {
      (window as unknown as { airaMarker?: string }).airaMarker = 'the same page';
    });
    const before = await page.evaluate(() => window.sessionStorage.getItem('access_token'));
    let authorizations = 0;
    page.on('request', (request) => {
      if (request.url().includes('/protocol/openid-connect/auth')) authorizations += 1;
    });
    const renewal = page.waitForRequest(
      (request) =>
        request.url().includes('/protocol/openid-connect/token') &&
        (request.postData() ?? '').includes('grant_type=refresh_token'),
      { timeout: 30_000 },
    );

    await page.clock.fastForward('04:00');
    // The library waits a second after the expiry warning before it renews.
    await page.clock.runFor(2_000);
    await renewal;

    await expect
      .poll(() => page.evaluate(() => window.sessionStorage.getItem('access_token')))
      .not.toBe(before);
    expect(
      await page.evaluate(() => (window as unknown as { airaMarker?: string }).airaMarker),
    ).toBe('the same page');
    expect(authorizations).toBe(0);
    const status = await page.evaluate(async () => {
      const token = window.sessionStorage.getItem('access_token') ?? '';
      const response = await fetch('/api/v1/me', { headers: { Authorization: `Bearer ${token}` } });
      return response.status;
    });
    expect(status).toBe(200);
  });
});

/** Keep the token's shape and destroy its signature — what the server sees when one expires. */
async function breakStoredToken(page: Page) {
  await page.evaluate(() => {
    const stored = window.sessionStorage.getItem('access_token');
    if (stored) window.sessionStorage.setItem('access_token', stored.slice(0, -6) + 'broken');
  });
}
