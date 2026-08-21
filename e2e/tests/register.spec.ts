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

  test('carries a use case’s purpose, storage and controls on one row', async ({ page }) => {
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
    // A fresh use case stores prompts on the default clock and releases nothing.
    await expect(row).toContainText('day(s)');
    await expect(row).toContainText('none released');
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
