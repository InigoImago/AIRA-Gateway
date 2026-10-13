import { Browser, expect, test } from '@playwright/test';
import { USERS, createUseCase, grantGroup, login, uniqueSlug } from '../tests/support';
import {
  CACHE_LIFESPAN_SECONDS,
  DOMAIN,
  Person,
  createPerson,
  gateway,
  leaveGroup,
  logOn,
  removePerson,
  renew,
  signOn,
  tokensOf,
  workstation,
} from './domain';

/**
 * **The gateway, called with the token of a Windows sign-on** (`FRD-140`).
 *
 * The token the console holds after a person signed on with their ticket is presented to the
 * gateway directly, as any client of it would. The use case is granted to an AD **department** —
 * `/abteilungen/kundendienst`, the realm's group that the domain's `kundendienst` lands in — so the
 * decision the gateway makes is one AD's membership made.
 *
 * The model is `mock-1`, the stack's own test double: a real call through the whole request path,
 * refused or answered exactly as any model would be, at no cost.
 */

const DEPARTMENT = 'kundendienst';
const DEPARTMENT_PATH = `${DOMAIN.groupPaths[DOMAIN.departments]}/${DEPARTMENT}`;
const ASK = { contents: [{ role: 'user', parts: [{ text: 'Is this the Windows sign-on?' }] }] };
const BOUND_MS = (CACHE_LIFESPAN_SECONDS + 20) * 1000;

test.describe('Windows sign-on — the gateway', () => {
  let people: Person[] = [];
  let browsers: Browser[] = [];
  let slug = '';

  test.beforeAll(async ({ browser }) => {
    // The use case, by the realm's own administrator, granted to the department as an
    // administrator grants one to any group (`FRD-209`).
    const page = await browser.newPage();
    await login(page, USERS.globalAdmin);
    slug = await createUseCase(page, uniqueSlug('windows'), 'Windows sign-on probe');
    await grantGroup(page, slug, DEPARTMENT_PATH, 'user');
    await page.close();
  });

  test.afterEach(async () => {
    for (const browser of browsers) await browser.close();
    for (const made of people) removePerson(made);
    people = [];
    browsers = [];
  });

  /** A person of the domain, signed on with their ticket, and the tokens the console holds. */
  async function signedOn(groups: string[]) {
    const person = createPerson({ groups });
    people.push(person);
    const browser = await workstation(logOn(person));
    browsers.push(browser);
    const page = await signOn(browser, person);
    return { person, tokens: await tokensOf(page) };
  }

  /** The grant crosses Kafka and a five-second cache (`FRD-209`); asked until it has arrived. */
  async function admitted(token: string) {
    await expect
      .poll(async () => (await gateway(token, `/uc/${slug}/v1beta/me/budget`)).status, {
        timeout: 30_000,
        intervals: [500],
      })
      .toBe(200);
  }

  test('a member of the department is let through, and one outside it is refused', async () => {
    const member = await signedOn([DEPARTMENT]);
    const outsider = await signedOn(['entwicklung']);

    await admitted(member.tokens.access);
    // The gateway knows who is calling: the domain account, from the token.
    const budget = await gateway(member.tokens.access, `/uc/${slug}/v1beta/me/budget`);
    expect(budget.body.person).toBe(member.person.name);

    const answered = await gateway(member.tokens.access, '/v1beta/models/mock-1:generateContent', {
      method: 'POST',
      useCase: slug,
      body: ASK,
    });
    expect(answered.status, JSON.stringify(answered.body)).toBe(200);
    expect(answered.body.candidates).toBeTruthy();

    // **Compared with the member, after the member was let through**: the grant has arrived, so a
    // refusal now is the decision and not the propagation.
    const refused = await gateway(outsider.tokens.access, '/v1beta/models/mock-1:generateContent', {
      method: 'POST',
      useCase: slug,
      body: ASK,
    });
    expect(refused.status).toBe(403);
    expect(JSON.stringify(refused.body)).toContain('PERMISSION_DENIED');

    // And without any token, nobody.
    expect((await gateway(null, `/uc/${slug}/v1beta/me/budget`)).status).toBe(401);
  });

  test('a person taken out of the department loses the use case, within the bound', async () => {
    const { person, tokens } = await signedOn([DEPARTMENT]);
    await admitted(tokens.access);

    leaveGroup(person, DEPARTMENT);

    // The console renews its token before it runs out; each new token says what AD says once
    // Keycloak's copy of the person has expired, and the gateway decides on the token it is given.
    let latest = tokens.access;
    await expect
      .poll(
        async () => {
          const renewed = await renew(tokens);
          expect(renewed.status, renewed.error).toBe(200);
          latest = renewed.access;
          return (await gateway(latest, `/uc/${slug}/v1beta/me/budget`)).status;
        },
        { timeout: BOUND_MS, intervals: [2_000] },
      )
      .toBe(403);
    const refused = await gateway(latest, '/v1beta/models/mock-1:generateContent', {
      method: 'POST',
      useCase: slug,
      body: ASK,
    });
    expect(refused.status).toBe(403);
  });
});
