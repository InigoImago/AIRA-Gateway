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

  /**
   * "One named person" was a bare text box: the reader had to know the username, spelled exactly,
   * with nothing on the page to check it against.
   *
   * A `datalist`, so the people this use case has are offered **and anything typed is still
   * accepted**. That is not politeness — a rule names a *subject*, and access can come through a
   * Keycloak group, so somebody granted that way belongs to no membership row at all (`FRD-209`).
   * A picker would be narrower than the rule it fills in, which is the conclusion `FRD-604`
   * already reached for a key's owner.
   */
  test('naming a person offers this use case’s people without refusing anybody else', async ({
    page,
  }) => {
    await login(page, USERS.globalAdmin);
    await page.goto('/use-cases/kundenservice?tab=rate-limits');
    await page.getByTestId('add-rate-limit').click();
    await page.getByLabel('Applies to').selectOption('member');

    const field = page.getByTestId('rl-subject');
    await expect(field).toBeVisible();

    // Suggestions come from the members the page already loaded.
    const list = await field.getAttribute('list');
    expect(list).toBeTruthy();
    const options = page.locator(`#${list} option`);
    await expect(options.first()).toHaveCount(1);

    // And a name that is in no membership row is still accepted — the group case.
    await field.fill('service-account-from-a-group');
    await expect(field).toHaveValue('service-account-from-a-group');
  });

  /**
   * The cost of a field that accepts anything, made visible.
   *
   * Reported straight after the suggestions were added: *"now I can write any rubbish into the
   * restriction and cover no member at all."* Exactly so — and refusing what is not in the list is
   * not the answer, because access can come through a Keycloak group and somebody granted that way
   * belongs to no membership row (`FRD-209`). A rule naming nobody would otherwise save cleanly and
   * sit in the list looking exactly like a working one: a control that is configured, displayed as
   * active, and applies to nothing.
   *
   * So the console says what it **knows**, at both moments that matter — while it is being typed,
   * and afterwards on the saved rule, which is the one a typo from last week is found in.
   */
  test('a name that matches no member is called out, and still allowed', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    await page.goto('/use-cases/kundenservice?tab=rate-limits');
    await page.getByTestId('add-rate-limit').click();
    await page.getByLabel('Applies to').selectOption('member');

    const field = page.getByTestId('rl-subject');
    const warning = page.getByTestId('rl-subject-unmatched');
    await expect(warning).toBeHidden();

    await field.fill('nobody-by-this-name');
    await expect(warning).toBeVisible();
    await expect(warning).toContainText('No member of this use case is called');

    // Still saveable — the warning is information, not a gate, and the group case is legitimate.
    await page.getByLabel('Requests per minute').fill('60');
    await expect(page.getByRole('button', { name: 'Add', exact: true })).toBeEnabled();

    // And a real member clears it, which is what makes the warning mean something.
    await field.fill('ucadmin');
    await expect(warning).toBeHidden();
  });
});
