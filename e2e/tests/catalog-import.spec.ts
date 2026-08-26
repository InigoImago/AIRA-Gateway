import { expect, test } from '@playwright/test';
import { USERS, expectNoSqueezedControls, login, openEditorTab, openModelEditor } from './support';

/**
 * Importing what the adapters already serve, and asking the vendor what it offers (`FRD-507`).
 *
 * In a browser because the two halves live in different planes: the lists come from the **gateway**
 * through the `/gw` proxy with the browser's own token, and the form they fill is Management's. No
 * component test can tell a working proxy from a stubbed one — and the stage C flow is three
 * network hops (providers → offerings → save) with a role gate on the first two.
 */
test.describe('Catalog import', () => {
  test('lists what the gateway serves and fills in only where a model lives', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    await page.goto('/models');

    await page.getByTestId('discover-models').click();
    await expect(page.getByTestId('discover-summary')).toBeVisible({ timeout: 20_000 });

    // Something served and not catalogued, or the run has nothing to prove. `mock-1` is always
    // there in a local stack and is never in the catalog — it is a test double (`FRD-307`).
    const row = page.locator('tr').filter({ hasText: 'mock-1' }).first();
    await expect(row).toBeVisible();

    await page.getByTestId('import-mock-1').click();

    await expect(page.locator('#model-name')).toHaveValue('mock-1');
    // The boundary: a price nobody set is not zero, and a capability is a measurement. The price
    // lives on its own tab since the editor was split, so the check has to go and look — an
    // import that quietly filled in a zero would otherwise pass by the field not being on screen.
    await openEditorTab(page, 'price');
    await expect(page.locator('#model-input')).toHaveValue('');
    await expect(page.getByTestId('model-approved')).not.toBeChecked();
  });

  test('browses what a provider offers and takes one model into the editor', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    await page.goto('/models');

    await page.getByTestId('add-model').click();

    // The provider list is what **this gateway** is configured with, fetched over `/gw` with the
    // browser's own token. A hard-coded vocabulary would render identically and mean something
    // else — which is the whole reason this test is in a browser.
    const provider = page.getByTestId('browse-provider');
    await expect(provider).toBeVisible({ timeout: 20_000 });
    // Chosen, not assumed: a stack with more than one askable provider preselects none, which is
    // the whole reason the preselect is conditional. Written first without this and it failed
    // exactly there — against a gateway that has three.
    await provider.selectOption('mock');
    await expect(page.getByTestId('offerings-count')).toBeVisible({ timeout: 20_000 });

    // The list itself, not a dropdown: one real key answered with 50 models.
    await page.getByTestId('offered-mock-1').click();

    await expect(page.locator('#model-name')).toHaveValue('mock-1');
    // Copied, because the vendor stated them — and now **stated as a sentence** rather than asked
    // for in four boxes, so this is what "the console names what it took" looks like.
    await expect(page.getByTestId('provenance-summary')).toContainText('Lives on');
    await expect(page.getByTestId('provenance-summary')).toContainText('mock');
    // And the note beside it says only the other half: what the import deliberately left alone.
    await expect(page.getByTestId('vendor-filled')).toContainText('Left for you');
    // Left, because a price nobody set is not zero and a capability is a measurement.
    await openEditorTab(page, 'price');
    await expect(page.locator('#model-input')).toHaveValue('');
    await expect(page.locator('#model-output')).toHaveValue('');
    await expect(page.getByTestId('model-approved')).not.toBeChecked();
  });

  test('names the vendor in the editor rather than only its routing identifier', async ({
    page,
  }) => {
    await login(page, USERS.globalAdmin);
    await page.goto('/models');
    // Adding starts by saying where the model lives, so the editor is reached *through* the
    // provider — and arrives with the provenance already answered. The block is therefore a
    // sentence, and this test is about the field behind it.
    //
    // Through the helper because there are **two** routes to a name and which one a provider
    // offers depends on whether it publishes a list: written by hand here against `mock`, which
    // does publish one, the test waited 45 seconds for the button the *other* kind of provider
    // shows.
    await openModelEditor(page, 'mock');

    await expect(page.getByTestId('provenance-summary')).toContainText('mock');
    await page.getByTestId('change-provenance').click();

    const provider = page.getByTestId('provider-select');
    await expect(provider).toBeVisible();
    await expect(provider).toHaveValue('mock');

    // Cataloguing a model under this provider is enough to reach it (`FRD-507` stage B), and the
    // form says so — the field that separates a working import from a convincing decoration.
    await expect(page.getByTestId('provider-note')).toContainText('enough to reach it');
  });

  test('does not offer the vendor question to a role that may not declare a model', async ({
    page,
  }) => {
    // IT Security investigates across every use case and writes nothing (PRD §154). The editor is
    // Global-Admin-only, so the question is whether the *screen* stops before the control — an
    // action nobody can carry out is worse than an absent one (`FRD-206`).
    await login(page, USERS.security);
    await page.goto('/models');

    await expect(page.getByRole('heading', { name: 'Models & prices' })).toBeVisible();
    await expect(page.getByTestId('add-model')).toHaveCount(0);
    await expect(page.getByTestId('discover-models')).toHaveCount(0);
  });

  /**
   * The editor after an import, which is where the layout broke.
   *
   * Reported from the console: importing an AI Studio model and clicking "Catalogue…" packed the
   * whole form into one row. It was the vendor's note — `flex: 1` gives it `flex-basis: 0`, so it
   * never wrapped onto a line of its own and was squeezed to **67 px wide and 4818 px tall**
   * instead, taking the model-id field down to 30 px with it.
   *
   * The import path is the only one that renders those notes, which is why every other screen
   * looked fine and this one did not.
   */
  test('the editor is readable after taking a model from a listing', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    await page.goto('/models');

    await page.getByTestId('add-model').click();
    // Named, not `{ index: 1 }` on the first offering that renders. That version failed roughly
    // one run in three: the list is fetched live and grows as it arrives, so `.first()` resolves
    // against a partial render and the node it clicked is gone by the time the click lands — the
    // editor never opens and the failure reads as "the layout is broken", which it is not.
    // `offerings-count` appears only once the listing has been answered.
    await page.getByTestId('browse-provider').selectOption('mock');
    await expect(page.getByTestId('offerings-count')).toBeVisible({ timeout: 20_000 });
    await page.getByTestId('offered-mock-1').click();

    // The note is on screen — this asserts the fixed layout, not a form that never showed one.
    await expect(page.getByTestId('vendor-filled')).toBeVisible();
    await expectNoSqueezedControls(page, 'model editor after an import');

    // And the field that was crushed beside it is usable: the model id is the longest value here.
    const idBox = await page.locator('#model-name').boundingBox();
    expect(idBox?.width ?? 0).toBeGreaterThan(180);
  });

  /**
   * Reopening the picker after a cancelled import.
   *
   * Reported: open the AI Studio listing, click *Catalogue…*, cancel the editor, click *Add from
   * provider* again — the provider is still selected and **the list never loads**. `openBrowse`
   * clears the offerings and then asks for them only `if (askable.length === 1 && !browseProvider())`,
   * so a remembered provider skips the fetch. Half the state was kept and half was dropped: the
   * select says AI Studio, and there is nothing under it, forever.
   *
   * The remembered provider is deliberate — `catalogueOffered` needs it after the dialog closes —
   * so the fix is to make the list follow the selection rather than to forget it.
   */
  test('reopening the picker after a cancelled import loads the listing again', async ({
    page,
  }) => {
    await login(page, USERS.globalAdmin);
    await page.goto('/models');

    await page.getByTestId('add-model').click();
    await page.getByTestId('browse-provider').selectOption({ index: 1 });
    const chosen = await page.getByTestId('browse-provider').inputValue();
    await expect(page.locator('[data-testid^="offered-"]').first()).toBeVisible({
      timeout: 20_000,
    });

    await page.locator('[data-testid^="offered-"]').first().click();
    await page.getByRole('button', { name: /^cancel$/i }).click();

    await page.getByTestId('add-model').click();

    // The selection survives — that is the convenience, and it is not the bug.
    await expect(page.getByTestId('browse-provider')).toHaveValue(chosen);
    // And the list under it is there, which is what "still selected but nothing loads" was about.
    await expect(page.locator('[data-testid^="offered-"]').first()).toBeVisible({
      timeout: 20_000,
    });
  });
});
