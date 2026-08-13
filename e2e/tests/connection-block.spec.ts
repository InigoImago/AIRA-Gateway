import { expect, test } from '@playwright/test';
import { USERS, createUseCase, login, uniqueSlug } from './support';

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
test.describe('Connecting a client', () => {
  test('shows both surfaces, with a base URL and an example for each', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    await page.goto('/use-cases/kundenservice');

    const block = page.getByTestId('connection');
    await expect(block).toBeVisible({ timeout: 20_000 });
    await block.scrollIntoViewIfNeeded();

    // Two surfaces, two tabs — alternatives rather than steps, so only one is on screen. A reader
    // migrating a KIRA client should not have to read the Gemini half to find their own.
    await expect(page.getByTestId('connection-gemini-base')).toContainText('/v1beta');
    await expect(page.getByTestId('connection-kira-base')).toHaveCount(0);

    await page.getByTestId('conn-tab-kira').click();
    await expect(page.getByTestId('connection-kira-base')).toContainText('/kira/api/external');
    await expect(page.getByTestId('connection-gemini-base')).toHaveCount(0);

    // The address is described rather than asserted as *the* gateway: it is the console's proxy,
    // which works from anywhere that reaches the console and is not what a client elsewhere uses.
    await expect(page.getByTestId('connection-base-note')).toContainText('this console');
  });

  test('names the models this use case may call, with the id a KIRA client needs', async ({
    page,
  }) => {
    await login(page, USERS.globalAdmin);
    await page.goto('/use-cases/kundenservice');
    await expect(page.getByTestId('connection')).toBeVisible({ timeout: 20_000 });

    await page.getByTestId('conn-tab-kira').click();
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

  test('the examples name a model this use case is actually allowed to call', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    await page.goto('/use-cases/kundenservice');
    await expect(page.getByTestId('connection')).toBeVisible({ timeout: 20_000 });

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
    await expect(page.getByTestId('connection')).toBeVisible({ timeout: 20_000 });

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
  test('explains the path prefix, for a client that can set a URL and not a header', async ({
    page,
  }) => {
    await login(page, USERS.globalAdmin);
    await page.goto('/use-cases/kundenservice');
    await expect(page.getByTestId('connection')).toBeVisible({ timeout: 20_000 });

    // It was half a sentence in a hover hint. A caller whose client can only be given a base URL
    // does not go looking in a hint — they conclude the gateway has no way to do what they need.
    const section = page.getByTestId('connection-attribution');
    await expect(section).toContainText('/uc/');
    await expect(section).toContainText('403');
    await expect(page.getByTestId('connection-uc-example')).toContainText(
      '/uc/kundenservice/v1beta',
    );

    // And it follows the open tab, because a prefix example naming the other surface is the one
    // thing on this panel most likely to be pasted unread.
    await page.getByTestId('conn-tab-kira').click();
    await expect(page.getByTestId('connection-uc-example')).toContainText(
      '/uc/kundenservice/kira/api/external',
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

    const block = page.getByTestId('connection');
    await expect(block).toBeVisible({ timeout: 20_000 });

    await expect(block.getByRole('button', { name: /issue a key/i })).toHaveCount(0);
    await expect(block.getByRole('link', { name: /issue a key/i })).toHaveCount(0);

    // The route that does exist: the page's own tab bar, and it lands on the panel that issues.
    // Asserted on the panel, not on the tab's own `aria-selected` — a tab can mark itself active
    // while showing nothing, which is the defect this whole test replaced.
    await page.getByRole('tab', { name: /API keys/i }).click();
    await expect(page.getByRole('tabpanel')).toContainText('API keys');
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
    await expect(page.getByRole('tabpanel')).toContainText('API keys');
  });
});
