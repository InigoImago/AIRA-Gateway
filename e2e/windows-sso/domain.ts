import { execFileSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Browser, Page, Response, chromium, expect } from '@playwright/test';
import { CONSOLE_URL, GATEWAY_URL } from '../stack';
import { acknowledgePrivacyNoticeIfDue } from '../tests/support';

/**
 * The Windows domain beside the stack, and the workstations of its people (`FRD-140`).
 *
 * Everything a test needs a person to *be* is made here, by the test, through the domain
 * controller's own tool — the way an administrator makes a person in AD. A person made for one test
 * is removed by it: the next run's domain holds nobody this run left behind.
 */

const TOOL = join(__dirname, '..', '..', 'tools', 'windows_sso.py');

/** What the domain is, asked of the tool that configures it rather than written down twice. */
export const DOMAIN = JSON.parse(
  execFileSync('python3', [TOOL, 'facts'], { encoding: 'utf8' }),
) as {
  realm: string;
  container: string;
  keycloakHost: string;
  servicePrincipal: string;
  people: string;
  roles: string;
  departments: string;
  groupPaths: Record<string, string>;
  federation: string;
  principalAccount: string;
  upnSuffix: string;
};

/**
 * How long Keycloak may answer from its own copy of a person, in this suite. The setup applies it;
 * the tests wait for it. Short so that a revocation can be watched to its end — the property is
 * the bound, and `tools/windows_sso.py` documents the one an installation should choose.
 */
export const CACHE_LIFESPAN_SECONDS = 10;

/** Run one command on the domain controller, as its administrator. */
function onTheDomain(command: string[], input?: string): Buffer {
  return execFileSync('docker', ['exec', '-i', DOMAIN.container, ...command], {
    input,
    stdio: ['pipe', 'pipe', 'pipe'],
  });
}

export interface Person {
  name: string;
  password: string;
}

let made = 0;

/**
 * A person of the domain, in the groups named.
 *
 * In the part of the directory Keycloak reads, unless `outsideTheDirectory` — then in the domain's
 * default container, where a ticket can be had and Keycloak finds nobody. Names stay within the
 * twenty characters an account name may have, and differ between runs.
 */
export function createPerson(
  options: { groups?: string[]; outsideTheDirectory?: boolean } = {},
): Person {
  made += 1;
  const name = `w${Date.now().toString(36).slice(-6)}${made.toString(36)}`;
  const password = `Pw-${randomBytes(12).toString('base64url')}-9a`;
  onTheDomain([
    'samba-tool',
    'user',
    'create',
    name,
    password,
    ...(options.outsideTheDirectory ? [] : [`--userou=${DOMAIN.people}`]),
  ]);
  // The principal name ends in the mail domain, not the realm — what most installations have,
  // and the case in which a federation that looks a ticket up by UPN finds nobody. The ticket is
  // still `<account>@<REALM>`.
  onTheDomain(['samba-tool', 'user', 'rename', name, `--upn=${name}@${DOMAIN.upnSuffix}`]);
  for (const group of options.groups ?? []) joinGroup({ name, password }, group);
  return { name, password };
}

export function removePerson(person: Person): void {
  onTheDomain(['samba-tool', 'user', 'delete', person.name]);
}

export function disablePerson(person: Person): void {
  onTheDomain(['samba-tool', 'user', 'disable', person.name]);
}

export function joinGroup(person: Person, group: string): void {
  onTheDomain(['samba-tool', 'group', 'addmembers', group, person.name]);
}

export function leaveGroup(person: Person, group: string): void {
  onTheDomain(['samba-tool', 'group', 'removemembers', group, person.name]);
}

/**
 * Log a person on to the domain, as Windows does at the desktop: their ticket-granting ticket, in
 * a credential cache of their own, on this machine.
 *
 * With `serviceTicket`, the workstation also already holds Keycloak's ticket — what a PC has that
 * opened the console before. Without it, the browser asks the KDC itself.
 */
export function logOn(person: Person, options: { serviceTicket?: boolean } = {}): string {
  const remote = `/tmp/ticket-${person.name}`;
  const cache = ['env', `KRB5CCNAME=FILE:${remote}`];
  onTheDomain([...cache, 'kinit', `${person.name}@${DOMAIN.realm}`], `${person.password}\n`);
  if (options.serviceTicket) onTheDomain([...cache, 'kvno', DOMAIN.servicePrincipal]);
  const local = join(mkdtempSync(join(tmpdir(), 'aira-ticket-')), 'ccache');
  writeFileSync(local, onTheDomain(['cat', remote]));
  onTheDomain(['rm', '-f', remote]);
  return local;
}

/**
 * Change the password of the account holding Keycloak's service principal. Every ticket the KDC
 * issues for Keycloak from now on is sealed with a key Keycloak's keytab does not have.
 */
export function rotateKeycloaksKey(): void {
  onTheDomain(['samba-tool', 'user', 'setpassword', DOMAIN.principalAccount, '--random-password']);
}

/** Export Keycloak's key again — the domain administrator's step after any such change. */
export function exportKeycloaksKey(): void {
  execFileSync('python3', [TOOL, 'keytab'], { stdio: 'pipe' });
}

/**
 * A browser on a workstation of the domain, holding `ticket` — or none.
 *
 * `trustsKeycloak` is the one thing an installation's IT has to roll out to every PC: the policy
 * that lets the browser answer Keycloak's Negotiate challenge (`AuthServerAllowlist` in Edge and
 * Chrome). Without it the browser never offers the ticket, which is a second way of having none.
 *
 * The credential cache is always named, even when empty, so that a ticket the machine running the
 * suite happens to hold can never stand in for the one a test meant.
 */
export async function workstation(
  ticket: string | null,
  options: { trustsKeycloak?: boolean } = {},
): Promise<Browser> {
  const dir = mkdtempSync(join(tmpdir(), 'aira-workstation-'));
  const krb5 = join(dir, 'krb5.conf');
  writeFileSync(krb5, execFileSync('python3', [TOOL, 'workstation'], { encoding: 'utf8' }));
  return chromium.launch({
    executablePath: process.env.AIRA_E2E_CHROME || undefined,
    args:
      options.trustsKeycloak === false ? [] : [`--auth-server-allowlist=${DOMAIN.keycloakHost}`],
    env: {
      ...process.env,
      KRB5_CONFIG: krb5,
      KRB5CCNAME: `FILE:${ticket ?? join(dir, 'no-ticket')}`,
    },
  });
}

/** A new page of the console in `browser`, counting every Keycloak form the browser is shown. */
async function consolePage(browser: Browser): Promise<{ page: Page; forms: () => number }> {
  const page = await (await browser.newContext({ baseURL: CONSOLE_URL })).newPage();
  let forms = 0;
  // Keycloak's form, and the page that sends a browser without a ticket on to it, are both
  // answered under `login-actions`; a ticket accepted at the first request never gets there.
  page.on('request', (request) => {
    if (request.url().includes('/login-actions/')) forms += 1;
  });
  return { page, forms: () => forms };
}

/**
 * Open the console and require that `person` is signed in **without any form** — the sign-on.
 *
 * Nothing here can type a password: the person's is known only to the test, and the only request
 * a form would have made is counted. So the name in the header got there by the ticket.
 */
export async function signOn(browser: Browser, person: Person): Promise<Page> {
  const { page, forms } = await consolePage(browser);
  const notice = page.waitForResponse(
    (response: Response) =>
      response.url().includes('/api/v1/privacy-notice') && response.request().method() === 'GET',
    { timeout: 60_000 },
  );
  await page.goto('/');
  await expect(page.locator('.aira-user__name')).toHaveText(person.name, { timeout: 30_000 });
  expect(forms(), 'a Keycloak form was shown on the way in').toBe(0);
  await acknowledgePrivacyNoticeIfDue(page, await notice);
  return page;
}

/** Open the console and require Keycloak's form instead of a session: nobody is signed in. */
export async function shownTheForm(browser: Browser): Promise<Page> {
  const { page } = await consolePage(browser);
  await page.goto('/');
  await expect(page.locator('#kc-login')).toBeVisible({ timeout: 30_000 });
  await expect(page.locator('.aira-user__name')).toHaveCount(0);
  expect(new URL(page.url()).hostname).toBe(DOMAIN.keycloakHost);
  return page;
}

/** The access and refresh token the console holds for the person signed in on `page`. */
export async function tokensOf(page: Page): Promise<{ access: string; refresh: string }> {
  return page.evaluate(() => ({
    access: sessionStorage.getItem('access_token') ?? '',
    refresh: sessionStorage.getItem('refresh_token') ?? '',
  }));
}

/**
 * Renew a session the way the console does, with its refresh token — at the issuer the token
 * itself names, because Keycloak refuses a token presented under any other host.
 */
export async function renew(tokens: {
  access: string;
  refresh: string;
}): Promise<{ status: number; access: string; error: string }> {
  const issuer = (
    JSON.parse(Buffer.from(tokens.access.split('.')[1], 'base64url').toString()) as { iss: string }
  ).iss;
  const response = await fetch(`${issuer}/protocol/openid-connect/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      grant_type: 'refresh_token',
      client_id: 'aira-gateway',
      refresh_token: tokens.refresh,
    }),
  });
  const body = (await response.json()) as { access_token?: string; error?: string };
  return { status: response.status, access: body.access_token ?? '', error: body.error ?? '' };
}

/** One call to the gateway with a bearer token, as any client of it makes one. */
export async function gateway(
  token: string | null,
  path: string,
  init: { method?: string; useCase?: string; body?: unknown } = {},
): Promise<{ status: number; body: Record<string, unknown> }> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (init.useCase) headers['X-AIRA-Use-Case'] = init.useCase;
  const response = await fetch(`${GATEWAY_URL}${path}`, {
    method: init.method ?? 'GET',
    headers,
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
  });
  const text = await response.text();
  return {
    status: response.status,
    body: text ? (JSON.parse(text) as Record<string, unknown>) : {},
  };
}
