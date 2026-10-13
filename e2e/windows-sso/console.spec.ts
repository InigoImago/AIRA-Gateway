import { Browser, expect, test } from '@playwright/test';
import {
  CACHE_LIFESPAN_SECONDS,
  Person,
  createPerson,
  disablePerson,
  exportKeycloaksKey,
  leaveGroup,
  logOn,
  removePerson,
  renew,
  rotateKeycloaksKey,
  shownTheForm,
  signOn,
  tokensOf,
  workstation,
} from './domain';

/**
 * **The console, signed on to with Windows** (`FRD-140`): who gets in with the ticket they were
 * given at logon, and who does not.
 *
 * Every test makes its own person in the domain and removes them, and opens its own browser — a
 * workstation of the domain, holding that person's ticket or none. The console and Keycloak are
 * the stack's own; nothing in AIRA knows a ticket was involved, which is the point being proven.
 */

/** Long enough for Keycloak's copy of a person to expire, and the change to be read from AD. */
const BOUND_MS = (CACHE_LIFESPAN_SECONDS + 20) * 1000;

test.describe('Windows sign-on — the console', () => {
  let people: Person[] = [];
  let browsers: Browser[] = [];

  function person(options: Parameters<typeof createPerson>[0] = {}): Person {
    const made = createPerson(options);
    people.push(made);
    return made;
  }

  async function pc(ticket: string | null, options: { trustsKeycloak?: boolean } = {}) {
    const browser = await workstation(ticket, options);
    browsers.push(browser);
    return browser;
  }

  test.afterEach(async () => {
    for (const browser of browsers) await browser.close();
    for (const made of people) removePerson(made);
    people = [];
    browsers = [];
  });

  test('a person logged on to the domain is in, with no form, holding the role AD gives them', async () => {
    const anna = person({ groups: ['it-steuerung'] });

    const page = await signOn(await pc(logOn(anna)), anna);

    // The role comes from the AD group, through Keycloak's group of the same name — the realm's
    // `/aira/it-steuerung`, which `AIRA_ROLE_GROUPS` already names. Nothing in AIRA was changed.
    await expect(page.locator('.aira-user__role[data-role="it-steuerung"]')).toBeVisible();
    await expect(page.locator('[data-role="global-admin"]')).toHaveCount(0);
  });

  test('without a ticket the form is shown, and the domain password is what it takes', async () => {
    // A PC outside the domain, or a person who logged on to Windows with another account: nothing
    // to present, so Keycloak's own form — and the same directory behind it.
    const bernd = person({ groups: ['it-steuerung'] });

    const page = await shownTheForm(await pc(null));

    await page.fill('#username', bernd.name);
    await page.fill('#password', bernd.password);
    await page.click('#kc-login');
    await expect(page.locator('.aira-user__name')).toHaveText(bernd.name, { timeout: 30_000 });
  });

  test('a browser not told to trust Keycloak shows the form, whatever ticket the PC holds', async () => {
    // The policy an installation rolls out to every PC (`AuthServerAllowlist`). Without it the
    // browser never offers the ticket — the sign-on fails safe, into the form.
    const clara = person({ groups: ['it-steuerung'] });

    await shownTheForm(await pc(logOn(clara), { trustsKeycloak: false }));
  });

  test('a person disabled in AD is not let in, not even with a ticket issued before', async () => {
    // The workstation already holds Keycloak's ticket, from before the account was disabled — a
    // ticket that is still valid, for the right service. What refuses it is Keycloak reading the
    // account from the directory, not the KDC.
    const dieter = person({ groups: ['it-steuerung'] });
    const ticket = logOn(dieter, { serviceTicket: true });

    disablePerson(dieter);

    // And the domain issues a disabled account no new ticket at all.
    expect(() => logOn(dieter)).toThrow(/revoked/i);
    await shownTheForm(await pc(ticket));
  });

  test('a person outside the part of AD Keycloak reads is not let in by their ticket', async () => {
    // A real account of the domain, with a valid ticket — in the default `CN=Users` container,
    // outside the federation's `OU=People`. An installation scopes who may sign on exactly so.
    const erik = person({ outsideTheDirectory: true });

    await shownTheForm(await pc(logOn(erik)));
  });

  test('a ticket for a key Keycloak does not hold is not a sign-on', async () => {
    // The service account's password changes — and with it the key every new ticket is sealed
    // with — while Keycloak keeps the keytab exported before. A ticket Keycloak cannot open is one
    // it cannot have been meant to read, whoever made it; the browser falls back to the form.
    const frieda = person({ groups: ['it-steuerung'] });
    rotateKeycloaksKey();
    try {
      await shownTheForm(await pc(logOn(frieda)));
    } finally {
      exportKeycloaksKey();
    }
    // And with the key exported again, the same person is in — so it was the key.
    await signOn(await pc(logOn(frieda)), frieda);
  });

  test('a person taken out of the AD group loses the role, within the bound', async () => {
    const gerd = person({ groups: ['it-steuerung'] });
    const page = await signOn(await pc(logOn(gerd)), gerd);
    await expect(page.locator('.aira-user__role[data-role="it-steuerung"]')).toBeVisible();

    leaveGroup(gerd, 'it-steuerung');

    // Each round drops the console's token and reloads, so the console asks Keycloak for a new
    // one — through the session it already has, without a form — and shows what that token says.
    await expect
      .poll(
        async () => {
          await page.evaluate(() => sessionStorage.clear());
          await page.goto('/');
          await expect(page.locator('.aira-user__name')).toHaveText(gerd.name, { timeout: 30_000 });
          return page.locator('.aira-user__role[data-role="it-steuerung"]').count();
        },
        { timeout: BOUND_MS, intervals: [2_000] },
      )
      .toBe(0);
    await expect(page.getByTestId('no-role')).toBeVisible();
  });

  test('a person disabled in AD can no longer renew their session, within the bound', async () => {
    const hanna = person({ groups: ['it-steuerung'] });
    const page = await signOn(await pc(logOn(hanna)), hanna);
    const tokens = await tokensOf(page);
    expect((await renew(tokens)).status, 'renewal before the account is disabled').toBe(200);

    disablePerson(hanna);

    // What the console does by itself before its token runs out. Once refused, the console has
    // nothing left to renew with, and the gateway sees this person for at most the lifetime of the
    // last access token.
    await expect
      .poll(async () => (await renew(tokens)).error, { timeout: BOUND_MS, intervals: [2_000] })
      .toBe('invalid_grant');

    await page.evaluate(() => sessionStorage.clear());
    await page.goto('/');
    await expect(page.locator('#kc-login')).toBeVisible({ timeout: 30_000 });
    await expect(page.locator('.aira-user__name')).toHaveCount(0);
  });
});
