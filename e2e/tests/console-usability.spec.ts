import { Page, expect, test } from '@playwright/test';
import { USERS, expectNoHorizontalOverflow, login, uniqueSlug, createUseCase } from './support';

/**
 * The console reads, explains and does not move (`FRD-207`).
 *
 * This is the layer these properties live in and no other can see them: a layout shift is a
 * browser measurement, an alignment is a geometry, and a hover is an interaction jsdom has no
 * concept of. The unit suite asserts that the markup and the state are right; only here can it be
 * shown that a reader is not being moved about, misled, or asked to guess what a control does.
 */

/**
 * Create a global anomaly rule through the API, as whoever is signed in.
 *
 * Deterministic on purpose. The first version of these tests skipped when the installation
 * happened to have no rules, which meant the rule editor — the part of this pass with the most
 * behaviour in it — was exercised in the browser exactly never. A test that skips itself when the
 * data is inconvenient is a test that reports green about nothing.
 */
async function createRule(page: Page, name: string): Promise<void> {
  const status = await page.evaluate(async (ruleName) => {
    const token = sessionStorage.getItem('access_token') ?? localStorage.getItem('access_token');
    const response = await fetch('/api/v1/anomaly-rules/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({
        name: ruleName,
        kind: 'refusal_rate',
        window_minutes: 15,
        threshold: 50,
        min_sample: 20,
        action: 'alert',
        target: 'subject',
      }),
    });
    return response.status;
  }, name);
  expect(status, 'the rule could not be created').toBeLessThan(300);
}

test.describe('The page holds still', () => {
  test('a live view refreshes without moving anything', async ({ page }) => {
    // Measured, not eyeballed. The previous version shifted the Refresh button on every tick,
    // because the stamp beside it changed width — "updating…" against "updated 12s ago", and "9s"
    // against "10s". Five shifts in forty seconds is the jiggle somebody notices without being
    // able to name it, and the smallest ones are the most unsettling: nothing appears to happen.
    await login(page, USERS.security);
    await page.goto('/security');
    await expect(page.locator('[data-testid="live-stamp"]')).toBeVisible();

    // **Measured from after the page has settled**, which is what this test has always claimed to
    // be about. It used `buffered: true` and therefore also counted the *initial* render — and on
    // 2026-08-08, with 1919 findings in the database, that render reflowed once (0.0207 at t=101ms,
    // a card arriving after the shell) and the test failed for a reason it was not written to
    // catch. A first paint that settles once as its data lands is not the defect here; the defect
    // was a control that moved under the reader's cursor on **every tick**, and a test that cannot
    // tell the two apart reports the wrong thing.
    await page.waitForTimeout(1_000);

    const shifts = await page.evaluate(async () => {
      const seen: number[] = [];
      const observer = new PerformanceObserver((list) => {
        for (const entry of list.getEntries() as unknown as { value: number }[]) {
          seen.push(entry.value);
        }
      });
      observer.observe({ type: 'layout-shift' });
      // Two full ticks of the 15-second poll, plus room for a slow one.
      await new Promise((resolve) => setTimeout(resolve, 35_000));
      observer.disconnect();
      return seen;
    });

    expect(shifts, `the page shifted ${shifts.length} time(s) while refreshing`).toEqual([]);
  });

  test('the trace filters sit on one line', async ({ page }) => {
    // A bare checkbox beside a field with a label above its control ends up on a different line
    // from the control it is read as a pair with — jsdom has no layout at all, so this is the
    // only place the two can be compared.
    await login(page, USERS.governance);
    await page.goto('/use-cases/demo-uc?tab=traces');
    await expect(page.locator('[data-testid="refusals-only"]')).toBeVisible();

    const geometry = await page.evaluate(() => {
      const box = (selector: string) => document.querySelector(selector)!.getBoundingClientRect();
      const check = box('[data-testid="refusals-only"]');
      const select = box('#trace-outcome');
      const label = document.querySelector('[data-testid="refusals-only"]')!.parentElement!;
      return {
        checkCentre: check.top + check.height / 2,
        selectCentre: select.top + select.height / 2,
        gap: select.left - label.getBoundingClientRect().right,
      };
    });

    expect(Math.abs(geometry.checkCentre - geometry.selectCentre)).toBeLessThan(4);
    // Far enough apart to read as two controls rather than one crowded one.
    expect(geometry.gap).toBeGreaterThan(12);
  });
});

test.describe('The console explains itself', () => {
  test('the kill switch says how far it reaches, on hover', async ({ page }) => {
    // "Stop traffic" is a verb with no object until it is pressed, and the object is the thing a
    // reader needs before deciding. Hover, because an "i" is a thing you point at.
    await login(page, USERS.security);
    await page.goto('/security');
    await page.click('[role="tab"]:has-text("Suspensions")');

    const hint = page.locator('[data-testid="info-stop-scope"]');
    await expect(page.locator('[data-testid="help-stop-scope"]')).toHaveCount(0);

    await hint.hover();
    const help = page.locator('[data-testid="help-stop-scope"]');
    await expect(help).toBeVisible();
    await expect(help).toContainText('one caller');
    await expect(help).toContainText('one whole use case');
    await expect(help).toContainText('no switch for the installation');
  });

  test('a finding opens to show what it was drawn from', async ({ page }) => {
    await login(page, USERS.security);
    await page.goto('/security');

    // Waited for, not counted the instant the tab is clicked: the findings arrive from the
    // gateway, and a count taken before the response reads as "there are none".
    const toggles = page.locator('[data-testid^="event-toggle-"]');
    await expect(toggles.first().or(page.locator('.empty')).first()).toBeVisible({
      timeout: 20_000,
    });
    test.skip((await toggles.count()) === 0, 'nothing has crossed a threshold here');

    await toggles.first().click();
    await expect(page.locator('[data-testid^="event-detail-"]').first()).toBeVisible();
  });

  test('a rule says what it does, in a sentence', async ({ page }) => {
    // The console used to print `new_source_ip` and two bare numbers, which is enough for whoever
    // wrote the rule and nothing for whoever has to act on the alert.
    await login(page, USERS.security);
    const name = `e2e readable ${Date.now()}`;
    await page.goto('/security');
    await createRule(page, name);
    await page.reload();
    await page.click('[role="tab"]:has-text("Rules")');

    // Searched for, not scrolled to. The list is paged now, and an installation with a few
    // hundred rules puts a freshly created one well off the first page — which is exactly what a
    // person would hit, and what this test hit the moment paging landed.
    await page.getByTestId('rule-search').fill(name);

    const row = page.locator(`tr:has-text("${name}")`);
    await expect(row).toBeVisible({ timeout: 20_000 });
    await row.locator('[data-testid^="rule-toggle-"]').click();
    const detail = page.locator('[data-testid^="rule-detail-"]').first();
    await expect(detail).toBeVisible();
    await expect(detail).toContainText('Watches');
    await expect(detail).toContainText('minutes');
  });

  test('a global rule can be edited by IT Security and not by oversight', async ({ page }) => {
    await login(page, USERS.security);
    const name = `e2e editable ${Date.now()}`;
    await page.goto('/security');
    await createRule(page, name);
    await page.reload();
    await page.click('[role="tab"]:has-text("Rules")');
    await page.getByTestId('rule-search').fill(name);

    const row = page.locator(`tr:has-text("${name}")`);
    await expect(row).toBeVisible({ timeout: 20_000 });
    await row.locator('[data-testid^="rule-toggle-"]').click();
    await page.locator('[data-testid^="rule-edit-"]').first().click();

    // The form opens with the rule as it is, not empty. Its fields are the shared `RuleForm`'s
    // now, so they are named after the form rather than after the screen.
    const threshold = page.locator('[data-testid^="rule-"][data-testid$="-threshold"]').first();
    await expect(threshold).toBeVisible();
    expect(await threshold.inputValue()).toBe('50');

    // And a change actually reaches the server: the reason this screen exists is that rules were
    // authorable only over the API.
    await threshold.fill('65');
    await page.locator('[data-testid^="rule-"][data-testid$="-save"]').first().click();
    await expect(page.locator('[role="status"]')).toContainText('saved', { timeout: 15_000 });

    await page.reload();
    await page.click('[role="tab"]:has-text("Rules")');
    await page.getByTestId('rule-search').fill(name);
    await expect(page.locator(`tr:has-text("${name}")`)).toContainText('65');
  });

  test('the export column explanations open on hover', async ({ page }) => {
    await login(page, USERS.governance);
    await page.goto('/reporting');
    await expect(page.locator('[data-testid="export-breakdown"]')).toBeVisible();

    const hint = page.locator('[data-testid="info-table-tokens"]');
    await expect(hint.or(page.locator('.table__empty')).first()).toBeVisible({ timeout: 20_000 });
    test.skip((await hint.count()) === 0, 'no traffic in this period to break down');

    await hint.hover();
    const help = page.locator('[data-testid="help-table-tokens"]');
    await expect(help).toBeVisible();
    await expect(help).toContainText('what went');
    await expect(help).toContainText('priced apart');
  });
});

test.describe('Lists stay usable as they grow', () => {
  test('the reporting page shows one breakdown, and downloads that one', async ({ page }) => {
    // Four stacked tables made the page long enough that its own export control scrolled out of
    // sight — and left two ideas of "which table", one for the screen and one for the file.
    await login(page, USERS.governance);
    await page.goto('/reporting');

    await expect(page.locator('h3:has-text("By use case")')).toBeVisible();
    await expect(page.locator('h3:has-text("By model")')).toHaveCount(0);

    await page.selectOption('[data-testid="export-breakdown"]', 'model');
    await expect(page.locator('h3:has-text("By model")')).toBeVisible();
    await expect(page.locator('h3:has-text("By use case")')).toHaveCount(0);

    // The outcome breakdown is shown and not exported, and says so rather than answering 400.
    await page.selectOption('[data-testid="export-breakdown"]', 'outcome');
    await expect(page.locator('[data-testid="export-download"]')).toHaveCount(0);
    await expect(page.locator('[data-testid="export-unavailable"]')).toContainText('not exported');
  });

  test('the use-case overview can be searched', async ({ page }) => {
    // A live round found 801 use cases in one installation. Nothing about the screen was wrong,
    // and it was unusable.
    await login(page, USERS.useCaseAdmin);
    const slug = uniqueSlug('findme');
    await createUseCase(page, slug, 'Findable probe');

    await page.goto('/use-cases');
    // Generous, and for a reason worth writing down: `/api/v1/use-cases/` computes object-level
    // permissions per row, so a database that has accumulated hundreds of use cases from earlier
    // suites takes many seconds to answer. That is the *list* being slow, not this screen — and it
    // is precisely what the search box and the pager exist to make survivable.
    await expect(page.locator('[data-testid="use-case-pager"]')).toBeVisible({ timeout: 30_000 });

    await page.fill('[data-testid="use-case-search"]', slug);
    await expect(page.locator(`code:text-is("${slug}")`)).toBeVisible();
    await expect(page.locator('[data-testid="use-case-pager"]')).toContainText('filtered');

    await page.fill('[data-testid="use-case-search"]', 'zzz-nothing-matches-this');
    await expect(page.locator('[data-testid="use-case-no-match"]')).toBeVisible();
  });

  test('the model catalog can be searched, and its buttons stay in their row', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    await page.goto('/models');
    await expect(page.locator('[data-testid="model-search"]')).toBeVisible();

    // `display: flex` on a `<td>` stops it being a table cell: it leaves the row's height and
    // baseline, which is the visible break between a model and its own Edit/Delete buttons.
    const aligned = await page.evaluate(() => {
      const cell = document.querySelector('.table__actions');
      if (!cell) return null;
      const row = cell.closest('tr')!.getBoundingClientRect();
      const box = cell.getBoundingClientRect();
      return { rowTop: row.top, cellTop: box.top, rowHeight: row.height, cellHeight: box.height };
    });
    if (aligned) {
      expect(Math.abs(aligned.rowTop - aligned.cellTop)).toBeLessThan(2);
      expect(Math.abs(aligned.rowHeight - aligned.cellHeight)).toBeLessThan(2);
    }

    await page.fill('[data-testid="model-search"]', 'zzz-no-such-model');
    await expect(page.locator('[data-testid="model-no-match"]')).toBeVisible();
    await expectNoHorizontalOverflow(page, 'the model catalog');
  });
});

test.describe('Navigation says where you are', () => {
  test('the selected area is unmistakable, not a hairline', async ({ page }) => {
    await login(page, USERS.security);
    await page.goto('/security');
    // Waited for, not assumed: `routerLinkActive` applies its class once the navigation settles,
    // and reading the DOM the instant `goto` returns finds the bar as it was on arrival.
    await expect(page.locator('.aira-nav__item.is-active')).toBeVisible();

    const marker = await page.evaluate(() => {
      const active = document.querySelector('.aira-nav__item.is-active') as HTMLElement | null;
      const other = document.querySelector('.aira-nav__item:not(.is-active)') as HTMLElement | null;
      if (!active || !other) return null;
      const style = getComputedStyle(active);
      return {
        href: active.getAttribute('href'),
        border: parseFloat(style.borderBottomWidth),
        weight: style.fontWeight,
        background: style.backgroundColor,
        otherBackground: getComputedStyle(other).backgroundColor,
      };
    });

    expect(marker).not.toBeNull();
    expect(marker!.href).toContain('/security');
    expect(marker!.border).toBeGreaterThanOrEqual(3);
    expect(Number(marker!.weight)).toBeGreaterThanOrEqual(700);
    // A tint the unselected items do not have — the underline alone read as decoration.
    expect(marker!.background).not.toBe(marker!.otherBackground);
  });
});

test.describe("Paging is the server's, where the list is unbounded", () => {
  test('the use-case list arrives one page at a time, and says how many there are', async ({
    page,
  }) => {
    // Client-side paging fixed the half that was never the expensive one: this endpoint computes
    // object-level permissions per row, so fetching everything and slicing it in the browser left
    // every one of those computations happening. The reader waited exactly as long for 25 rows.
    await login(page, USERS.useCaseAdmin);
    const started = Date.now();
    await page.goto('/use-cases');
    await expect(page.locator('[data-testid="use-case-pager"]')).toBeVisible({ timeout: 30_000 });

    // Not a benchmark — an upper bound that the old shape could not have met on this database.
    expect(Date.now() - started).toBeLessThan(15_000);
    expect(await page.locator('tbody tr').count()).toBeLessThanOrEqual(25);

    const pager = page.locator('[data-testid="use-case-pager"]');
    // The total is the server's count, not the length of what arrived.
    await expect(pager).toContainText(/of \d+ use cases/);
  });

  test('the search is answered by the database, not by the page', async ({ page }) => {
    await login(page, USERS.useCaseAdmin);
    const slug = uniqueSlug('needle');
    await createUseCase(page, slug, 'Needle probe');

    await page.goto('/use-cases');
    await expect(page.locator('[data-testid="use-case-search"]')).toBeVisible({ timeout: 30_000 });

    const request = page.waitForRequest(
      (r) => r.url().includes('/api/v1/use-cases/') && r.url().includes(`q=${slug}`),
    );
    await page.fill('[data-testid="use-case-search"]', slug);
    await request;

    await expect(page.locator(`code:text-is("${slug}")`)).toBeVisible();
    await expect(page.locator('[data-testid="use-case-pager"]')).toContainText('filtered');
  });
});

test.describe("A use case's own anomaly rules", () => {
  test('can be created and changed by whoever administers the use case', async ({ page }) => {
    // The security console said a use-case rule "is changed on that use case" and there was no
    // such screen — an instruction with no destination. This is the destination.
    await login(page, USERS.useCaseAdmin);
    const slug = uniqueSlug('rules');
    await createUseCase(page, slug, 'Rules probe');

    await page.goto(`/use-cases/${slug}?tab=rules`);
    await expect(page.locator('[data-testid="no-rules"]')).toBeVisible({ timeout: 20_000 });
    // An empty list here does not mean nothing is watching — global rules may still apply, and
    // the place that knows the difference says so.
    await expect(page.locator('[data-testid="no-rules"]')).toContainText('global rules');

    await page.click('[data-testid="rule-add"]');
    await page.fill('[data-testid="new-rule-name"]', 'refusals are climbing');
    await page.selectOption('[data-testid="new-rule-kind"]', 'refusal_rate');
    await page.fill('[data-testid="new-rule-threshold"]', '40');
    await page.click('[data-testid="new-rule-save"]');

    await expect(page.locator('[role="status"]')).toContainText('created', { timeout: 20_000 });
    const row = page.locator('tr:has-text("refusals are climbing")').first();
    await expect(row).toBeVisible();
    // Said in words, not as a kind and two numbers.
    await expect(page.locator('.detail-sentence').first()).toContainText('Watches');

    // And changed.
    await page.locator('[data-testid^="uc-rule-edit-"]').first().click();
    const threshold = page.locator('[data-testid^="rule-"][data-testid$="-threshold"]').first();
    await threshold.fill('55');
    await page.locator('[data-testid^="rule-"][data-testid$="-save"]').first().click();
    await expect(page.locator('[role="status"]')).toContainText('saved', { timeout: 20_000 });
  });

  test('is read-only for somebody who does not administer it', async ({ page }) => {
    // `ucuser` is a member of `demo-uc` and administers nothing.
    await login(page, USERS.useCaseUser);
    await page.goto('/use-cases/demo-uc?tab=rules');

    await expect(page.locator('[data-testid="rules-readonly"]')).toBeVisible({ timeout: 20_000 });
    await expect(page.locator('[data-testid="rule-add"]')).toHaveCount(0);
  });
});
