import { expect, test } from '@playwright/test';
import { USERS, api, createUseCase, issueKey, login, uniqueSlug } from './support';

/** The model every stack catalogues and serves itself, so a released example costs nothing. */
const LOCAL_MODEL = 'qwen3:0.6b';

/**
 * How to call a use case, on the page that governs it.
 *
 * The console said everything about *governing* a use case and nothing about *using* one, so
 * somebody who had just issued a key had no address to put it against. That is `FRD-206`'s
 * complaint from the other side: a control that refuses when used announces itself, a missing
 * instruction never does — the reader concludes the feature is unfinished, or that they missed a
 * page. It was found by somebody asking.
 *
 * **In a browser because the whole claim is a crossing.** The block's examples are built from this
 * use case's release (Management), the model ids the gateway assigns (the gateway's own KIRA
 * listing, over the `/gw` proxy) and the address the console itself reaches the gateway at. A
 * component test can prove the template renders a string; only a browser can show that the string
 * describes *this* deployment. And the copy buttons are exactly the kind of control this repository
 * has shipped inert twice — a `title` attribute that showed nothing, a `routerLinkActive` that
 * styled nothing — both invisible to every layer but this one.
 */
/**
 * The block is a **disclosure**, shut when the page loads.
 *
 * It was 3467 px of a 3849 px overview — a reference somebody reads once when wiring a client up
 * and scrolls past every day after, so the owner asked for it folded. Every test below is about
 * what is *inside* it, so every one opens it first; the summary is the control a reader clicks,
 * so that is what these click too rather than setting `open` from script.
 */
async function openConnectionBlock(page: import('@playwright/test').Page) {
  const block = page.getByTestId('connection');
  await expect(block).toBeVisible({ timeout: 20_000 });
  await block.locator('summary').click();
  // The disclosure is what makes the contents queryable at all; asserting it is open here means a
  // failure below is about the block's *contents* rather than about the click.
  await expect(block).toHaveJSProperty('open', true);
  return block;
}

test.describe('Connecting a client', () => {
  test('gives each interface an address that names the use case', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    await page.goto('/use-cases/kundenservice');

    // The answer to "where do I send it" is on the shut card already, address included.
    const summary = page.getByTestId('connection-summary');
    await expect(summary).toContainText('/uc/kundenservice/v1beta', { timeout: 20_000 });

    await openConnectionBlock(page);
    // Every model call names its use case, whatever the credential (`FRD-149`): an address
    // without the prefix is one a key is refused at.
    await expect(page.getByTestId('connection-gemini-base')).toHaveText(
      /\/uc\/kundenservice\/v1beta$/,
    );
    await expect(page.getByTestId('connection-kira-base')).toHaveText(
      /\/uc\/kundenservice\/kira\/api\/external$/,
    );

    // The address is described rather than asserted as *the* gateway: it is the console's proxy,
    // which works from anywhere that reaches the console and is not what a client elsewhere uses.
    await expect(page.getByTestId('connection-base-note')).toContainText('this console');
  });

  test('names the models this use case may call, with the id a KIRA client needs', async ({
    page,
  }) => {
    await login(page, USERS.globalAdmin);
    await page.goto('/use-cases/kundenservice');
    await openConnectionBlock(page);

    const table = page.getByTestId('connection-models');
    await expect(table).toBeVisible();

    // The integer is the one field a migrating client actually has to fill in, and it is the one
    // nobody could see: it lives in the catalog, which an administrator of a use case may not read.
    // Asserted as *a number*, not as a particular one — which id a deployment assigned is its own
    // business, and pinning it here would be a test of the seed.
    const ids = page.getByTestId('connection-kira-id');
    await expect(ids.first()).toBeVisible();
    const shown = await ids.allTextContents();
    expect(shown.some((text) => /\d/.test(text))).toBe(true);
  });

  /**
   * **The example works when it is pasted.** The panel's whole claim, made where it can be checked:
   * the generation example's address is read off the page and sent a request with a key issued on
   * the same page — through the console's proxy, with the use case in the path. A model released
   * on purpose: the stack's own local one, so nothing is spent. Not the mock, which every gateway
   * serves and no catalogue lists, so there is nothing to release.
   */
  test('the generation example is served, as written, with a key from this page', async ({
    page,
  }) => {
    await login(page, USERS.globalAdmin);
    const slug = await createUseCase(page, uniqueSlug('connect'), 'Connection example probe');
    const released = await api(page, 'PATCH', `/api/v1/use-cases/${slug}/`, {
      allowed_models: [LOCAL_MODEL],
    });
    expect(released.status, JSON.stringify(released.body)).toBeLessThan(300);
    const key = await issueKey(page, slug, 'connection-example');

    await page.goto(`/use-cases/${slug}`);
    await openConnectionBlock(page);
    const example = (await page.getByTestId('connection-gemini-chat').textContent()) ?? '';
    const url = example.match(/curl -s (\S+)/)?.[1];
    expect(url, example).toContain(`/uc/${slug}/v1beta/models/${LOCAL_MODEL}:generateContent`);

    // A key reaches the gateway over Kafka, so it is valid within a moment rather than at once.
    await expect
      .poll(
        async () =>
          (
            await page.request.post(url!, {
              headers: { 'x-goog-api-key': key, 'content-type': 'application/json' },
              data: {
                contents: [{ parts: [{ text: 'Say OK.' }] }],
                generationConfig: { maxOutputTokens: 8 },
              },
              timeout: 180_000,
            })
          ).status(),
        { timeout: 60_000, intervals: [1000] },
      )
      .toBe(200);

    // And the same address without the use case is refused, with the prefix to add: the
    // requirement is real, not only written down.
    const unnamed = await page.request.post(url!.replace(`/uc/${slug}`, ''), {
      headers: { 'x-goog-api-key': key, 'content-type': 'application/json' },
      data: { contents: [{ parts: [{ text: 'Say OK.' }] }] },
    });
    expect(unnamed.status()).toBe(400);
    expect(await unnamed.text()).toContain(`/uc/${slug}`);
  });

  test('the examples name a model this use case is actually allowed to call', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    await page.goto('/use-cases/kundenservice');
    await openConnectionBlock(page);

    // The property that matters. An example naming a model the gateway would refuse is an example
    // that fails when it is pasted — and the reader debugs their client rather than the release.
    //
    // Checked against the **release picker's chips**, not against `release-summary`, which counts
    // ("2 of 3 released") and names nothing — a substring test against it would have passed on any
    // model whose name happened to contain a digit. The chips are the list a reader would compare
    // by eye, rendered by a different component.
    // **Greedy up to the verb, because a model name may contain a colon.** `[^:]+` stops at the
    // first one and matched nothing at all against `qwen3:0.6b:generateContent` — the same mistake
    // that once produced "Model 'qwen3' not found" in the gateway itself, made again here by a
    // regex rather than by a split.
    const example = await page.getByTestId('connection-gemini-chat').textContent();
    const model = example?.match(/models\/(.+):generateContent/)?.[1];
    expect(model, 'the generation example names no model').toBeTruthy();

    const chips = await page
      .getByTestId('release-picker-chosen')
      .locator('.chip')
      .allTextContents();
    expect(
      chips.length,
      'the release picker shows no chosen model to compare against',
    ).toBeGreaterThan(0);
    expect(
      chips.some((chip) => chip.includes(model!)),
      `the example offers ${model}, which is not among the released models ${chips.join(', ')}`,
    ).toBe(true);
  });

  test('a copy button copies, and says that it did', async ({ page, context }) => {
    // Granted rather than assumed: without permission the click silently takes the failure path,
    // and a test asserting "something happened" would pass on the wrong branch.
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    await login(page, USERS.globalAdmin);
    await page.goto('/use-cases/kundenservice');
    await openConnectionBlock(page);

    await page.getByTestId('conn-tab-kira').click();
    const shown = await page.getByTestId('connection-kira-chat').textContent();
    await page.getByTestId('copy-kira-chat').click();

    await expect(page.getByTestId('copy-kira-chat')).toHaveText(/Copied/);
    const clipboard = await page.evaluate(() => navigator.clipboard.readText());
    // What reached the clipboard, not merely that the label changed. A button wired to the wrong
    // example is one that works and hands the reader somebody else's command.
    expect(clipboard).toBe(shown?.trim());
  });

  test('a use case with nothing released says so instead of showing an empty block', async ({
    page,
  }) => {
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('conn');
    await createUseCase(page, slug, 'Connection probe');

    // `FRD-308`: a use case starts able to call nothing. A connection block that rendered an
    // example here would hand somebody a command that is refused — worse than an empty block,
    // because it reads as working.
    await expect(page.getByTestId('connection-nothing-released')).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId('connection-gemini-chat')).toHaveCount(0);
  });
  test('explains why every address names the use case, and the header instead', async ({
    page,
  }) => {
    await login(page, USERS.globalAdmin);
    await page.goto('/use-cases/kundenservice');
    await openConnectionBlock(page);

    // Stated in the panel, not in a hover hint: a reader whose old client sent no use case meets
    // a refusal, and the reason has to be where they look.
    const section = page.getByTestId('connection-attribution');
    await expect(section).toContainText('/uc/kundenservice');
    await expect(section).toContainText('a key too');
    await expect(section).toContainText('403');
    await expect(page.getByTestId('connection-header-example')).toContainText(
      'X-AIRA-Use-Case: kundenservice',
    );
  });

  /**
   * The block offers no key-issuing control, on purpose.
   *
   * One was added with it and did nothing — a `routerLink` with `fragment="api-keys"`, where the
   * page selects its tab from a query parameter and calls it `keys`, and where the parent reads
   * that parameter from the route *snapshot* so the same route with a different one moves nothing.
   * The owner chose removal over repair: the tab bar above already leads to where keys are issued,
   * and a second route to one place is a second thing that can rot.
   *
   * In a browser because that is where it was found. The three defects were each invisible to a
   * component test, which sees a rendered anchor and has no opinion about where it goes — and this
   * is the third inert control this repository has shipped, after a `title` attribute that showed
   * nothing and a `routerLinkActive` that styled nothing.
   */
  test('offers no key control of its own, and the tab bar still leads to one', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    await page.goto('/use-cases/kundenservice');

    const block = await openConnectionBlock(page);
    await expect(block).toBeVisible({ timeout: 20_000 });

    await expect(block.getByRole('button', { name: /issue a key/i })).toHaveCount(0);
    await expect(block.getByRole('link', { name: /issue a key/i })).toHaveCount(0);

    // The route that does exist: the page's own tab bar, and it lands on the panel that issues.
    // Asserted on the panel, not on the tab's own `aria-selected` — a tab can mark itself active
    // while showing nothing, which is the defect this whole test replaced.
    //
    // **The page's panel by name.** A bare `getByRole('tabpanel')` matched exactly one element
    // while the connection block was shut; opening it puts a second tab strip on the page — the
    // two surfaces — and the assertion became a strict-mode violation that named the *overview*
    // panel. An unqualified role query is a query about a page that has one of something.
    await page.getByRole('tab', { name: /API keys/i }).click();
    await expect(page.getByRole('tabpanel', { name: /API keys/i })).toContainText('API keys');
  });

  /**
   * The two things the block did not say, both found by reading it rather than by any suite.
   *
   * Every example asked for `<your key>` and nothing said where one comes from — the gap the
   * removed button left, an instruction with no destination one indirection out. And bearer tokens
   * were missing outright, although they are how a person or a service account calls the gateway
   * with no key minted at all, and the only way a Keycloak group grant reaches the data plane.
   *
   * In a browser because both are claims about what a reader can find on the page: a component
   * test proves a string is rendered, not that somebody scrolling this card meets it.
   */
  test('says where a key comes from and how a bearer token is used', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    await page.goto('/use-cases/kundenservice');

    await openConnectionBlock(page);
    const credentials = page.getByTestId('connection-credentials');
    await expect(credentials).toBeVisible({ timeout: 20_000 });
    await credentials.scrollIntoViewIfNeeded();

    // The key: where it is issued, and the forms the gateway accepts.
    await expect(credentials).toContainText('API keys');
    await expect(page.getByTestId('connection-key-forms')).toContainText('x-goog-api-key');

    // The bearer: the header, and the one fact that separates it from a key.
    await expect(page.getByTestId('connection-bearer-example')).toContainText(
      'Authorization: Bearer',
    );
    await expect(credentials).toContainText('carries no use');

    // And the tab it points at is real — the failure this block has already had twice.
    await page.getByRole('tab', { name: /API keys/i }).click();
    // By name, for the reason given above: an open connection block puts a second tab strip
    // on the page, so an unqualified `tabpanel` names two things.
    await expect(page.getByRole('tabpanel', { name: /API keys/i })).toContainText('API keys');
  });
});
