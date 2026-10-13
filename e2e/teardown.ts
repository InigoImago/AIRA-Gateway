import { execFileSync } from 'node:child_process';
import { chromium } from '@playwright/test';
import { CONSOLE_URL, container, setting } from './stack';
import { Made, forget, remembered, rememberedUseCases } from './created';
import { USERS, login } from './tests/support';
import { deleteRealmUser } from './tests/realm';

/**
 * Remove what this suite created, and nothing else.
 *
 * ## The bill this pays
 *
 * 90 calls to `createUseCase` across the suite and not one removal. Measured on the demo database
 * after a few sessions: **1734 use cases in Management and 1946 in the gateway's read-model**, of
 * which four were the demo's. A global administrator opening that list learns nothing except that
 * the list is long, the register screen counts them all, and the seed's own comment already
 * records the same thing happening at 801. The same holds for the rules, roles, models and
 * budgets a test makes — a global rule left behind is a **policy** every tick applies.
 *
 * ## Only what this run made
 *
 * Every entry comes from `created.ts`, written at the moment of creation. Nothing is matched by
 * shape: a person may name a use case anything, and this demo holds two that a person made. That
 * is also why `seed_demo --fresh`, which deletes everything that is not one of the demo's own six,
 * is **not** what runs here.
 *
 * ## Through the API, with the console's own token
 *
 * The alternative was driving the UI once per row, which at several hundred rows is minutes of
 * clicking. The token is read out of the page the way `grantGroup` does — the SPA holds it in
 * session storage — and the deletions go straight to Management, which announces each one so the
 * gateway's read-model follows. A `404` is success: a test that removed its own, or a run that
 * never got as far as creating it, must not turn tidying into a failure.
 */
export default async function teardown(): Promise<void> {
  const slugs = rememberedUseCases();
  const made = remembered();
  if (slugs.length === 0 && made.length === 0) {
    return;
  }

  const browser = await chromium.launch({
    executablePath: process.env.AIRA_E2E_CHROME || undefined,
  });
  const page = await browser.newPage({ baseURL: CONSOLE_URL });
  const done = new Set<string>();
  const failed: string[] = [];
  try {
    await login(page, USERS.globalAdmin);
    const result = await page.evaluate(
      async ({ wanted, others }: { wanted: string[]; others: Made[] }) => {
        const token =
          sessionStorage.getItem('access_token') ?? localStorage.getItem('access_token') ?? '';
        const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };
        const call = async (method: string, path: string, body?: unknown) =>
          fetch(path, {
            method,
            headers,
            body: body === undefined ? undefined : JSON.stringify(body),
          });
        let removed = 0;
        let absent = 0;
        const refused: string[] = [];
        for (const slug of wanted) {
          const response = await call('DELETE', `/api/v1/use-cases/${encodeURIComponent(slug)}/`);
          if (response.status === 404) absent += 1;
          else if (response.ok) removed += 1;
          else refused.push(`${slug}:${response.status}`);
        }

        const settled: string[] = [];
        const notSettled: string[] = [];
        const settle = (entry: Made, ok: boolean, why = '') =>
          (ok ? settled : notSettled).push(
            ok ? `${entry.kind}\u0000${entry.name}` : `${entry.kind} ${entry.name}${why}`,
          );
        const rules = others.some((entry) => entry.kind === 'rule')
          ? await (await call('GET', '/api/v1/anomaly-rules/')).json()
          : [];
        const ruleRows = (Array.isArray(rules) ? rules : (rules.results ?? [])) as {
          id: number;
          name: string;
        }[];
        for (const entry of others) {
          let response: Response | null = null;
          if (entry.kind === 'rule') {
            const found = ruleRows.filter((rule) => rule.name === entry.name);
            const statuses = [];
            for (const rule of found) {
              statuses.push((await call('DELETE', `/api/v1/anomaly-rules/${rule.id}/`)).status);
            }
            settle(
              entry,
              statuses.every((status) => status === 204 || status === 404),
            );
            continue;
          }
          if (entry.kind === 'role') {
            response = await call('DELETE', `/api/v1/roles/${encodeURIComponent(entry.name)}/`);
          } else if (entry.kind === 'model') {
            response = await call('DELETE', `/api/v1/models/${encodeURIComponent(entry.name)}/`);
          } else if (entry.kind === 'installation-budget') {
            response = await call('DELETE', `/api/v1/installation-budgets/${entry.name}/`);
          } else if (entry.kind === 'release') {
            response = await call('PATCH', `/api/v1/use-cases/${encodeURIComponent(entry.name)}/`, {
              allowed_models: entry.was,
            });
          } else {
            continue; // runs and realm users are not the console's to remove; see below
          }
          settle(entry, response.ok || response.status === 404, `:${response.status}`);
        }
        return { removed, absent, refused, settled, notSettled };
      },
      { wanted: slugs, others: made },
    );
    result.settled.forEach((key) => done.add(key));
    failed.push(...result.notSettled);

    // Said out loud, because a clean-up that silently does nothing is indistinguishable from one
    // that is not running at all — the shape this repository keeps meeting as "a guard that cannot
    // fail". The counts are the evidence that it did something.
    console.log(
      `[teardown] use cases: ${result.removed} removed, ${result.absent} already gone` +
        (result.refused.length
          ? `, ${result.refused.length} refused: ${result.refused.join(', ')}`
          : ''),
    );
    // **The use-case register is left in place**, and that is the correction that cost a run to
    // find. Retiring is only half of the clean-up: a tombstone is kept on purpose (`FRD-607`), and
    // the purge that removes it is a separate, demo-guarded step outside this process. So
    // `purge_test_use_cases` consumes the register and the Makefile deletes it afterwards.
  } finally {
    await browser.close();
  }

  for (const entry of made.filter((entry) => entry.kind === 'run')) {
    try {
      removeRun(entry.name);
      done.add(`${entry.kind}\u0000${entry.name}`);
    } catch (error) {
      failed.push(`run ${entry.name}: ${String(error).slice(0, 200)}`);
    }
  }
  for (const entry of made.filter((entry) => entry.kind === 'keycloak-user')) {
    try {
      await deleteRealmUser(entry.name);
      done.add(`${entry.kind}\u0000${entry.name}`);
    } catch (error) {
      failed.push(`keycloak user ${entry.name}: ${String(error).slice(0, 200)}`);
    }
  }

  forget((entry) => done.has(`${entry.kind}\u0000${entry.name}`));
  console.log(
    `[teardown] other things this run made: ${done.size} removed or put back` +
      (failed.length ? `, ${failed.length} left for the next run: ${failed.join('; ')}` : ''),
  );
}

/**
 * Remove a pipeline-test run a test made. The API keeps every run on purpose — a run holds
 * verdicts people formed (`FRD-504` §5.7) — and one about a test's own use case is not that, so it
 * is removed where it lives, its answers first: the cascade Django declares is the ORM's, and the
 * database refuses the run while a result still names it.
 */
function removeRun(id: string): void {
  if (!/^\d+$/.test(id)) throw new Error(`not a run id: ${id}`);
  execFileSync(
    'docker',
    [
      'exec',
      container('postgres'),
      'psql',
      '-v',
      'ON_ERROR_STOP=1',
      '-U',
      setting('POSTGRES_USER'),
      '-d',
      'aira_mgmt',
      '-c',
      `DELETE FROM smoketests_testresult WHERE run_id = ${id}; ` +
        `DELETE FROM smoketests_testrun WHERE id = ${id};`,
    ],
    { stdio: 'pipe' },
  );
}
