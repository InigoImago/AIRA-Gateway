import { GATEWAY_URL } from '../stack';
import { Page, expect, test } from '@playwright/test';
import {
  USERS,
  createUseCase,
  login,
  releaseAllModels,
  submitOfOpenForm,
  uniqueSlug,
} from './support';

/**
 * Platform administration and the content-read log, in a real browser (`FRD-622`).
 *
 * The gateway tests prove a read is recorded with the reader's roles and that the log is served to
 * the platform roles only. Only a browser shows that the header offers the area to the right
 * people, that its menu leads to the page, and that a read somebody has just made is on it.
 */

async function issueKey(page: Page, slug: string): Promise<string> {
  await page.goto(`/use-cases/${slug}?tab=keys`);
  await page.click('button:has-text("Issue key")');
  await page.fill('#key-label', 'content-reads-e2e');
  await (await submitOfOpenForm(page)).click();
  const secret = page.locator('.secret');
  await expect(secret).toBeVisible();
  return (await secret.textContent())?.trim() ?? '';
}

test.describe('Platform administration — content reads', () => {
  test('a read of a stored prompt appears in the log, with the reader and the role', async ({
    page,
    request,
  }) => {
    await login(page, USERS.globalAdmin);
    const slug = await createUseCase(page, uniqueSlug('reads'), 'Content reads');
    await releaseAllModels(page, slug);
    const key = await issueKey(page, slug);

    // One request through the gateway, retried until the key has reached it over Kafka.
    await expect
      .poll(
        async () =>
          (
            await request.post(`${GATEWAY_URL}/v1beta/models/mock-1:generateContent`, {
              headers: { 'x-goog-api-key': key },
              data: { contents: [{ role: 'user', parts: [{ text: 'content read probe' }] }] },
            })
          ).status(),
        { timeout: 60_000, intervals: [1000], message: 'the issued key never reached the gateway' },
      )
      .toBe(200);

    await page.goto(`/use-cases/${slug}?tab=traces`);
    const opener = page.locator('[data-testid^="open-payload-"]').first();
    await expect(opener).toBeVisible({ timeout: 60_000 });
    await opener.click();
    await expect(page.getByTestId('payload-request')).toContainText('content read probe');

    // From the header, beside the name — not from the main navigation.
    await page.locator('.aira-user').getByTestId('platform-admin').click();
    await expect(page).toHaveURL(/\/platform\/content-reads$/);
    await expect(page.getByTestId('platform-nav-content-reads')).toHaveClass(/is-active/);

    await page.getByTestId('reads-use-case').fill(slug);
    const row = page.locator('[data-testid="content-reads"] tbody tr').first();
    await expect(row).toContainText(slug, { timeout: 15_000 });
    await expect(row.getByTestId('read-who')).toHaveText('admin');
    await expect(row.getByTestId('read-ground')).toHaveText('Platform role (incident)');
    await expect(row.getByTestId('read-roles')).toContainText('Global administrator');

    // The request id leads to the request: its row in the use case's traces, marked, with the
    // content one deliberate click away — that click is a recorded read of its own.
    await row.getByTestId('read-request').click();
    await expect(page).toHaveURL(new RegExp(`/use-cases/${slug}\\?.*request=`));
    await expect(page.getByTestId('focused-request')).toBeVisible();
    const openers = page.locator('[data-testid^="open-payload-"]');
    await expect(openers).toHaveCount(1);
    await expect(page.getByTestId('payload-request')).toHaveCount(0);
    await openers.click();
    await expect(page.getByTestId('payload-request')).toContainText('content read probe');

    await page.getByTestId('show-all-requests').click();
    await expect(page.getByTestId('focused-request')).toHaveCount(0);
    await expect(page).not.toHaveURL(/request=/);
  });

  test('IT Steuerung reads the log, and a use-case user is not offered the area', async ({
    browser,
  }) => {
    // A fresh context per person: Keycloak's SSO cookie would otherwise continue as the first.
    const governance = await browser.newContext();
    const governancePage = await governance.newPage();
    await login(governancePage, USERS.governance);
    await governancePage.locator('.aira-user').getByTestId('platform-admin').click();
    await expect(
      governancePage.getByTestId('content-reads').or(governancePage.getByTestId('no-reads')),
    ).toBeVisible({ timeout: 15_000 });
    await expect(governancePage.locator('[role="alert"]')).toHaveCount(0);
    await governance.close();

    const member = await browser.newContext();
    const memberPage = await member.newPage();
    await login(memberPage, USERS.useCaseUser);
    await expect(memberPage.getByTestId('platform-admin')).toHaveCount(0);
    await member.close();
  });
});
