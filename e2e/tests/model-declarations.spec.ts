import { expect, test } from '@playwright/test';
import { USERS, login, openEditorTab, openModelEditor } from './support';

/**
 * Declaring what a model can do — the three blocks the API always accepted and the console could
 * not write (`FRD-114`).
 *
 * `thinking`, `embedding` and `attachments` were *shown* in the opened row as JSON and had no
 * field anywhere. So `all-minilm` listed in the compatibility surface with a batch flag and no
 * width: a Global Administrator could tick "embed" and had nowhere to say how wide the vectors
 * are, and the seed was the only way in. `FRD-206` inverted — a capability with no way in
 * announces itself through nothing, because an absent control reads as a design decision.
 *
 * **In a browser because the blocks are conditional on a checkbox.** A component test proves the
 * template renders them; only this can show that ticking "embed" puts a width field in front of
 * somebody, and that what they type survives a save and a reload. Both halves have failed here
 * before: a control that renders and does nothing, and a form that saves and loses a field.
 */
test.describe('Model declarations', () => {
  test('a width can be declared, saved and read back', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    await page.goto('/models');

    // What a model *is* lives in the opened row; the columns carry what a catalog is scanned by.
    await expect(page.getByTestId('open-model-all-minilm')).toBeVisible({ timeout: 20_000 });
    await page.getByTestId('open-model-all-minilm').click();
    await page.getByTestId('edit-all-minilm').click();
    await openEditorTab(page, 'capabilities');

    // The block is on screen because the model declares `embed` — that is the whole conditional.
    const dimensions = page.getByTestId('embedding-dimensions');
    await expect(dimensions).toBeVisible();
    await expect(page.getByTestId('embedding-batch')).toBeChecked();

    // **Whatever is there**, not a particular number. Written against `'384'` first, this failed
    // on its second run: the earlier attempt had saved `'384, 768'` and never reached its own
    // restore. A test that asserts the seed's value is asserting the deployment's inventory, and
    // the property here is the round trip.
    const before = (await dimensions.inputValue()) || '384';
    const changed = before.includes('768') ? '384' : `${before}, 768`;

    await dimensions.fill(changed);
    await page.getByRole('button', { name: /^save$/i }).click();

    // **Reloaded**, not merely reopened. Reopening within the same session raced the table's own
    // refresh — the click resolved against a row that was being replaced — and it would also have
    // proved less: a reload fetches the catalog again, so what comes back is what was stored.
    await expect(dimensions).toBeHidden();
    await page.reload();
    await expect(page.getByTestId('open-model-all-minilm')).toBeVisible({ timeout: 20_000 });
    await page.getByTestId('open-model-all-minilm').click();
    await page.getByTestId('edit-all-minilm').click();
    // The editor opens on Identity every time, so the reopened one needs the tab again — the
    // round trip is only proved by reading the field back where it actually lives.
    await openEditorTab(page, 'capabilities');
    await expect(dimensions).toHaveValue(changed);

    // Put back, so the next run and the demo find the measured declaration.
    await dimensions.fill(before);
    await page.getByRole('button', { name: /^save$/i }).click();
    await expect(dimensions).toBeHidden();
  });

  test('a block appears only when its capability is ticked', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    await page.goto('/models');

    await expect(page.getByTestId('open-model-qwen3:0.6b')).toBeVisible({ timeout: 20_000 });
    await page.getByTestId('open-model-qwen3:0.6b').click();
    await page.getByTestId('edit-qwen3:0.6b').click();
    await openEditorTab(page, 'capabilities');

    // This one reasons and does not embed, so exactly one of the two is on screen — which is the
    // property: a thinking budget on a model that does not think is a declaration the validator
    // refuses and a reader has to guess the meaning of.
    await expect(page.getByTestId('thinking-block')).toBeVisible();
    await expect(page.getByTestId('embedding-block')).toBeHidden();

    await expect(page.getByTestId('mode-disabled')).toBeChecked();
    await expect(page.getByTestId('thinking-default')).toHaveValue('disabled');
  });

  test('the standing decisions sit below the tabs, separated, and on none of them', async ({
    page,
  }) => {
    /** Reported: *"Approved for use / Deprecated is now present in every tab."* They were — after
     *  the form, before the footer, flush against the last input with nothing between. A checkbox
     *  touching the field above it belongs to that field's section to every reader, whichever tab
     *  is open.
     *
     *  Asserted as geometry rather than as a CSS rule: below the tab strip, below the form, and
     *  with a visible rule of its own. The next arrangement that puts them back inside a tab's
     *  content should fail here too. */
    await login(page, USERS.globalAdmin);
    await page.goto('/models');
    await openModelEditor(page);

    const standing = page.getByTestId('editor-standing');
    await expect(standing).toBeVisible();

    const geometry = await standing.evaluate((el) => {
      const strip = document.querySelector('.modal__body > .tabs') as HTMLElement;
      const form = document.querySelector('#model-editor-form') as HTMLElement;
      const box = el.getBoundingClientRect();
      return {
        belowStrip: box.top >= strip.getBoundingClientRect().bottom,
        belowForm: box.top >= form.getBoundingClientRect().bottom - 1,
        insideForm: form.contains(el),
        rule: getComputedStyle(el).borderTopWidth,
      };
    });

    expect(geometry.insideForm, 'it must not be part of a tab panel').toBe(false);
    expect(geometry.belowStrip, 'it sits below the tab strip').toBe(true);
    expect(geometry.belowForm, 'it sits below the tabbed fields').toBe(true);
    expect(geometry.rule, 'a rule separates it from the tabbed fields').not.toBe('0px');

    // And it stays put across tabs — the same element, not one per panel.
    for (const tab of ['capabilities', 'price', 'identity'] as const) {
      await openEditorTab(page, tab);
      await expect(page.getByTestId('editor-standing')).toHaveCount(1);
    }
  });

  test('the tab strip and the standing decisions do not move when the tab changes', async ({
    page,
  }) => {
    /** Reported: *"the two toggles now jump with the size of the tab contents, which looks
     *  unprofessional."* They did. `modal--steady` fixed the dialog's height, which keeps the
     *  *window* still and lets everything inside it slide: the body was one scrolling block, so
     *  the strip and the band sat wherever the current tab's fields happened to end.
     *
     *  Measured, because this is a claim about pixels and nothing else can hold it: the strip's
     *  top and the band's top are identical on all three tabs, whose field counts differ by more
     *  than a screen. Asserted to the pixel — a tolerance here would be a licence for the next
     *  layout to move it "only a little", which is the thing being complained about. */
    await login(page, USERS.globalAdmin);
    await page.goto('/models');
    await openModelEditor(page);

    const measure = async () =>
      page.evaluate(() => {
        const strip = document.querySelector('.modal__body > .tabs') as HTMLElement;
        const band = document.querySelector('[data-testid="editor-standing"]') as HTMLElement;
        return {
          strip: Math.round(strip.getBoundingClientRect().top),
          band: Math.round(band.getBoundingClientRect().top),
        };
      });

    const seen: Record<string, { strip: number; band: number }> = {};
    for (const tab of ['identity', 'capabilities', 'price'] as const) {
      await openEditorTab(page, tab);
      seen[tab] = await measure();
    }

    const positions = Object.entries(seen);
    for (const [tab, box] of positions) {
      expect(box.strip, `the tab strip moved on ${tab}: ${JSON.stringify(seen)}`).toBe(
        positions[0][1].strip,
      );
      expect(box.band, `the standing decisions moved on ${tab}: ${JSON.stringify(seen)}`).toBe(
        positions[0][1].band,
      );
    }
  });
});
