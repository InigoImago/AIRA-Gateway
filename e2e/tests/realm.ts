import { KEYCLOAK_URL, setting } from '../stack';
import { remember } from '../created';

/**
 * The realm's administration API, for the few tests that must write to it.
 *
 * **The master administrator is the stack's**, read through `stack.ts` — Compose takes it from
 * `KEYCLOAK_ADMIN` / `KEYCLOAK_ADMIN_PASSWORD`, and a stack started with another password made a
 * literal `admin` fail at its first call. **A token per call**: the master realm's live sixty
 * seconds. And every answer is checked, because a refused call here is a test acting on something
 * other than what it set up.
 */
export async function masterToken(): Promise<string> {
  const response = await fetch(`${KEYCLOAK_URL}/realms/master/protocol/openid-connect/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      grant_type: 'password',
      client_id: 'admin-cli',
      username: setting('KEYCLOAK_ADMIN'),
      password: setting('KEYCLOAK_ADMIN_PASSWORD'),
    }),
  });
  if (!response.ok) {
    throw new Error(`no master-realm token: ${response.status} ${await response.text()}`);
  }
  return ((await response.json()) as { access_token: string }).access_token;
}

async function admin(method: string, path: string, body?: unknown): Promise<Response> {
  return fetch(`${KEYCLOAK_URL}/admin/realms/aira${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${await masterToken()}`,
      'Content-Type': 'application/json',
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

/**
 * A realm user of this test's own, registered before it exists so the teardown removes it: the
 * person a test may sign out of every session without signing anybody else out.
 */
export async function createRealmUser(
  username: string,
  password: string,
): Promise<{ id: string; username: string; password: string }> {
  const made = await admin('POST', '/users', {
    username,
    enabled: true,
    emailVerified: true,
    email: `${username}@example.invalid`,
    firstName: 'E2E',
    lastName: 'Session',
    credentials: [{ type: 'password', value: password, temporary: false }],
  });
  if (made.status !== 201) {
    throw new Error(`could not create ${username}: ${made.status} ${await made.text()}`);
  }
  const id = (made.headers.get('Location') ?? '').replace(/\/$/, '').split('/').pop() ?? '';
  if (!id) throw new Error(`no id for ${username}`);
  remember('keycloak-user', id);
  return { id, username, password };
}

/** End every Keycloak session of one user — the state a Keycloak restart leaves them in. */
export async function logoutRealmUser(id: string): Promise<void> {
  const response = await admin('POST', `/users/${id}/logout`);
  if (response.status !== 204) {
    throw new Error(`could not end the sessions of ${id}: ${response.status}`);
  }
}

export async function deleteRealmUser(id: string): Promise<void> {
  const response = await admin('DELETE', `/users/${id}`);
  if (!response.ok && response.status !== 404) {
    throw new Error(`could not remove ${id}: ${response.status}`);
  }
}
