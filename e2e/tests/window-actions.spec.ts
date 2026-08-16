import { expect, test } from '@playwright/test';
import { USERS, createUseCase, expectFooterActionsApart, login, uniqueSlug } from './support';

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
   * The one thing a per-head figure does not say by itself.
   *
   * Measured on the live stack: a person's API keys share **one** allowance — a key's subject is
   * its owner's name, so every key they own counts to the same place — while the same person
   * signed in through Keycloak gets a *separate* one. Nothing reconciles the two since the scope
   * that named a person by hand was removed, so somebody running an agent with a key while also
   * working in a browser has the configured figure twice over.
   *
   * In a browser because it is a warning: a component test proves the template can render it, and
   * only this shows that somebody choosing the scope meets it.
   */
  test('choosing a per-head limit says a person has one allowance', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    await page.goto('/use-cases/kundenservice?tab=rate-limits');
    await page.getByTestId('add-rate-limit').click();

    // Scoped to the window that was just opened, not to the page. The same note is printed beside
    // an **existing** per-head limit (that is the point of the sibling test below), so on a use
    // case that already has one an unqualified locator resolves to two elements and Playwright's
    // strict mode fails on the ambiguity rather than on the behaviour. `LESSONS.md` §7: an
    // unqualified role query is a query about a page that has one of something.
    const warning = page.getByTestId('rate-limit-editor').getByTestId('rl-two-pots');
    await expect(warning).toBeHidden();

    await page.getByLabel('Applies to').selectOption('each_member');

    await expect(warning).toBeVisible();
    // It said *"counted per credential — two separate limits for the same person"* until
    // `ADR-0019` keyed the counter on the person, and it was true while it stood. A note that has
    // become false is worse than none: somebody sizes a limit around it.
    await expect(warning).toContainText('Counted per person');
    await expect(warning).toContainText('Keycloak');
    await expect(warning).not.toContainText('two separate');

    // The qualifier, which matters as much: a use-case-wide figure binds everybody together, and a
    // reader told only the first half concludes that per-head is all there is.
    await expect(warning).toContainText('whole use case');

    // And the same on budgets, where the figure is money rather than a rate.
    await page.goto('/use-cases/kundenservice?tab=budgets');
    await page.getByTestId('add-budget').click();
    await page.getByLabel('Applies to').selectOption('each_member');
    await expect(page.getByTestId('budget-editor').getByTestId('budget-two-pots')).toBeVisible();
  });

  /**
   * Reported after the warning was added: *"where do I find these notes? there is nothing in
   * budgets or rate limits"*. True — it lived in the creation window, so anybody **reading** the
   * configuration never met it, which is most of the time somebody spends on those tabs.
   */
  test('the warning is on the tab too, wherever a per-head row exists', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    // **Its own row, not the seed's.** A test that depends on which limits an installation happens
    // to carry is asserting inventory — this suite has been caught by that three times, most
    // recently naming a model only one machine had.
    const slug = uniqueSlug('perhead');
    await createUseCase(page, slug, 'Per-head warning probe');
    await page.goto(`/use-cases/${slug}?tab=rate-limits`);

    // Nothing configured yet, so nothing to warn about.
    await expect(page.getByTestId('rl-two-pots')).toBeHidden();

    await page.getByTestId('add-rate-limit').click();
    await page.getByLabel('Applies to').selectOption('each_member');
    await page.getByLabel('Requests per minute').fill('60');
    await page.getByRole('button', { name: 'Add', exact: true }).click();

    // And now a reader who creates nothing meets it, which is the whole report.
    await expect(page.getByTestId('rl-two-pots')).toBeVisible({ timeout: 20_000 });
  });
});
