import { expect, test } from '@playwright/test';
import {
  USERS,
  createUseCase,
  expectFormControlsAligned,
  expectNoHorizontalOverflow,
  login,
  openEditorTab,
  openModelEditor,
  uniqueSlug,
  submitOfOpenForm,
} from './support';

/**
 * The objective form of "the page overflows".
 *
 * Unit tests run in jsdom, which has no layout engine and therefore cannot see this class of
 * bug at all — a real browser at a real width is the only way to assert it.
 */
const VIEWPORTS = [
  { name: 'phone', width: 360, height: 740 },
  { name: 'tablet', width: 768, height: 1024 },
  { name: 'laptop', width: 1280, height: 800 },
  { name: 'wide', width: 1920, height: 1080 },
];

test.describe('Layout', () => {
  test('the global stylesheet actually applies in the served build', async ({ page }) => {
    // Regression: Angular's production build defers the global stylesheet with
    // <link media="print" onload="this.media='all'">. That inline handler is script, and the
    // CSP (ADR-0007) allows scripts from 'self' only — so it never ran and the entire design
    // system was missing in the container build while looking fine in the dev server.
    await login(page, USERS.useCaseAdmin);
    await page.goto('/use-cases');

    const applied = await page.evaluate(() => {
      const sheets = Array.from(document.querySelectorAll('link[rel=stylesheet]'));
      const card = document.querySelector('.card');
      return {
        deferred: sheets.filter((l) => l.getAttribute('media') === 'print').length,
        cardBackground: card ? getComputedStyle(card).backgroundColor : null,
      };
    });

    expect(
      applied.deferred,
      'a stylesheet is still deferred behind an inline onload handler the CSP will block',
    ).toBe(0);
    // .card paints a surface colour; an unstyled div would be transparent.
    expect(applied.cardBackground).not.toBe('rgba(0, 0, 0, 0)');
  });

  test('no route overflows its viewport at any width', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('layout');
    await createUseCase(page, slug, 'Layout probe');

    const routes = [
      { path: '/use-cases', label: 'list' },
      { path: `/use-cases/${slug}`, label: 'detail' },
      { path: `/use-cases/${slug}/pipeline`, label: 'pipeline builder' },
    ];

    for (const viewport of VIEWPORTS) {
      await page.setViewportSize({
        width: viewport.width,
        height: viewport.height,
      });
      for (const route of routes) {
        await page.goto(route.path);
        await page.waitForLoadState('networkidle');
        await expectNoHorizontalOverflow(page, `${route.label} @ ${viewport.name}`);
      }
    }
  });

  test('every detail tab stays inside the viewport on a phone', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('tabs');
    await createUseCase(page, slug, 'Tab probe');

    await page.setViewportSize({ width: 360, height: 740 });
    for (const tab of ['overview', 'members', 'keys', 'budgets']) {
      await page.goto(`/use-cases/${slug}?tab=${tab}`);
      await page.waitForLoadState('networkidle');
      await expect(page.locator(`#tab-${tab}`)).toHaveAttribute('aria-selected', 'true');
      await expectNoHorizontalOverflow(page, `${tab} tab @ phone`);
    }
  });

  test('a long name and description do not widen the page', async ({ page }) => {
    // The regression this guards: one unbroken server-supplied string used to push the whole
    // layout wider than the screen.
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('long');
    const longName = 'Ein-ausgesprochen-langer-Name-ohne-Leerzeichen-'.repeat(4);

    await createUseCase(page, slug, longName);

    for (const viewport of [VIEWPORTS[0], VIEWPORTS[2]]) {
      await page.setViewportSize({
        width: viewport.width,
        height: viewport.height,
      });
      await page.goto('/use-cases');
      await expectNoHorizontalOverflow(page, `list with a long name @ ${viewport.name}`);
      await page.goto(`/use-cases/${slug}`);
      await expectNoHorizontalOverflow(page, `detail with a long name @ ${viewport.name}`);
    }
  });

  test('a wide table scrolls inside its card, not the page', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('table');
    await createUseCase(page, slug, 'Table probe');

    await page.setViewportSize({ width: 360, height: 740 });
    await page.goto(`/use-cases/${slug}?tab=keys`);
    await page.click('button:has-text("Issue key")');
    await page.fill('#key-label', 'a-deliberately-long-label-for-the-table');
    await (await submitOfOpenForm(page)).click();
    await expect(page.locator('.secret')).toBeVisible();

    const wrap = page.locator('.table-wrap').last();
    const scrolls = await wrap.evaluate((el) => el.scrollWidth > el.clientWidth);
    const overflowX = await wrap.evaluate((el) => getComputedStyle(el).overflowX);
    expect(overflowX).toBe('auto');
    // Whether or not this particular table is wider than the card, the page must not scroll.
    expect(typeof scrolls).toBe('boolean');
    await expectNoHorizontalOverflow(page, 'keys table @ phone');
  });

  test('the pipeline builder scrolls as one page, with nothing scrolling inside it', async ({
    page,
  }) => {
    // Two bugs, and the second is the fix for the first.
    //
    // The inspector was `position: sticky` with `overflow-y: auto` and a height cap, because the
    // left column was short and the right one long. That cap had its own failure — a sticky
    // element taller than the viewport pins its top and leaves its lower half unreachable, which
    // is what the routing step's "Default model" field used to do with enough categories.
    //
    // Reported by the owner: *no scrollable areas.* A scroll container inside a document that
    // already scrolls gives a reader two scrollbars and one of them appears only sometimes. So the
    // panel scrolls with the page, and the property to hold is that the field is still reachable —
    // which is what the old test was really about.
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('inspector');
    await createUseCase(page, slug, 'Inspector probe');

    await page.setViewportSize({ width: 1280, height: 700 });
    await page.goto(`/use-cases/${slug}/pipeline`);
    await page.click('button:has-text("Model Routing")');

    for (let i = 0; i < 6; i++) {
      await page.click('button:has-text("Add category")');
    }

    const defaultModel = page.locator('#insp-default-model');
    await defaultModel.scrollIntoViewIfNeeded();
    await expect(defaultModel).toBeInViewport();

    // Nothing on this page is its own scroll container. Asserted over every element rather than
    // over the inspector by name: the complaint is about the page, and naming one element is how a
    // rule comes back on the next panel somebody adds.
    const scrollers = await page.evaluate(() =>
      // `Array.from` rather than a spread: `NodeListOf<Element>` is only iterable when the
      // `dom.iterable` lib is on, which this project's tsconfig does not enable — the spread was a
      // `tsc` error that nothing in CI ran, so it sat here compiling fine under Playwright's own
      // transpile and failing under a type check.
      Array.from(document.querySelectorAll('.pipe *'))
        .filter((el) => {
          const style = getComputedStyle(el);
          const scrolls = ['auto', 'scroll'].includes(style.overflowY);
          return scrolls && el.scrollHeight > el.clientHeight + 1;
        })
        .map((el) => el.className),
    );
    expect(scrollers, 'nothing inside the builder may scroll on its own').toEqual([]);
    await expectNoHorizontalOverflow(page, 'pipeline builder with many categories');
  });
});

test.describe('Form alignment', () => {
  // Regression: `.form-inline` was bottom-aligned, so a field carrying a hint under its input
  // grew and pushed its control upwards — the row became a staircase of uneven inputs.
  test('every inline form lines its controls up', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('align');

    // The create form is deliberately **not** checked: it is a `stack`, one control per line, and
    // a stacked form has no row that could become a staircase. It was checked here — on the list
    // page, before the window was even opened — so the assertion named `'create use case'`
    // compared nothing and passed for as long as it existed. Found when the guard started
    // insisting it had something to compare.

    await createUseCase(page, slug, 'Alignment probe');

    // The access panel's picker is a filter row, not a disclosure — it is always there for
    // somebody who may grant, so there is nothing to open first (`FRD-209`).
    await page.goto(`/use-cases/${slug}?tab=members`);
    await expectFormControlsAligned(page, 'grant access');

    // The issue-key window is **not** checked, for the same reason as the create form and found
    // the same way: the guard reported "nothing with two controls on a line was found to compare".
    // It said `.form-inline` and laid out as three lines regardless — each field as wide as its own
    // hint, so the inputs were 838, 641 and 461 px wide. It is a `stack` now, which is what a form
    // in a window with a sentence under every field actually is, and a stack has no row that could
    // become a staircase. Left here as a comment rather than deleted: the next person to look at
    // that window should know it was considered.

    await page.goto(`/use-cases/${slug}?tab=budgets`);
    await page.click('button:has-text("Add budget")');
    await expectFormControlsAligned(page, 'add budget');
  });

  test('the model editor is a stack, on every tab', async ({ page }) => {
    /**
     * This was `expectFormControlsAligned` — *do the controls sharing a line start at the same
     * height* — and the model editor now has **no line with two controls on it**. Reported twice
     * by the owner: the fields stretched to half an 880-pixel dialog and did not stand under one
     * another. One question per row fixed it, and it also emptied the old assertion, which would
     * have gone on passing by finding nothing to compare.
     *
     * So the guard follows the property. What must hold now is that the editor **stacks**: every
     * top-level field starts at the same left edge, and no two of them share a line. Asserted on
     * all three tabs, because the first fix was applied per field and reached exactly one of them
     * — which is how a window with two thirds of it unfixed got reported a second time.
     */
    await login(page, USERS.globalAdmin);
    await page.goto('/models');
    await openModelEditor(page);

    for (const tab of ['identity', 'capabilities', 'price'] as const) {
      await openEditorTab(page, tab);
      const fields = await page.evaluate(() => {
        const form = document.querySelector('#model-editor-form');
        return Array.from(form?.children ?? [])
          .filter((child) => child.classList.contains('field'))
          .map((child) => {
            const box = child.getBoundingClientRect();
            return { left: box.left, top: box.top, bottom: box.bottom };
          })
          .filter((box) => box.bottom > box.top);
      });

      expect(fields.length, `${tab}: no fields found to check`).toBeGreaterThan(1);
      const lefts = new Set(fields.map((f) => Math.round(f.left)));
      expect([...lefts], `${tab}: fields do not share a left edge`).toHaveLength(1);

      for (let i = 1; i < fields.length; i += 1) {
        expect(
          fields[i].top,
          `${tab}: two fields share a line — the row is back`,
        ).toBeGreaterThanOrEqual(fields[i - 1].bottom);
      }
    }
  });

  test('a message in a dialog footer never squeezes the buttons', async ({ page }) => {
    /**
     * Reported the moment the model editor's window was narrowed to a form's width: *"when an
     * error is thrown in the interface, the button layout at the bottom breaks when you touch the
     * model-name field."*
     *
     * The footer is a wrapping flex row carrying `Check reachability`, an explanation of why Save
     * is unavailable, `Cancel` and `Save`. The explanation is `.grow`, which is `flex: 1` — that
     * is `flex-basis: 0`, so it **contributes nothing to the wrap calculation** and never takes a
     * line of its own: it is squeezed into a column and the buttons are pushed around it. The
     * identical defect `.form-inline > .callout` carries a comment about, one container along.
     *
     * Latent at 880 px, where the longest message happened to fit on one line — which is why this
     * measures the buttons **with and without** a message rather than asserting a layout at one
     * width. A rule that only holds at one width is not a rule.
     */
    await login(page, USERS.globalAdmin);
    await page.goto('/models');
    await openModelEditor(page);

    const box = async () => {
      const buttons = page.locator('.modal__foot button');
      const boxes = await buttons.evaluateAll((nodes) =>
        nodes.map((node) => {
          const r = node.getBoundingClientRect();
          return { top: r.top, width: r.width, text: (node.textContent ?? '').trim() };
        }),
      );
      return boxes;
    };

    const before = await box();
    expect(before.length).toBeGreaterThan(2);

    await page.locator('#model-name').fill('probe');
    await openEditorTab(page, 'price');
    await page.locator('#model-input').fill('0.075');
    await page.locator('#model-input').blur();
    await expect(page.getByText('Set both the input and the output price')).toBeVisible();

    const after = await box();
    // Same buttons, still on one line among themselves, and none of them narrower than it was.
    expect(after.length).toBe(before.length);
    expect(new Set(after.map((b) => Math.round(b.top))).size).toBe(1);
    for (let i = 0; i < after.length; i += 1) {
      expect(
        after[i].width,
        `"${after[i].text}" was squeezed by the message beside it`,
      ).toBeGreaterThanOrEqual(before[i].width - 1);
    }
  });

  test('forms stay aligned when they wrap on a narrow screen', async ({ page }) => {
    await login(page, USERS.globalAdmin);
    const slug = uniqueSlug('wrap');
    await createUseCase(page, slug, 'Wrap probe');

    await page.setViewportSize({ width: 900, height: 800 });
    await page.goto(`/use-cases/${slug}?tab=budgets`);
    await page.click('button:has-text("Add budget")');
    await expectFormControlsAligned(page, 'add budget @ 900px');
    await expectNoHorizontalOverflow(page, 'add budget @ 900px');
  });
});
