import { GATEWAY_URL } from '../stack';
import { Page, expect, test } from '@playwright/test';
import {
  USERS,
  createUseCase,
  grantGroup,
  login,
  releaseAllModels,
  submitOfOpenForm,
  uniqueSlug,
} from './support';

/**
 * Who may read a prompt, as a browser sees it (`FRD-622`, `FRD-139`'s sibling question).
 *
 * The hermetic suites prove that `payload_visibility` travels both planes and that
 * `may_read_payload` refuses. Neither can say what a *person* meets, and that is the whole
 * subject of this setting: a use-case administrator who asks for content and is told no, while
 * the oversight roles are not. Three things only a browser shows —
 *
 * - the fourth mode is **offered** and survives a reload (a setting that saves and does not come
 *   back is the defect this suite exists for);
 * - under it, the trace **rows stay** and only the content is withheld. The mode hides what was
 *   said, not that it was said, and a table that emptied itself would be a different feature;
 * - the refusal is **readable**. The console does not hide the control: it asks, and shows the
 *   server's own message, so a reader learns the rule rather than concluding the trace is broken.
 *
 * `/abteilungen/kundendienst` is granted **as administrator**, which makes `ucuser` an
 * administrator of this use case and nothing else. A platform role would prove the opposite of
 * what is asserted — it reads content by design.
 */
const DEPARTMENT = '/abteilungen/kundendienst';

/** The sentence the test looks for, unique per run so no neighbouring row can satisfy it. */
function sentence(slug: string): string {
  return `A prompt only ${slug} should contain.`;
}

async function issueKey(page: Page, slug: string): Promise<string> {
  await page.goto(`/use-cases/${slug}?tab=keys`);
  await page.click('button:has-text("Issue key")');
  await page.fill('#key-label', 'content-visibility-e2e');
  await (await submitOfOpenForm(page)).click();
  const secret = page.locator('code.secret');
  await expect(secret).toBeVisible();
  const key = (await secret.innerText()).trim();
  await page.click('button:has-text("Done")');
  return key;
}

/** One recorded request. Polled: a key reaches the gateway over Kafka (`FRD-205`). */
async function spend(page: Page, slug: string, key: string) {
  const send = () =>
    page.request.post(`${GATEWAY_URL}/v1beta/models/qwen3:0.6b:generateContent`, {
      headers: { 'x-goog-api-key': key, 'content-type': 'application/json' },
      data: {
        contents: [{ parts: [{ text: sentence(slug) }] }],
        generationConfig: { maxOutputTokens: 8 },
      },
      timeout: 180_000,
    });
  await expect
    .poll(async () => (await send()).status(), { timeout: 60_000, intervals: [1000] })
    .toBe(200);
}

/**
 * Choose a content mode and wait until **Management** has stored it.
 *
 * The reload is the point: a checked radio is this component's own signal, which is set by the
 * click and says nothing about whether the request succeeded. Reloading asks the server.
 */
async function chooseContentMode(page: Page, slug: string, mode: string) {
  await page.goto(`/use-cases/${slug}`);
  const radio = page.getByTestId(`content-mode-${mode}`);
  await expect(radio).toBeVisible();
  await radio.click();
  await page.click('button:has-text("Save storage settings")');
  await page.reload();
  await expect(page.getByTestId(`content-mode-${mode}`)).toBeChecked();
}

/**
 * Open a trace's content panel and leave it open.
 *
 * **Live refresh off first.** The tab polls, and a refresh landing between the click and the
 * assertion closes the panel again — the first run of this test found the row collapsed with its
 * button still reading *"Show the prompt and response"*, which looks like a click that missed.
 * The toggle is also what a person would do before reading a row carefully.
 */
async function openContent(page: Page) {
  const live = page.getByTestId('traces-live');
  await expect(live).toBeVisible({ timeout: 60_000 });
  if (await live.isChecked()) await live.uncheck();

  const opener = page.locator('[data-testid^="open-payload-"]').first();
  await expect(opener).toBeVisible({ timeout: 60_000 });
  await opener.click();
  // Deliberately no assertion that the panel opened: whether it does is exactly what differs
  // between a reader who may and one who may not, and each test says which it expects.
}

/** A second identity needs its **own context**: Keycloak's SSO cookie would continue the first. */
async function asUser(page: Page, user: { username: string; password: string }) {
  const context = await page.context().browser()!.newContext();
  const fresh = await context.newPage();
  await login(fresh, user);
  return { context, page: fresh };
}

test.describe('Who may read prompts and responses', () => {
  test('the oversight-only mode is offered, saves, and is still chosen after a reload', async ({
    page,
  }) => {
    await login(page, USERS.globalAdmin);
    const slug = await createUseCase(page, uniqueSlug('vis'), 'Content visibility');

    // All four, in the order the panel lists them: a mode that is not offered cannot be chosen,
    // and the one added last is the one a template edit would drop.
    await page.goto(`/use-cases/${slug}`);
    for (const mode of ['not-stored', 'oversight', 'admins-and-own', 'members']) {
      await expect(page.getByTestId(`content-mode-${mode}`)).toBeVisible();
    }

    await chooseContentMode(page, slug, 'oversight');

    await page.reload();
    await expect(page.getByTestId('content-mode-oversight')).toBeChecked();
    // Storage itself is untouched: oversight-only is *stored and not readable here*, which is a
    // different answer from "not stored" and the whole reason it is a fourth mode.
    await expect(page.getByTestId('storage-off-warning')).toHaveCount(0);
  });

  test('an administrator of the use case is refused the content and keeps the row', async ({
    page,
  }) => {
    await login(page, USERS.globalAdmin);
    const slug = await createUseCase(page, uniqueSlug('vis'), 'Oversight only');
    // **The setting first, before any traffic.** A use-case update reaches the gateway over Kafka
    // (`FRD-205`), and the first version of this test saved the mode *after* sending the request:
    // the read then arrived while the gateway still held the previous visibility and the content
    // was served to an administrator. Not a defect — `_authority` checks `OVERSIGHT_ONLY` before
    // the administrator rule and reads the projection fresh — but a test that races a projection
    // is flaky in the way that looks exactly like one.
    await chooseContentMode(page, slug, 'oversight');
    await releaseAllModels(page, slug);
    await grantGroup(page, slug, DEPARTMENT, 'admin');
    const key = await issueKey(page, slug);
    // This polls until the key is live, which is itself several seconds of Kafka round trips after
    // the setting was saved — so by the time there is a trace to read, the gateway has the mode.
    await spend(page, slug, key);

    const administrator = await asUser(page, USERS.useCaseUser);
    await administrator.page.goto(`/use-cases/${slug}?tab=traces`);

    // The row is there — the request happened, and the figures are the use case's own business.
    await openContent(administrator.page);

    // **Asked, refused, and told why**, in the gateway's own words through the page's single
    // banner (`CLAUDE.md` §3: no silent failures). The control is not hidden: a reader who found
    // the button missing would learn nothing, and one who found an empty panel would reasonably
    // conclude the audit trail is broken.
    await expect(administrator.page.locator('[role="alert"]')).toContainText('oversight only');
    await expect(administrator.page.locator('[role="alert"]')).toContainText('administrators');
    await expect(administrator.page.getByTestId('payload-request')).toHaveCount(0);
    await expect(administrator.page.locator('body')).not.toContainText(sentence(slug));
    await administrator.context.close();
  });

  test('IT Security reads the same prompt the administrator was refused', async ({ page }) => {
    // The other half, and the half that makes the first one a *governance* statement rather than a
    // storage one: the content exists, it is kept, and somebody accountable can still read it.
    await login(page, USERS.globalAdmin);
    const slug = await createUseCase(page, uniqueSlug('vis'), 'Oversight reads');
    await chooseContentMode(page, slug, 'oversight');
    await releaseAllModels(page, slug);
    const key = await issueKey(page, slug);
    await spend(page, slug, key);

    const security = await asUser(page, USERS.security);
    await security.page.goto(`/use-cases/${slug}?tab=traces`);
    await openContent(security.page);

    await expect(security.page.getByTestId('payload-request')).toContainText(
      `A prompt only ${slug}`,
    );
    await security.context.close();
  });
});
