import { expect, test } from '@playwright/test';
import { USERS, expectFooterActionsApart, login } from './support';

/**
 * What a window's foot and its "one named person" field owe the reader.
 *
 * Both were reported from the console while adding a rate limit, and both turned out to be true of
 * more than the screen they were noticed on.
 */
test.describe('Window actions', () => {
  /**
   * Every window, not the one it was reported on: the footer's `gap` applied to the projected
   * wrapper rather than to the buttons, so **all** of them sat at 0 px apart. A test naming only
   * the rate-limit window would have gone green the day somebody fixed that one by hand.
   */
  test('the two decisions at the foot of a window are not touching', async ({ page }) => {
    await login(page, USERS.globalAdmin);

    await page.goto('/use-cases/kundenservice?tab=rate-limits');
    await page.getByTestId('add-rate-limit').click();
    await expectFooterActionsApart(page, 'rate-limit-editor');

    await page.goto('/use-cases/kundenservice?tab=budgets');
    await page.getByTestId('add-budget').click();
    await expectFooterActionsApart(page, 'budget-editor');

    await page.goto('/use-cases/kundenservice?tab=keys');
    await page.click('button:has-text("Issue key")');
    await expectFooterActionsApart(page, 'key-editor');
  });
});
