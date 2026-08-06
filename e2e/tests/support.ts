import { Page, expect } from '@playwright/test';

/** Demo accounts seeded into the Keycloak realm (deploy/compose/keycloak/realms). */
export const USERS = {
  globalAdmin: { username: 'admin', password: 'demo-password' },
  useCaseAdmin: { username: 'ucadmin', password: 'demo-password' },
  useCaseUser: { username: 'ucuser', password: 'demo-password' },
  governance: { username: 'itgov', password: 'demo-password' },
} as const;

/**
 * Complete the real Keycloak authorization-code flow.
 *
 * Deliberately driven through the browser rather than a password grant: the dev realm has the
 * direct-access grant disabled (ADR-0007), so this exercises exactly the flow a user takes,
 * including the PKCE challenge the SPA generates.
 */
export async function login(page: Page, user: { username: string; password: string }) {
  await page.goto('/');

  // Either the app redirects to Keycloak (first visit) or an existing session lands straight in
  // the SPA. Waiting for whichever appears avoids racing the redirect.
  const loginButton = page.locator('#kc-login');
  const userName = page.locator('.aira-user__name');
  await expect(loginButton.or(userName).first()).toBeVisible({
    timeout: 30_000,
  });

  if (await loginButton.isVisible()) {
    await page.fill('#username', user.username);
    await page.fill('#password', user.password);
    await loginButton.click();
  }

  await expect(userName).toHaveText(user.username, { timeout: 30_000 });
}

/** Log out so the next login starts from a clean Keycloak session. */
export async function logout(page: Page) {
  await page.click('.aira-user button');
  await expect(page.locator('#kc-login').or(page.locator('#username')).first()).toBeVisible({
    timeout: 30_000,
  });
}

/**
 * Assert the document is not wider than the viewport.
 *
 * This is the objective form of "the page overflows": if any element pushes the document past
 * the viewport width, the whole page scrolls sideways. A tolerance of 1px absorbs sub-pixel
 * rounding at fractional device scales.
 */
export async function expectNoHorizontalOverflow(page: Page, context: string) {
  const overflow = await page.evaluate(() => {
    const doc = document.documentElement;
    // Report the widest offenders so a failure names the element, not just the number.
    const guilty: string[] = [];
    for (const el of Array.from(document.querySelectorAll<HTMLElement>('body *'))) {
      const rect = el.getBoundingClientRect();
      if (rect.width > 0 && rect.right > doc.clientWidth + 1) {
        const id = el.id ? `#${el.id}` : '';
        const cls =
          el.className && typeof el.className === 'string'
            ? `.${el.className.trim().split(/\s+/).join('.')}`
            : '';
        guilty.push(`${el.tagName.toLowerCase()}${id}${cls} (right=${Math.round(rect.right)})`);
      }
    }
    return {
      scrollWidth: doc.scrollWidth,
      clientWidth: doc.clientWidth,
      guilty: guilty.slice(0, 5),
    };
  });

  expect(
    overflow.scrollWidth,
    `${context}: document scrolls horizontally (${overflow.scrollWidth}px in a ${overflow.clientWidth}px viewport). Widest offenders: ${overflow.guilty.join(', ') || 'none identified'}`,
  ).toBeLessThanOrEqual(overflow.clientWidth + 1);
}

/** Create a use case through the UI, returning its slug. */
export async function createUseCase(page: Page, slug: string, name: string) {
  await page.goto('/use-cases');
  await page.fill('#uc-slug', slug);
  await page.fill('#uc-name', name);
  await page.click('button[type="submit"]');
  await expect(page.locator(`text=${slug}`).first()).toBeVisible();
  return slug;
}

/** A slug that is unique per run so reruns do not collide on the unique constraint. */
export function uniqueSlug(prefix: string) {
  return `${prefix}-${Math.random().toString(36).slice(2, 8)}`;
}

/**
 * Assert that the controls of an inline form line up.
 *
 * A row of fields is bottom-aligned only as long as every field is equally tall. As soon as one
 * carries a hint under its input it grows, its control is pushed up, and the row turns into a
 * staircase — visible at a glance to a person, invisible to a DOM assertion and to jsdom, which
 * has no layout at all. Controls are grouped by the row they landed in, since these forms wrap.
 */
export async function expectFormControlsAligned(page: Page, context: string) {
  const rows = await page.evaluate(() => {
    const out: { form: number; rows: Record<string, string[]> }[] = [];
    document.querySelectorAll('form.form-inline').forEach((form, index) => {
      const grouped: Record<string, string[]> = {};
      form.querySelectorAll('input:not([type=checkbox]), select, button').forEach((element) => {
        const rect = element.getBoundingClientRect();
        if (rect.width === 0) return;
        // Group by the row band the control sits in; the forms wrap on purpose.
        const band = String(Math.round(rect.top / 40));
        const id = (element as HTMLElement).id || element.tagName.toLowerCase();
        (grouped[band] ??= []).push(`${id}@${Math.round(rect.top)}`);
      });
      out.push({ form: index, rows: grouped });
    });
    return out;
  });

  for (const { form, rows: bands } of rows) {
    for (const [band, controls] of Object.entries(bands)) {
      const tops = controls.map((c) => Number(c.split('@')[1]));
      const spread = Math.max(...tops) - Math.min(...tops);
      expect(
        spread,
        `${context}: form ${form}, row ${band} is a staircase — ${controls.join(', ')}`,
      ).toBeLessThanOrEqual(2);
    }
  }
}

/**
 * Make sure a use case exists, creating it only if it really is absent.
 *
 * Deliberately not `if ((await locator.count()) === 0) create(...)`: the list loads
 * asynchronously, so that count is taken while the table is still a spinner and answers 0 for
 * every slug. The create then fails on the unique constraint and the test blames the wrong
 * thing. Wait for the list to have finished loading first, then decide.
 */
export async function ensureUseCase(page: Page, slug: string, name: string) {
  await page.goto('/use-cases');
  // Either rows or the empty state — both mean the request came back.
  await expect(page.locator('table.table').or(page.locator('.empty')).first()).toBeVisible({
    timeout: 30_000,
  });
  if ((await page.locator(`code:has-text("${slug}")`).count()) === 0) {
    await createUseCase(page, slug, name);
  }
  return slug;
}
