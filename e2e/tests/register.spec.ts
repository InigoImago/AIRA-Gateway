import { expect, test } from '@playwright/test';
import {
  USERS,
  createUseCase,
  expectNoHorizontalOverflow,
  login,
  logout,
  uniqueSlug,
} from './support';

/**
 * The register of processing activities (`FRD-608`), through a real browser and a real token.
 *
 * What this layer is for here, and the unit tests structurally cannot see: that the screen is
 * reachable **by the role it was built for**, that the gateway scopes it by the same token the
 * console holds, and that the CSV a compliance function will actually print comes back as a file.
 *
 * `it-steuerung` is the subject of most of it deliberately. That role is the one the owner asked
 * about — *"there is still no overview for IT Steuerung"* — and it is read-only everywhere by
 * `ADR-0007`, so it is also the role that would notice first if this screen ever grew a control.
 */
test.describe('Register of processing activities', () => {
  test('is offered to an oversight role and lists every use case', async ({ page }) => {
    await login(page, USERS.governance);

    await expect(page.getByTestId('nav-register')).toBeVisible();
    await page.getByTestId('nav-register').click();

    await expect(page.getByTestId('register-table')).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId('register-scope')).toContainText('Every use case');
    await expectNoHorizontalOverflow(page, 'register @ default width');
  });

  test('is not offered to somebody who oversees nothing, and answers them their own', async ({
    page,
  }) => {
    // **Not a refusal.** The gateway scopes it by `visible_scope` like every other read here, so a
    // member who types the URL gets a register of their own use cases. It is out of their
    // navigation because the value of the screen is comparison across use cases, which a member of
    // one cannot do — offering it would be a tab that looks broken (`FRD-206`).
    await login(page, USERS.useCaseUser);

    await expect(page.getByTestId('nav-register')).toHaveCount(0);

    await page.goto('/register');
    await expect(page.getByTestId('register-scope')).toContainText('member of', {
      timeout: 30_000,
    });
  });

  test('carries what a reader scans by on the row, and the rest one click in', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('register');
    await createUseCase(page, slug, 'Register probe');

    // **The register reads the gateway, and the gateway learns over Kafka.** A use case authored
    // a second ago is in Management and not yet in the read-model, so this reloads until it
    // arrives rather than asserting once and calling the lag a defect.
    //
    // That lag is a property of the document worth knowing: the register describes what is *in
    // force* rather than what was last typed, which for a register is the more honest of the two —
    // and it is why the screen is served by the gateway at all.
    await expect(async () => {
      await page.goto('/register');
      await expect(page.getByTestId('register-table')).toBeVisible({ timeout: 30_000 });
      await page.getByTestId('register-search').fill(slug);
      await expect(page.getByTestId(`register-row-${slug}`)).toBeVisible({ timeout: 2_000 });
    }).toPass({ timeout: 60_000 });

    const row = page.getByTestId(`register-row-${slug}`);
    // A fresh use case stores prompts on the default clock. That is a *scan* column — "does this
    // use case keep prompts, and for how long" is the question forty rows are read for.
    await expect(row).toContainText('day(s)');
    // What it may call is a detail *about* one use case, so it is not on the row.
    await expect(row).not.toContainText('none released');

    // The second cause of the reported jiggling — a page that gains a scrollbar loses ~15px and
    // re-lays out every percentage width on it — is **not** provable here. Headless Chromium draws
    // overlay scrollbars, so `clientWidth` measured 1280 on this page at viewport heights of 400,
    // 3000, 6000 and 9000 alike: there is no width to lose. It is guarded one layer out, in
    // `tools/tests/test_a_growing_page_does_not_reflow.py`, which says why it has to be.
    await page.getByTestId(`register-open-${slug}`).click();
    await expect(page.locator('tr.row-detail')).toContainText('none released');
  });

  test('opening every row on the page moves no column', async ({ page }) => {
    /**
     * That an opened detail does not resize the table.
     *
     * **The weaker of the two stability properties, and it is recorded as weak on purpose.** It
     * was written expecting to prove the `<colgroup>`, and when the fix was removed and the
     * frontend rebuilt it **still passed** — with one row open and again with all twenty-five. A
     * cell spanning every column only widens a table when it is wider than their sum, and a detail
     * of wrapped prose never is. So this guards the property a reader cares about rather than the
     * mechanism that delivers it; the mechanism is guarded by the test below, where removing it
     * moved every column by up to 26px.
     *
     * Kept rather than deleted, because the property is the thing somebody would break next: a
     * `white-space: nowrap` or an unbreakable id in the detail is all it would take.
     */
    await login(page, USERS.governance);
    await page.goto('/register');
    await expect(page.getByTestId('register-table')).toBeVisible({ timeout: 30_000 });

    const widths = () =>
      page
        .locator('[data-testid="register-table"] thead th')
        .evaluateAll((cells) =>
          cells.map((cell) => Math.round(cell.getBoundingClientRect().width)),
        );

    const closed = await widths();
    expect(closed.length, 'the register should have a caret and four columns').toBe(5);

    // **Every row on the page, not one.** Opening one row was measured against an automatic table
    // and moved nothing — the detail of a single use case is narrower than the columns above it,
    // so the guard passed with the fix removed and proved nothing. Twenty-five details at once is
    // the case an automatic layout cannot absorb, and it is also the honest property: a register
    // is read by opening several.
    const rows = page.locator('[data-testid="register-table"] tbody tr.row-openable');
    const count = await rows.count();
    expect(count, 'a page of rows is what makes this measurement mean anything').toBeGreaterThan(5);
    for (let index = 0; index < count; index++) {
      await rows.nth(index).locator('button').click();
    }
    await expect(page.locator('tr.row-detail')).toHaveCount(count);

    const open = await widths();
    expect(open, `columns moved when the rows were opened: ${closed} → ${open}`).toEqual(closed);

    // And the page still does not scroll sideways with every detail on it.
    await expectNoHorizontalOverflow(page, 'register @ every row open');

    for (let index = 0; index < count; index++) {
      await rows.nth(index).locator('button').click();
    }
    await expect(page.locator('tr.row-detail')).toHaveCount(0);
    expect(await widths(), 'columns moved when the rows were closed again').toEqual(closed);
  });

  test('a row of its own making moves no column', async ({ page }) => {
    /**
     * **The jiggle that is actually there**, and it is not the one the detail row causes.
     *
     * Measured before the fix, on a demo installation that then held hundreds of use cases: the
     * header columns read `[52, 266, 152, 199, 185]` on the first page of the register and
     * `[52, 240, 159, 209, 194]` on the third. An automatic table sizes its columns from the rows
     * it is *currently holding*, so a different set of rows is a different set of column widths.
     * On a screen whose whole purpose is reading down a column, that is the defect the
     * `<colgroup>` and `table-layout: fixed` fix.
     *
     * ## Two versions of this went wrong before this one, in opposite directions
     *
     * It paged five deep first, which needed 125 use cases — and the demo had them only because
     * nothing cleaned up after a browser run. When the suite learned to tidy, the demo went back
     * to thirteen and the guard went **red** for want of a pager. That is this file's own recorded
     * lesson repeated by its author: *"the pager browser guard passed against the unfixed console
     * — it depended on 917 accumulated demo use cases."*
     *
     * Rewritten to filter instead of page, it went **green with the fix removed**: thirteen short
     * rows are all about as wide as each other, so an automatic table sized them the same and
     * there was nothing to see. A guard that cannot fail is the thing it guards against.
     *
     * So it brings its own row. One use case with a name and a purpose far longer than any other
     * is exactly what an automatic table cannot absorb — and because the row is created here, the
     * measurement means the same thing on an empty installation and on a busy one.
     */
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('widths');
    // Far wider than any name this table would otherwise hold, which is exactly what an automatic
    // layout cannot absorb quietly. The name is enough — it is the first column's content — and
    // using it keeps the test to one creation and no editing.
    await createUseCase(page, slug, `Column width probe ${'wide '.repeat(24)}`);

    const widths = () =>
      page
        .locator('[data-testid="register-table"] thead th')
        .evaluateAll((cells) =>
          cells.map((cell) => Math.round(cell.getBoundingClientRect().width)),
        );
    const show = async (term: string, expected: string) => {
      await page.getByTestId('register-search').fill(term);
      await expect(page.getByTestId(`register-row-${expected}`)).toBeVisible({ timeout: 10_000 });
      return widths();
    };

    // The register reads the gateway, and the gateway learns over Kafka — the same lag the row
    // test above polls for, and for the same reason.
    await expect(async () => {
      await page.goto('/register');
      await expect(page.getByTestId('register-table')).toBeVisible({ timeout: 30_000 });
      await page.getByTestId('register-search').fill(slug);
      await expect(page.getByTestId(`register-row-${slug}`)).toBeVisible({ timeout: 2_000 });
    }).toPass({ timeout: 60_000 });

    const wide = await show(slug, slug);
    const ordinary = await show('coding-assistant', 'coding-assistant');

    expect(
      ordinary,
      `columns moved with the rows on screen:\n  the wide row: ${wide}` +
        `\n  an ordinary one: ${ordinary}`,
    ).toEqual(wide);
  });

  test('several rows stay open at once — a register is read by comparing', async ({ page }) => {
    // The deliberate difference from the request list, which keeps one open because opening one
    // fetches its payload. Here everything is loaded already, and *these two side by side* is the
    // question the screen exists for.
    await login(page, USERS.governance);
    await page.goto('/register');
    await expect(page.getByTestId('register-table')).toBeVisible({ timeout: 30_000 });

    const rows = page.locator('[data-testid="register-table"] tbody tr.row-openable');
    await rows.nth(0).locator('button').click();
    await rows.nth(1).locator('button').click();

    await expect(page.locator('tr.row-detail')).toHaveCount(2);
  });

  test('says whether the erasure it promises has actually been happening', async ({ page }) => {
    // `FRD-608` §2.4. Either a pass with its figures, or the sentence that says there is no record
    // — never a silent absence, which reads as "not applicable".
    await login(page, USERS.governance);
    await page.goto('/register');

    await expect(page.getByTestId('register-erasure')).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId('register-erasure')).toContainText(
      /Retention last ran|no recorded pass/,
    );
  });

  test('downloads as a spreadsheet with the columns a register needs', async ({ page }) => {
    await login(page, USERS.governance);
    await page.goto('/register');
    await expect(page.getByTestId('register-table')).toBeVisible({ timeout: 30_000 });

    const download = page.waitForEvent('download');
    await page.getByTestId('register-export').click();
    const file = await download;

    expect(file.suggestedFilename()).toMatch(/^aira-register_/);
    const stream = await file.createReadStream();
    const chunks: Buffer[] = [];
    for await (const chunk of stream) {
      chunks.push(chunk as Buffer);
    }
    const body = Buffer.concat(chunks).toString('utf-8');

    expect(body.startsWith('﻿'), 'Excel needs the BOM to read this as UTF-8').toBe(true);
    expect(body).toContain('# AIRA register of processing activities');
    expect(body).toContain('purpose');
    expect(body).toContain('retention_days');
    expect(body).toContain('regions_outside_the_configuration');
  });

  test('the screen is read-only — governance registers, it does not change', async ({ page }) => {
    // `ADR-0007` makes governance read-only deliberately, and a register that can change what it
    // registers is not a register. Asserted as the absence of any control that writes, because the
    // way this rule breaks is somebody adding one helpful button.
    await login(page, USERS.governance);
    await page.goto('/register');
    await expect(page.getByTestId('register-table')).toBeVisible({ timeout: 30_000 });

    const writers = await page
      .locator('main button:visible, button:visible')
      .evaluateAll((buttons) =>
        buttons
          .map((button) => (button.textContent ?? '').trim())
          .filter((label) =>
            /save|add|create|edit|delete|remove|issue|release|approve/i.test(label),
          ),
      );

    expect(writers, `the register offered a control that writes: ${writers.join(', ')}`).toEqual(
      [],
    );
    await logout(page);
  });
});
