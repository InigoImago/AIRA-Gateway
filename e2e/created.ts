import { appendFileSync, existsSync, mkdirSync, readFileSync, rmSync } from 'node:fs';
import { dirname, join } from 'node:path';

/**
 * What this suite made, so the teardown can remove **exactly that** and nothing else.
 *
 * ## Why a register and not a pattern
 *
 * The suite names its use cases `<prefix>-<6 base36>` (`uniqueSlug`), and a sweep could match that
 * shape. It must not. A person is free to call a use case `matrix-test` or `foo-abc123`, and a
 * teardown that deletes by *shape* would take their work with it — this repository has two such
 * use cases in its demo today, and `seed_demo --fresh` already deletes them, which is why the
 * clean-up after a session had to be written by hand rather than delegated to that.
 *
 * So the file is the contract: a slug is removed only because this run wrote it down here.
 *
 * ## Why a file and not a variable
 *
 * `globalTeardown` runs in its own process. Nothing in a worker's memory survives to reach it.
 *
 * Appends are one line each and `workers: 1` is set in the config, so there is no interleaving to
 * guard against; `appendFileSync` would stay safe for several workers on POSIX anyway, since a
 * single small write to a file opened `O_APPEND` is not split.
 *
 * ## What happens when the file is left behind
 *
 * The teardown deletes it after a successful pass. A run that is **killed** leaves it, and the
 * next run's teardown finds those slugs too and removes them — which is the case that produced
 * 1721 leftover use cases in the demo database, none of them from a run that finished.
 */
// Anchored to this file's own directory, so it does not depend on the working directory a
// runner happens to use — `npx playwright test` from `e2e/` and from the repository root both
// resolve to the same register.
const REGISTER = join(__dirname, '.artifacts', 'use-cases.txt');

export function rememberUseCase(slug: string): void {
  mkdirSync(dirname(REGISTER), { recursive: true });
  appendFileSync(REGISTER, `${slug}\n`, 'utf-8');
}

export function rememberedUseCases(): string[] {
  if (!existsSync(REGISTER)) {
    return [];
  }
  const slugs = readFileSync(REGISTER, 'utf-8')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
  return [...new Set(slugs)];
}

export function forgetUseCases(): void {
  rmSync(REGISTER, { force: true });
}
