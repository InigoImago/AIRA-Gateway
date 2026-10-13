import { Page, expect, test } from '@playwright/test';
import { USERS, login } from './support';

/**
 * **Every window in the console asks one question per row.**
 *
 * The rule is `styles.scss`'s and it is old; what was missing was anything that checks it. It has
 * now been re-broken twice by two different mechanisms, which is why the guard measures the
 * *outcome* rather than asserting the selector:
 *
 * - the rule read `form.form-inline > .field`, and every window built through `<app-modal>` — which
 *   projects a `<div modal-body>` wrapper — was outside it by construction;
 * - and once that was widened, the anomaly rule editor still laid out two and three fields to a
 *   line, because `.form-stack .form-inline` is a **grid** and a grid item ignores `flex-basis`.
 *   Measured then: *Watch for* 551px beside *Raised about* 263px, and *Above* / *Over (minutes)* /
 *   *Smallest sample* all on one line.
 *
 * Two spellings of one rule, each fixed without the other being noticed. So: open every window
 * there is, and measure where its fields actually are.
 *
 * A `fieldset` is the exemption, in the stylesheet and here: the model editor groups per-media-type
 * and per-level token fields into rows on purpose, and fifteen small numbers in a column is a page
 * of scrolling.
 */

/**
 * Fields grouped by the line they sit on, ignoring any inside a `fieldset`.
 *
 * **A line is a set of boxes that overlap vertically**, the way the browser lays them out — not a
 * band of pixels. A band (`round(top / 6)`) splits two fields of different heights on one line
 * into two "lines" and calls the window stacked; `expectFormControlsAligned` groups by overlap for
 * the same reason.
 */
function fieldsSharingALine(): string[][] {
  const out: string[][] = [];
  for (const body of Array.from(document.querySelectorAll('.modal__body'))) {
    const lines: { top: number; bottom: number; labels: string[] }[] = [];
    for (const field of Array.from(body.querySelectorAll('.field'))) {
      const box = field.getBoundingClientRect();
      if (box.width === 0) continue;
      if (field.closest('fieldset')) continue;
      if (field.parentElement && field.parentElement.closest('.field')) continue;
      const label = (field.querySelector('label')?.textContent || '?')
        .trim()
        .replace(/\s+/g, ' ')
        .slice(0, 30);
      const line = lines.find((l) => box.top < l.bottom && box.bottom > l.top);
      if (line) {
        line.top = Math.min(line.top, box.top);
        line.bottom = Math.max(line.bottom, box.bottom);
        line.labels.push(label);
      } else {
        lines.push({ top: box.top, bottom: box.bottom, labels: [label] });
      }
    }
    for (const line of lines) if (line.labels.length > 1) out.push(line.labels);
  }
  return out;
}

async function expectStacked(page: Page, window_: string) {
  // **Waited for, not slept.** After a fixed sleep a window that has not rendered has no
  // `.modal__body`, the measurement finds nothing, and the test passes about an empty page.
  await page.locator('.modal__body .field').first().waitFor({ timeout: 20_000 });
  const shared = await page.evaluate(fieldsSharingALine);
  expect(
    shared,
    `${window_}: these fields share a line, and a window asks one question per row`,
  ).toEqual([]);
}

test.describe('Every window stacks its fields', () => {
  test('the windows reached from a page', async ({ page }) => {
    test.setTimeout(300_000);
    await login(page, USERS.globalAdmin);

    await page.goto('/models');
    await page.getByTestId('add-model').click();
    await expectStacked(page, 'the model editor');
    await page.keyboard.press('Escape');

    await page.goto('/reporting');
    await expect(page.getByTestId('add-installation-budget')).toBeVisible({ timeout: 30_000 });
    await page.getByTestId('add-installation-budget').click();
    await expectStacked(page, "the installation's spend limit");
    await page.keyboard.press('Escape');

    await page.goto('/security');
    await page.locator('.tabs button').filter({ hasText: /Rules/ }).first().click();
    await expect(page.getByTestId('new-global-rule')).toBeVisible({ timeout: 30_000 });
    await page.getByTestId('new-global-rule').click();
    await expectStacked(page, 'a rule for every use case');
    await page.keyboard.press('Escape');
  });

  test('the windows on a use case', async ({ page }) => {
    test.setTimeout(300_000);
    await login(page, USERS.globalAdmin);
    await page.goto('/use-cases');
    await page.locator('table tbody tr a').first().click();
    await expect(page.locator('#tab-budgets')).toBeVisible({ timeout: 30_000 });

    for (const [tab, trigger, label] of [
      ['#tab-budgets', 'add-budget', 'a budget'],
      ['#tab-rate-limits', 'add-rate-limit', 'a rate limit'],
      ['#tab-rules', 'rule-add', 'a rule for this use case'],
      ['#tab-keys', 'issue-key', 'an API key'],
    ] as Array<[string, string, string]>) {
      await page.locator(tab).click();
      await expect(page.getByTestId(trigger)).toBeVisible({ timeout: 30_000 });
      await page.getByTestId(trigger).click();
      await expectStacked(page, label);
      await page.keyboard.press('Escape');
      await page.waitForTimeout(300);
    }
  });
});
