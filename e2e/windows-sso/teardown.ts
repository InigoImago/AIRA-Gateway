import { execFileSync } from 'node:child_process';
import { join } from 'node:path';
import tidy from '../teardown';

/**
 * Remove what the suite made, then take the realm's half of the sign-on away — passed or not.
 *
 * The use cases first, through the console as the realm's own administrator, while the realm
 * still signs people on either way; the federation last, in a `finally`, because a realm left
 * federated answers every other suite's directory searches with people from the domain.
 */
export default async function teardown(): Promise<void> {
  try {
    await tidy();
  } finally {
    execFileSync('python3', [join(__dirname, '..', '..', 'tools', 'windows_sso.py'), 'remove'], {
      stdio: 'inherit',
    });
  }
}
