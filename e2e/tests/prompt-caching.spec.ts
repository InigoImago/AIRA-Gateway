import { expect, test } from '@playwright/test';
import { USERS, createUseCase, login, uniqueSlug } from './support';

/**
 * The two caching controls, in a real browser (`FRD-133` stage C).
 *
 * Everything below the console is covered hermetically and by mutation. What only a browser can
 * answer is whether the controls *do* anything when used, and whether the explanations are
 * *shown* rather than merely present in the template — the two defects `FRD-206` and `FRD-505`
 * both shipped: a `title` attribute that displayed nothing, and an `InfoHint` given `text=` when
 * it takes projected content, which Angular ignores in silence.
 *
 * The lifetime is the only tunable parameter, and the reason it is worth a test of its own is
 * that its two options are not "shorter" and "longer" but "cheaper to write" and "about double" —
 * a reader who cannot see the price will pick the long one every time.
 */
test.describe('Prompt caching', () => {
  test('the switch and its lifetime survive a reload', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('caching');
    await createUseCase(page, slug, 'Caching probe');

    await page.goto(`/use-cases/${slug}`);
    // Off by default, and the lifetime is not offered until there is something to keep alive.
    await expect(page.locator('#prompt-caching')).not.toBeChecked();
    await expect(page.locator('#cache-ttl')).toHaveCount(0);

    await page.click('label:has(#prompt-caching)');
    await expect(page.locator('#cache-ttl')).toBeVisible();
    await page.selectOption('#cache-ttl', '1h');
    await page.click('button:has-text("Save capabilities")');
    await expect(page.locator('[role="status"]')).toBeVisible();

    await page.reload();
    await expect(page.locator('#prompt-caching')).toBeChecked();
    await expect(page.locator('#cache-ttl')).toHaveValue('1h');
  });

  test('the lifetime says what it costs, where somebody choosing can read it', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('cachehelp');
    await createUseCase(page, slug, 'Caching help probe');

    await page.goto(`/use-cases/${slug}`);
    await page.click('label:has(#prompt-caching)');

    // In the options themselves, so the price is legible without opening anything.
    await expect(page.locator('#cache-ttl')).toContainText('costs about double');

    // And the hint actually appears — hover, which is how an "i" is used.
    await page.hover('[data-testid="info-cache-ttl"]');
    const help = page.locator('[data-testid="help-cache-ttl"]');
    await expect(help).toBeVisible();
    await expect(help).toContainText('only your own traffic settles it');
  });

  test('the caching hint names the reason it is off by default', async ({ page }) => {
    /** Not a saving nobody claimed: on Google Cloud the cache is scoped to the organisation, so
     *  an administrator opting in is making a decision about their own system prompt. */
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('cachewhy');
    await createUseCase(page, slug, 'Caching reason probe');

    await page.goto(`/use-cases/${slug}`);
    await page.hover('[data-testid="info-prompt-caching"]');
    const help = page.locator('[data-testid="help-prompt-caching"]');
    await expect(help).toBeVisible();
    await expect(help).toContainText('whole organisation');
    await expect(help).toContainText('never the answer');
  });

  test('the overview reports the cache share beside the spend', async ({ page }) => {
    /** `FRD-133` FR-10: tuning is empirical only if the effect is visible where the setting's
     *  consequences are. A share with no traffic behind it is an em dash, never a confident 0 %. */
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('cacheshare');
    await createUseCase(page, slug, 'Caching share probe');

    await page.goto(`/use-cases/${slug}`);
    const share = page.locator('[data-testid="consumption-month-cached"]');
    await expect(share).toBeVisible();
    await page.hover('[data-testid="info-consumption-cached"]');
    await expect(page.locator('[data-testid="help-consumption-cached"]')).toContainText(
      'does not cache at all',
    );
  });
});
