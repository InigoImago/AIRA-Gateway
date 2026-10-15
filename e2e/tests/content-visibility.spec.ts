import { GATEWAY_URL } from '../stack';
import { Page, expect, test } from '@playwright/test';
import {
  USERS,
  createUseCase,
  grantGroup,
  login,
  releaseAllModels,
  strictDataMode,
  uniqueSlug,
  issueKey,
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

/** A key, with the one-time box closed again: the page is read afterwards. */
async function issueKeyAndClose(page: Page, slug: string): Promise<string> {
  const key = await issueKey(page, slug, 'content-visibility-e2e');
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
  // Under strict data mode, the default, a new use case already reads as oversight-only — the
  // server's answer, rendered — and saving an unchanged form is not offered.
  if (!(await radio.isChecked())) {
    await radio.click();
    await page.click('button:has-text("Save storage settings")');
  }
  await page.reload();
  await expect(page.getByTestId(`content-mode-${mode}`)).toBeChecked();
}

/**
 * Open a trace's content panel and leave it open.
 *
 * **The row first, with live refresh still on.** A request's row is written after its answer, so
 * the tab's first load can come back without it, and only a later refresh brings it. Switched off
 * before the row has arrived, the tab keeps that first answer for good, and the wait runs out on a
 * row that exists.
 *
 * **Then live refresh off, before the click.** A refresh landing between the click and the
 * assertion closes the panel again, which looks like a click that missed. Switching off drops a
 * refresh in flight (`Live.pause`), so nothing lands after it. The toggle is also what a person
 * would do before reading a row carefully.
 */
async function openContent(page: Page) {
  const opener = page.locator('[data-testid^="open-detail-"]').first();
  await expect(opener).toBeVisible({ timeout: 60_000 });

  const live = page.getByTestId('traces-live');
  if (await live.isChecked()) await live.uncheck();

  await opener.click();
  // The details open for every reader who sees the row, and the opening is recorded.
  await expect(page.getByTestId('detail-recorded')).toBeVisible();
  // The content is offered only to a reader who can read it; each test says which it expects.
  const read = page.locator('[data-testid^="read-payload-"]');
  if (await read.count()) await read.first().click();
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

    // Under strict data mode, the default, two modes are offered and the two that let the use
    // case's own people read content are not; switched off, all four. A mode that is offered can
    // be chosen, and the one added last is the one a template edit would drop.
    await page.goto(`/use-cases/${slug}`);
    const offered = strictDataMode(page)
      ? ['not-stored', 'oversight']
      : ['not-stored', 'oversight', 'admins-and-own', 'members'];
    for (const mode of offered) {
      await expect(page.getByTestId(`content-mode-${mode}`)).toBeVisible();
    }
    for (const mode of ['admins-and-own', 'members'].filter((m) => !offered.includes(m))) {
      await expect(page.getByTestId(`content-mode-${mode}`)).toHaveCount(0);
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
    // Three waits of up to a minute each — the key reaching the gateway, the row reaching the tab,
    // a second person signing in — which the suite's 45 seconds would cut short.
    test.slow();
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
    const key = await issueKeyAndClose(page, slug);
    // This polls until the key is live, which is itself several seconds of Kafka round trips after
    // the setting was saved — so by the time there is a trace to read, the gateway has the mode.
    await spend(page, slug, key);

    const administrator = await asUser(page, USERS.useCaseUser);
    await administrator.page.goto(`/use-cases/${slug}?tab=traces`);

    // The row is there — the request happened, and the figures are the use case's own business.
    await openContent(administrator.page);

    // **Not offered**: a button that can only answer "you may not" is a control the reader cannot
    // use, so the content is offered to the roles that can read it and to nobody else here.
    await expect(administrator.page.locator('[data-testid^="read-payload-"]')).toHaveCount(0);
    await expect(administrator.page.getByTestId('payload-request')).toHaveCount(0);
    await expect(administrator.page.locator('body')).not.toContainText(sentence(slug));
    await administrator.context.close();
  });

  test('IT Security reads the same prompt the administrator was refused', async ({ page }) => {
    // The other half, and the half that makes the first one a *governance* statement rather than a
    // storage one: the content exists, it is kept, and somebody accountable can still read it.
    // The same three waits as the test above.
    test.slow();
    await login(page, USERS.globalAdmin);
    const slug = await createUseCase(page, uniqueSlug('vis'), 'Oversight reads');
    await chooseContentMode(page, slug, 'oversight');
    await releaseAllModels(page, slug);
    const key = await issueKeyAndClose(page, slug);
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
