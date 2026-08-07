import { APIRequestContext, Page, expect, test } from '@playwright/test';
import {
  USERS,
  createUseCase,
  expectNoHorizontalOverflow,
  login,
  logout,
  uniqueSlug,
} from './support';

/**
 * Spend and usage reporting (FRD-601) through the real browser and the real stack.
 *
 * The property this layer exists for is the **visibility boundary**: governance sees a use case
 * it is not a member of, and a use-case user does not. Both halves need a real Keycloak token —
 * the hermetic tests construct a `Principal` directly and so cannot show that the role and the
 * group memberships survive the round trip through the realm and into the gateway.
 */

const GATEWAY = process.env.AIRA_E2E_GATEWAY_URL ?? 'http://localhost:8001';

/** Issue an API key for a use case through the UI and return the plaintext. */
async function issueKey(page: Page, slug: string): Promise<string> {
  await page.goto(`/use-cases/${slug}?tab=keys`);
  await page.click('button:has-text("Issue key")');
  await page.fill('#key-label', 'reporting-e2e');
  await page.click('form button[type="submit"]');
  const secret = page.locator('.secret');
  await expect(secret).toBeVisible();
  return (await secret.textContent())?.trim() ?? '';
}

/**
 * Send a request through the gateway so the period has something in it.
 *
 * The key reaches the gateway over Kafka, so the first attempts may be refused while the
 * read-model catches up — that is the distribution working, not a failure. Retry until it lands.
 */
async function sendTraffic(request: APIRequestContext, key: string): Promise<void> {
  await expect
    .poll(
      async () => {
        const response = await request.post(`${GATEWAY}/v1beta/models/mock-1:generateContent`, {
          headers: { 'x-goog-api-key': key },
          data: { contents: [{ role: 'user', parts: [{ text: 'reporting probe' }] }] },
        });
        return response.status();
      },
      { timeout: 30_000, message: 'the issued key never reached the gateway' },
    )
    .toBe(200);
}

test.describe('Reporting', () => {
  test('governance sees a use case it is not a member of; a use-case user does not', async ({
    page,
    request,
  }) => {
    // A fresh use case, so the assertion is about this slug and not about whatever else the
    // suite has left in the database.
    await login(page, USERS.useCaseAdmin);
    const slug = uniqueSlug('report');
    await createUseCase(page, slug, 'Reporting probe');
    await sendTraffic(request, await issueKey(page, slug));

    // A new user needs the old Keycloak session gone, or the SPA lands straight back in as
    // whoever was there before.
    await logout(page);
    // itgov holds `it-steuerung` and is in no use-case group at all (see the dev realm), so
    // anything it sees here it sees by virtue of the role.
    await login(page, USERS.governance);
    await page.goto('/reporting');
    await expect(page.locator('[data-testid="scope"]')).toContainText('Every use case');
    // The write is off the hot path (FRD-405), so the row appears a moment after the response.
    await expect(page.locator(`code:has-text("${slug}")`).first()).toBeVisible({
      timeout: 30_000,
    });

    await logout(page);
    // ucuser is a member of `demo-uc` only — not of this slug, in the realm or anywhere else.
    // Same screen, same period, same moment: one caller sees it and the other does not. That
    // contrast is what makes the absence below meaningful rather than a page that failed to
    // load; the page is asserted to be working, and empty of an error, on the way past.
    await login(page, USERS.useCaseUser);
    await page.goto('/reporting');
    await expect(page.locator('[data-testid="scope"]')).toContainText('Your use cases only');
    await expect(page.locator('[data-testid="total-requests"]')).toBeVisible();
    await expect(page.locator('[role="alert"]')).toHaveCount(0);
    await expect(page.locator(`code:has-text("${slug}")`)).toHaveCount(0);
  });

  test('the report opens on the current month and can be pointed at another period', async ({
    page,
  }) => {
    await login(page, USERS.governance);
    await page.goto('/reporting');

    // Arrives loaded: a reporting screen that opens empty and waits to be asked is a screen
    // people conclude has no data.
    await expect(page.locator('[data-testid="total-requests"]')).toBeVisible();
    await expect(page.locator('#report-preset')).toHaveValue('this-month');

    await page.selectOption('#report-preset', 'custom');
    await page.fill('#report-from', '2020-01-01');
    await page.fill('#report-to', '2020-02-01');
    await page.click('button:has-text("Show")');

    // A period long before the installation existed: zero, and said as zero rather than as an
    // error or a blank screen.
    await expect(page.locator('[data-testid="total-requests"]')).toHaveText('0');
    await expect(page.locator('text=No traffic from any use case in this period.')).toBeVisible();
    await expect(page.locator('[role="alert"]')).toHaveCount(0);
  });

  test('refuses a window that ends before it starts, without asking the gateway', async ({
    page,
  }) => {
    await login(page, USERS.governance);
    await page.goto('/reporting');
    await page.selectOption('#report-preset', 'custom');
    await page.fill('#report-from', '2026-08-10');
    await page.fill('#report-to', '2026-08-01');

    await expect(page.locator('.field__hint--error')).toContainText('after the start date');
    await expect(page.locator('button:has-text("Show")')).toBeDisabled();
  });

  test('hovering an info button shows what the figure counts', async ({ page }) => {
    // Reported from the running console: the info buttons showed nothing. They carried a `title`
    // attribute, and a native tooltip needs a long hover, never appears on a touch screen and is
    // invisible to a keyboard. A real browser is the only layer that can tell "renders a tooltip
    // attribute" from "shows the reader anything" — so the hover is exercised as a hover.
    await login(page, USERS.governance);
    await page.goto('/reporting');

    await expect(page.locator('[data-testid="help-refused"]')).toHaveCount(0);

    await page.hover('[data-testid="info-refused"]');
    await expect(page.locator('[data-testid="help-refused"]')).toBeVisible();
    await expect(page.locator('[data-testid="help-refused"]')).toContainText(
      'never reached a model',
    );

    // Moving away puts it back.
    await page.hover('h2');
    await expect(page.locator('[data-testid="help-refused"]')).toHaveCount(0);
  });

  test('and clicking pins it, for a screen with no hover at all', async ({ page }) => {
    await login(page, USERS.governance);
    await page.goto('/reporting');

    await page.click('[data-testid="info-tokens"]');
    await page.hover('h2');
    await expect(page.locator('[data-testid="help-tokens"]')).toBeVisible();

    await page.click('[data-testid="info-tokens"]');
    await page.hover('h2');
    await expect(page.locator('[data-testid="help-tokens"]')).toHaveCount(0);
  });

  test('the report fits a narrow viewport instead of scrolling the page sideways', async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await login(page, USERS.governance);
    await page.goto('/reporting');
    await expect(page.locator('[data-testid="total-requests"]')).toBeVisible();

    // Five-column tables are exactly the thing that drags a phone-width page sideways; they get
    // their own scroll container, and this is what proves it is doing its job.
    await expectNoHorizontalOverflow(page, 'reporting at 390px');
  });
});
