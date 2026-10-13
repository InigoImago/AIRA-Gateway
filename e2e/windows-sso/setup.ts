import { execFileSync } from 'node:child_process';
import { join } from 'node:path';
import { CACHE_LIFESPAN_SECONDS, DOMAIN } from './domain';

/**
 * Give the development realm its half of the Windows sign-on, once, before any test (`FRD-140`).
 *
 * Two preconditions are checked first and **refused, never skipped**: a suite about the sign-on
 * that skips when the domain is absent reports green about nothing, and one that runs without the
 * browser's Kerberos library fails every test with a form and no reason.
 */
export default async function setup(): Promise<void> {
  let health = '';
  try {
    health = execFileSync(
      'docker',
      ['inspect', '--format', '{{.State.Health.Status}}', DOMAIN.container],
      { encoding: 'utf8', stdio: 'pipe' },
    ).trim();
  } catch {
    health = 'absent';
  }
  if (health !== 'healthy') {
    throw new Error(
      `The Windows domain (${DOMAIN.container}) is ${health || 'not running'}. ` +
        '`make test-windows-sso` starts it; so does `make windows-sso-up`.',
    );
  }

  // Chromium loads MIT Kerberos at run time to answer a Negotiate challenge, and without the
  // library it answers none — silently, with the form.
  const libraries = execFileSync('ldconfig', ['-p'], { encoding: 'utf8' });
  if (!libraries.includes('libgssapi_krb5.so.2')) {
    throw new Error(
      'Chromium needs libgssapi_krb5.so.2 to sign anybody on with a ticket ' +
        '(Debian/Ubuntu: `apt-get install libgssapi-krb5-2`).',
    );
  }

  execFileSync(
    'python3',
    [
      join(__dirname, '..', '..', 'tools', 'windows_sso.py'),
      'apply',
      '--cache-lifespan',
      String(CACHE_LIFESPAN_SECONDS),
    ],
    { stdio: 'inherit' },
  );
}
