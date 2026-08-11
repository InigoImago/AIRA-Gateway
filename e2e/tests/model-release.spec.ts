import { expect, test } from '@playwright/test';
import { USERS, login } from './support';

/**
 * Which models a use case may call (`FRD-308`).
 *
 * In a browser because the decision crosses both planes and a queue: the console writes it to
 * Management, an event carries it to the gateway's read-model, and the gateway enforces it at
 * every hop of routing and fallback. A component test can prove the checkbox works and nothing
 * about whether the model is actually refused.
 */
test.describe('Model release', () => {
  test('says which models a use case may call, and names the empty case', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    await page.goto('/use-cases/kundenservice');

    const panel = page.getByText('Models this use case may call');
    await expect(panel).toBeVisible({ timeout: 20_000 });
    await panel.scrollIntoViewIfNeeded();

    // Either state is a real answer; what must never happen is neither being said.
    const released = page.getByTestId('release-summary');
    const nothing = page.getByTestId('nothing-released');
    expect((await released.count()) + (await nothing.count())).toBe(1);

    if (await nothing.count()) {
      await expect(nothing).toContainText('cannot call anything');
    } else {
      await expect(released).toContainText('and no others');
    }
  });

  test('releases a model and takes it away again', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    await page.goto('/use-cases/personalwesen');
    await page.getByText('Models this use case may call').scrollIntoViewIfNeeded();

    // Driven through the picker, keyboard first — which is the half a mouse-only test never
    // reaches, and the half with no fallback underneath it.
    const search = page.getByTestId('release-picker-search');
    await expect(search).toBeVisible({ timeout: 20_000 });
    const before = await page.getByTestId('release-picker-chosen').locator('.chip').count();

    await search.click();
    await search.press('ArrowDown');
    await search.press('Enter');
    // Opening is not moving: ArrowDown into a closed list lands on the **first** option, so
    // exactly one thing changed.
    await expect(page.getByTestId('release-picker-chosen').locator('.chip')).toHaveCount(
      before === 0 ? 1 : before === 1 ? 0 : before - 1,
    );

    // Escape first, deliberately: the list floats over what follows and stays open so several
    // models can be picked in one go, so a reader closes it before reaching for Save. That is the
    // combobox contract, and asserting it here is what keeps Escape working.
    await search.press('Escape');
    await page.getByTestId('save-release').click();
    // The page's **one** banner (`role="status"`), not any callout: this screen also carries a
    // standing warning about payload storage, and matching both would make the assertion pass on
    // text that was already there before the click.
    await expect(page.locator('[role="status"]')).toContainText(/released|refused/, {
      timeout: 20_000,
    });

    // Put it back, so the run leaves the demo as it found it.
    await search.click();
    await search.press('ArrowDown');
    await search.press('Enter');
    await search.press('Escape');
    await page.getByTestId('save-release').click();
    await expect(page.getByTestId('save-release')).toBeDisabled({ timeout: 20_000 });
  });

  test('offers no way to change it to somebody who only oversees', async ({ page }) => {
    // IT Steuerung sees every figure and writes nothing anywhere (PRD §154). Read-only means
    // **inert, not un-saveable**: the list is still worth reading (`FRD-206`).
    await login(page, USERS.governance);
    await page.goto('/use-cases/kundenservice');

    await expect(page.getByTestId('release-readonly')).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId('save-release')).toHaveCount(0);
  });
});
