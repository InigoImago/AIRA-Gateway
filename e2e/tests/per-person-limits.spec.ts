import { APIRequestContext, Page, expect, test } from '@playwright/test';
import {
  USERS,
  createUseCase,
  login,
  releaseAllModels,
  submitOfOpenForm,
  uniqueSlug,
} from './support';

const GATEWAY = process.env.AIRA_E2E_GATEWAY_URL ?? 'http://localhost:8001';

async function issueKey(page: Page, slug: string): Promise<string> {
  await page.goto(`/use-cases/${slug}?tab=keys`);
  await page.click('button:has-text("Issue key")');
  await page.fill('#key-label', 'per-person-e2e');
  await (await submitOfOpenForm(page)).click();
  const secret = page.locator('.secret');
  await expect(secret).toBeVisible();
  return (await secret.textContent())?.trim() ?? '';
}

async function ask(request: APIRequestContext, key: string) {
  return request.post(`${GATEWAY}/v1beta/models/mock-1:generateContent`, {
    headers: { 'x-goog-api-key': key },
    data: { contents: [{ role: 'user', parts: [{ text: 'per-person probe' }] }] },
  });
}

/**
 * A budget and a rate limit **per head** (`FRD-400` §2.1), through the real browser.
 *
 * Why this belongs in the fourth layer rather than only in the unit suites: the whole feature is a
 * new value travelling from a `<select>` through Management, Kafka and the gateway's read-model to
 * a counter key. Every hop of that is defended hermetically and none of them can see a dropped
 * option, a scope the serializer silently coerces, or a card that names the row "Whole use case"
 * — which is the one wrong conclusion this scope could lead a reader to, and which no unit test
 * of the enforcement would ever notice.
 *
 * The window is asserted here too, for the same reason: `FRD-206`'s pass shipped two defects that
 * only a browser could see, both in code that only a browser runs.
 */
test.describe('Per-person limits', () => {
  test('a budget can be set for each person without naming anybody', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('perhead');
    await createUseCase(page, slug, 'Per-head probe');

    await page.goto(`/use-cases/${slug}?tab=budgets`);
    await page.click('button:has-text("Add budget")');

    // The form opens in a window, and the window is what carries the action row.
    const editor = page.locator('[data-testid="budget-editor"]');
    await expect(editor).toBeVisible();

    await page.selectOption('#budget-scope', 'each_member');
    // No username field: that is the whole point of the scope, and a form that asked for one
    // would be asking for a name it then throws away.
    await expect(page.locator('#budget-subject')).toHaveCount(0);
    await expect(editor).toContainText('including people who join later');

    await page.fill('#budget-cost', '25.00');
    await (await submitOfOpenForm(page)).click();

    await expect(editor).toBeHidden();
    // `.last()`: the tab panel is itself a `.card`, so it matches its own contents.
    const card = page.locator('.card', { hasText: 'Each member, individually' }).last();
    await expect(card).toBeVisible();
    // Named as what it is. "Whole use case" here would describe a shared pot, which is the
    // opposite governance decision.
    await expect(card).toContainText('/ 25.000000');
  });

  test('the same scope exists for a rate limit, and Burst says what it does', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('perheadrl');
    await createUseCase(page, slug, 'Per-head rate probe');

    await page.goto(`/use-cases/${slug}?tab=rate-limits`);
    await page.click('[data-testid="add-rate-limit"]');

    await page.selectOption('#rl-scope', 'each_member');
    await expect(page.locator('#rl-subject')).toHaveCount(0);

    // Burst was reported unclear by the person who had configured it. The explanation has to be
    // reachable from the field, not from a document somebody would have to know exists.
    await page.hover('[data-testid="info-rl-burst"]');
    const hint = page.locator('[data-testid="help-rl-burst"]');
    await expect(hint).toBeVisible();
    await expect(hint).toContainText('bucket');

    await page.fill('#rl-rpm', '60');
    await (await submitOfOpenForm(page)).click();

    await expect(page.locator('[data-testid="rate-limit-editor"]')).toBeHidden();
    await expect(page.locator('tbody')).toContainText('Each member, individually');
  });

  test('the gateway enforces it, and says which allowance ran out', async ({ page, request }) => {
    /** The claim the console cannot make on its own: a scope that saves and distributes but is
     *  never *read* is `FRD-125`'s badge-wearing absent control. One request, then a refusal —
     *  measured against a real gateway rather than against a stand-in that agrees with us by
     *  construction.
     *
     *  Two callers each getting their own counter is asserted hermetically (`S8`/`S9`): a second
     *  identity here would need a second person with access to this use case, which is a fixture
     *  about the directory rather than about this rule.
     */
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('perheadgw');
    await createUseCase(page, slug, 'Per-head enforcement probe');
    await releaseAllModels(page, slug);

    await page.goto(`/use-cases/${slug}?tab=budgets`);
    await page.click('button:has-text("Add budget")');
    await page.selectOption('#budget-scope', 'each_member');
    await page.fill('#budget-requests', '1');
    await (await submitOfOpenForm(page)).click();
    await expect(page.locator('[data-testid="budget-editor"]')).toBeHidden();

    const key = await issueKey(page, slug);

    // The key and the budget travel over Kafka; the first served request is the signal that both
    // have arrived. Polling for it rather than sleeping, because the wait is a queue, not a clock.
    await expect
      .poll(async () => (await ask(request, key)).status(), {
        timeout: 30_000,
        message: 'the issued key never reached the gateway',
      })
      .toBe(200);

    const refused = await ask(request, key);
    expect(refused.status()).toBe(429);
    expect(JSON.stringify(await refused.json())).toContain('Request budget');
  });

  test('a per-person rate is enforced per person, not per use case', async ({ page, request }) => {
    /** The budget above proves the *how much*; this proves the *how fast*, which is a different
     *  store (Redis) reached through a different service. Both were changed by the same scope and
     *  only one of them needed repairing — which is precisely why neither should be taken on
     *  trust.
     */
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('perheadrate');
    await createUseCase(page, slug, 'Per-head rate enforcement probe');
    await releaseAllModels(page, slug);

    await page.goto(`/use-cases/${slug}?tab=rate-limits`);
    await page.click('[data-testid="add-rate-limit"]');
    await page.selectOption('#rl-scope', 'each_member');
    await page.fill('#rl-rpm', '60');
    await page.fill('#rl-burst', '1'); // one at a time: the second arrives before the refill
    await (await submitOfOpenForm(page)).click();
    await expect(page.locator('[data-testid="rate-limit-editor"]')).toBeHidden();

    const key = await issueKey(page, slug);
    await expect
      .poll(async () => (await ask(request, key)).status(), {
        timeout: 30_000,
        message: 'the issued key never reached the gateway',
      })
      .toBe(200);

    const refused = await ask(request, key);
    expect(refused.status()).toBe(429);
    // A well-behaved client is told when to come back, not merely that it was refused.
    expect(refused.headers()['retry-after']).toBeTruthy();
  });
});
