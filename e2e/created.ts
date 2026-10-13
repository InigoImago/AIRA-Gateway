import {
  appendFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
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
 * So the file is the contract: a thing is removed only because this run wrote it down here, and it
 * is written down **before** it is made, so a creation that succeeds and then fails its next
 * assertion is still removed.
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
 * The teardown removes what it could and keeps the rest. A run that is **killed** leaves it, and
 * the next run's teardown finds those entries too and removes them — which is the case that
 * produced 1721 leftover use cases in the demo database, none of them from a run that finished.
 */
// Anchored to this file's own directory, so it does not depend on the working directory a
// runner happens to use — `npx playwright test` from `e2e/` and from the repository root both
// resolve to the same register.
const REGISTER = join(__dirname, '.artifacts', 'use-cases.txt');
/** Everything else the suite makes: one JSON object per line (`Made`). */
const OTHERS = join(__dirname, '.artifacts', 'created.jsonl');

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

/**
 * What the register can name besides a use case, and what the teardown does with each:
 *
 * - `rule` — an anomaly rule, by **name** (the console creates it and the server gives the id):
 *   deleted.
 * - `role` — a role, by slug: deleted.
 * - `model` — a catalogue entry, by name: deleted.
 * - `installation-budget` — by id: deleted.
 * - `run` — a pipeline-test run, by id. Runs are evidence and the API never deletes one
 *   (`FRD-504` §5.7); one a test made about a test's use case is removed from the database.
 * - `keycloak-user` — a realm user a test made, by id: deleted.
 * - `release` — a use case **this suite did not make** whose release a test changes, by slug, with
 *   the release as it was found: put back.
 */
export type Kind =
  'rule' | 'role' | 'model' | 'installation-budget' | 'run' | 'keycloak-user' | 'release';

export interface Made {
  kind: Kind;
  name: string;
  /** What putting it back needs — the release as it was found, for `release`. */
  was?: unknown;
}

export function remember(kind: Kind, name: string, was?: unknown): void {
  mkdirSync(dirname(OTHERS), { recursive: true });
  appendFileSync(OTHERS, `${JSON.stringify({ kind, name, was })}\n`, 'utf-8');
}

/** Every entry, the **first** of each `kind`+`name` kept: for a release that is the one found. */
export function remembered(): Made[] {
  if (!existsSync(OTHERS)) {
    return [];
  }
  const seen = new Set<string>();
  const out: Made[] = [];
  for (const line of readFileSync(OTHERS, 'utf-8').split('\n')) {
    if (!line.trim()) continue;
    const entry = JSON.parse(line) as Made;
    const key = `${entry.kind}\u0000${entry.name}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(entry);
  }
  return out;
}

/** Drop entries that have been dealt with — a test that tidied after itself, or the teardown. */
export function forget(done: (entry: Made) => boolean): void {
  const left = remembered().filter((entry) => !done(entry));
  if (left.length === 0) {
    rmSync(OTHERS, { force: true });
    return;
  }
  writeFileSync(OTHERS, left.map((entry) => JSON.stringify(entry)).join('\n') + '\n', 'utf-8');
}
