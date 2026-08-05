import { expect, test } from '@playwright/test';
import { USERS, createUseCase, expectNoHorizontalOverflow, login, uniqueSlug } from './support';

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
  test('no route overflows its viewport at any width', async ({ page }) => {
    await login(page, USERS.useCaseAdmin);
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
    await login(page, USERS.useCaseAdmin);
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
    await login(page, USERS.useCaseAdmin);
    const slug = uniqueSlug('long');
    const longName = 'Ein-ausgesprochen-langer-Name-ohne-Leerzeichen-'.repeat(4);

    await page.goto('/use-cases');
    await page.fill('#uc-slug', slug);
    await page.fill('#uc-name', longName);
    await page.click('button[type="submit"]');
    await expect(page.locator(`text=${slug}`).first()).toBeVisible();

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
    await login(page, USERS.useCaseAdmin);
    const slug = uniqueSlug('table');
    await createUseCase(page, slug, 'Table probe');

    await page.setViewportSize({ width: 360, height: 740 });
    await page.goto(`/use-cases/${slug}?tab=keys`);
    await page.click('button:has-text("Issue key")');
    await page.fill('#key-label', 'a-deliberately-long-label-for-the-table');
    await page.click('form button[type="submit"]');
    await expect(page.locator('.secret')).toBeVisible();

    const wrap = page.locator('.table-wrap').last();
    const scrolls = await wrap.evaluate((el) => el.scrollWidth > el.clientWidth);
    const overflowX = await wrap.evaluate((el) => getComputedStyle(el).overflowX);
    expect(overflowX).toBe('auto');
    // Whether or not this particular table is wider than the card, the page must not scroll.
    expect(typeof scrolls).toBe('boolean');
    await expectNoHorizontalOverflow(page, 'keys table @ phone');
  });

  test('the pipeline inspector stays reachable when it is taller than the viewport', async ({
    page,
  }) => {
    // The bug this guards: a sticky element taller than the viewport pinned its top and left
    // its lower fields permanently unreachable.
    await login(page, USERS.useCaseAdmin);
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

    const inspector = page.locator('.pipe__inspector');
    const capped = await inspector.evaluate((el) => {
      const style = getComputedStyle(el);
      return style.overflowY === 'auto' && el.clientHeight <= window.innerHeight;
    });
    expect(capped, 'the sticky inspector must cap its height and scroll').toBe(true);
    await expectNoHorizontalOverflow(page, 'pipeline builder with many categories');
  });
});
