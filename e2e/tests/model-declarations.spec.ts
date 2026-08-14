import { expect, test } from '@playwright/test';
import { USERS, login } from './support';

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

    // This one reasons and does not embed, so exactly one of the two is on screen — which is the
    // property: a thinking budget on a model that does not think is a declaration the validator
    // refuses and a reader has to guess the meaning of.
    await expect(page.getByTestId('thinking-block')).toBeVisible();
    await expect(page.getByTestId('embedding-block')).toBeHidden();

    await expect(page.getByTestId('mode-disabled')).toBeChecked();
    await expect(page.getByTestId('thinking-default')).toHaveValue('disabled');
  });
});
