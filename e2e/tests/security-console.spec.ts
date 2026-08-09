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
 * The IT Security console, warnings and traces (FRD-502) through the real browser.
 *
 * This layer exists here for the reason the login and tooltip defects taught twice: **a change to
 * whether a control does something when used is an e2e change.** Three of this feature's properties
 * are invisible to every layer below:
 *
 * - **IT Security's console is no longer empty.** That was the complaint; only a real login as
 *   `itsec` can show it is answered.
 * - **A withheld action is withheld by the server's answer, not by the console's guess.** A
 *   read-only governance role must see the same page and no kill switch.
 * - **The view refreshes itself.** A unit test can prove a timer fires; only a browser can show
 *   that a row which did not exist when the page loaded appears without a reload.
 */

const GATEWAY = process.env.AIRA_E2E_GATEWAY_URL ?? 'http://localhost:8001';

async function issueKey(page: Page, slug: string): Promise<string> {
  await page.goto(`/use-cases/${slug}?tab=keys`);
  await page.click('button:has-text("Issue key")');
  await page.fill('#key-label', 'security-e2e');
  await page.click('form button[type="submit"]');
  const secret = page.locator('.secret');
  await expect(secret).toBeVisible();
  return (await secret.textContent())?.trim() ?? '';
}

/** One request through the gateway, retried until the key has reached it over Kafka. */
async function sendTraffic(request: APIRequestContext, key: string): Promise<void> {
  await expect
    .poll(
      async () => {
        const response = await request.post(`${GATEWAY}/v1beta/models/mock-1:generateContent`, {
          headers: { 'x-goog-api-key': key },
          data: { contents: [{ role: 'user', parts: [{ text: 'security console probe' }] }] },
        });
        return response.status();
      },
      { timeout: 30_000, message: 'the issued key never reached the gateway' },
    )
    .toBe(200);
}

test.describe('IT Security console', () => {
  test('IT Security lands on a console that shows it something', async ({ page }) => {
    // The whole point of FRD-502. Before it, this role signed in and saw an empty application.
    await login(page, USERS.security);

    await expect(page.locator('nav a:has-text("Security")')).toBeVisible();
    await page.click('nav a:has-text("Security")');
    await expect(page).toHaveURL(/\/security/);

    // The three questions the role actually has, all reachable.
    await expect(page.locator('[role="tab"]:has-text("Findings")')).toBeVisible();
    await expect(page.locator('[role="tab"]:has-text("Suspensions")')).toBeVisible();
    await expect(page.locator('[role="tab"]:has-text("Rules")')).toBeVisible();
    await expectNoHorizontalOverflow(page, 'the security console');
  });

  test('the kill switch is offered to IT Security and withheld from oversight', async ({
    page,
  }) => {
    // Two different permissions, and conflating them is the defect this project already made
    // once: *seeing* every use case is oversight, *stopping* traffic is an incident role. A
    // button that answers 403 is worse than an absent one (FRD-206).
    await login(page, USERS.security);
    await page.goto('/security');
    await page.click('[role="tab"]:has-text("Suspensions")');
    await expect(page.locator('[data-testid="stop-toggle"]')).toBeVisible();

    await logout(page);
    await login(page, USERS.governance);
    await page.goto('/security');
    await page.click('[role="tab"]:has-text("Suspensions")');

    await expect(page.locator('[data-testid="stop-toggle"]')).toHaveCount(0);
    // Withheld, and it says who does it — an unexplained absence reads as a broken console.
    await expect(page.locator('[data-testid="stop-readonly"]')).toContainText('IT Security');
  });

  test('stopping and restoring a caller is a decision the console records', async ({ page }) => {
    await login(page, USERS.security);
    await page.goto('/security');
    await page.click('[role="tab"]:has-text("Suspensions")');
    await page.click('[data-testid="stop-toggle"]');

    const target = `e2e-probe-${Date.now()}`;
    await page.selectOption('#stop-target', 'subject');
    await page.fill('#stop-value', target);
    await page.fill('#stop-reason', 'e2e walkthrough');
    await page.click('form button[type="submit"]');

    // The row carries author and reason, because "blocked for two hours last Tuesday, by whom,
    // and why" is what a review asks (ADR-0014).
    const active = page.locator('[data-testid="active-suspensions"]');
    const row = active.locator(`tr:has(code:text-is("${target}"))`);
    await expect(row).toBeVisible({ timeout: 15_000 });
    await expect(row).toContainText('e2e walkthrough');

    // Restoring asks first — it is a change to who may reach the models.
    page.once('dialog', (dialog) => dialog.accept());
    await row.locator('button:has-text("Restore")').click();
    // The page's status banner, not any `.callout`: the warning strip counting active suspensions
    // is one too, and whatever else the installation has stopped is none of this test's business.
    await expect(page.locator('[role="status"]')).toContainText('restored', { timeout: 15_000 });
    // And it says the decision takes a moment to reach every instance — the suspension cache is
    // deliberately a few seconds behind (FRD-503 §4.1), so a console implying "done" would have
    // somebody test it immediately and conclude it did not work.
    await expect(page.locator('[role="status"]')).toContainText('few seconds');
    // It leaves the *active* list and stays on the page: "blocked for two hours last Tuesday, by
    // whom, and why" is what a review asks, so a lifted suspension is kept and stamped, never
    // deleted.
    await expect(row).toHaveCount(0, { timeout: 15_000 });
    await expect(page.locator(`code:text-is("${target}")`)).toHaveCount(1);
  });
});

test.describe('Warnings and traces on a use case', () => {
  test("a request appears in the use case's traces without reloading the page", async ({
    page,
    request,
  }) => {
    // The live refresh is the part no other layer can show. The page is opened *before* the
    // traffic exists; if nothing arrives, "live" is a label rather than a behaviour.
    //
    // Read as `itgov`, deliberately. The gateway derives use-case membership from Keycloak
    // **groups** (FRD-102), and creating a use case in this console does not create one — so the
    // account that just created it can see no requests for it, and the oversight role can. The
    // console now says which of those two empties it is; the next test asserts that.
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('trace');
    await createUseCase(page, slug, 'Trace probe');
    const key = await issueKey(page, slug);

    await logout(page);
    await login(page, USERS.governance);
    await page.goto(`/use-cases/${slug}?tab=traces`);
    await expect(page.locator('[data-testid="no-traces"]')).toBeVisible({ timeout: 20_000 });

    await sendTraffic(request, key);

    // No reload, no click: the row arrives on its own.
    await expect(page.locator('tbody tr').first()).toBeVisible({ timeout: 40_000 });
    await expect(page.locator('tbody')).toContainText('generateContent');
    await expectNoHorizontalOverflow(page, 'the traces tab');
  });

  test('an empty tab says which empty it is', async ({ page }) => {
    // Found by this suite on its first run. A use-case administrator opened the tab of a use case
    // they had just created and read "no requests match" — while the real reason was that the
    // gateway could not see them as a member at all. An empty state that states the wrong reason
    // is worse than one that states none: the reader concludes the recording is broken, and then
    // distrusts every figure on the page.
    // Same split as the consumption panel: created by the only role that may, read by a role that
    // is not oversight — `not-in-scope` is unreachable for anybody who sees every use case.
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('scope');
    await createUseCase(page, slug, 'Scope probe');

    await logout(page);
    await login(page, USERS.useCaseAdmin);
    await page.goto(`/use-cases/${slug}?tab=traces`);
    const notice = page.locator('[data-testid="not-in-scope"]');
    await expect(notice).toBeVisible({ timeout: 20_000 });
    // Actionable: it names the group somebody has to be added to.
    await expect(notice).toContainText(`/use-cases/${slug}`);
    await expect(page.locator('[data-testid="no-traces"]')).toHaveCount(0);

    // The warnings tab answers the same question the same way.
    await page.goto(`/use-cases/${slug}?tab=warnings`);
    await expect(page.locator('[data-testid="not-in-scope"]')).toBeVisible({ timeout: 20_000 });
  });

  test('the traces view shows no prompt and no answer, and says so', async ({ page, request }) => {
    // FRD-502 FR-11 asserted where somebody could actually be harmed by the opposite: in the
    // rendered page. The use case stores payloads by default, so the prompt *is* in the row the
    // endpoint selects from.
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('nopay');
    await createUseCase(page, slug, 'Payload probe');
    const key = await issueKey(page, slug);

    const secret = `zwiebelkuchen-${Date.now()}`;
    await expect
      .poll(
        async () => {
          const response = await request.post(`${GATEWAY}/v1beta/models/mock-1:generateContent`, {
            headers: { 'x-goog-api-key': key },
            data: { contents: [{ role: 'user', parts: [{ text: secret }] }] },
          });
          return response.status();
        },
        { timeout: 30_000 },
      )
      .toBe(200);

    await logout(page);
    await login(page, USERS.governance);
    await page.goto(`/use-cases/${slug}?tab=traces`);
    await expect(page.locator('tbody tr').first()).toBeVisible({ timeout: 40_000 });
    await expect(page.locator('body')).toContainText('Prompts and responses are not shown here');
    await expect(page.locator('body')).not.toContainText(secret);
  });

  test('a member of a real use case reaches their own warnings', async ({ page }) => {
    // `demo-uc` is a seeded use case whose Keycloak group `ucadmin` is actually in, which is what
    // makes this the members' view rather than an oversight one.
    await login(page, USERS.useCaseAdmin);

    await page.goto('/use-cases/demo-uc?tab=warnings');
    // Nothing has fired, and the page says that rather than showing an empty table that reads as
    // broken — and it is *this* empty, not the out-of-scope one.
    await expect(page.locator('[data-testid="no-warnings"]')).toBeVisible({ timeout: 20_000 });
    await expect(page.locator('[data-testid="not-in-scope"]')).toHaveCount(0);
  });
});
