import { chromium } from '@playwright/test';
import { CONSOLE_URL } from './stack';
import { rememberedUseCases } from './created';
import { USERS, login } from './tests/support';

/**
 * Remove the use cases this suite created, and nothing else.
 *
 * ## The bill this pays
 *
 * 90 calls to `createUseCase` across the suite and not one removal. Measured on the demo database
 * after a few sessions: **1734 use cases in Management and 1946 in the gateway's read-model**, of
 * which four were the demo's. A global administrator opening that list learns nothing except that
 * the list is long, the register screen counts them all, and the seed's own comment already
 * records the same thing happening at 801.
 *
 * The project had learned this for **models** — `removeModel` exists with the sentence *"test
 * residue makes a real figure meaningless, and the residue never stops accumulating"* — and had
 * not learned it for the object the suite creates most.
 *
 * ## Only what this run made
 *
 * Every slug comes from `created.ts`, written at the moment of creation. Nothing is matched by
 * shape: a person may name a use case anything, and this demo holds two that a person made. That
 * is also why `seed_demo --fresh`, which deletes everything that is not one of the demo's own six,
 * is **not** what runs here.
 *
 * ## Through the API, with the console's own token
 *
 * The alternative was driving the UI once per use case, which at several hundred rows is minutes
 * of clicking. The token is read out of the page the way `grantGroup` does — the SPA holds it in
 * session storage — and the deletions go straight to Management, which announces each one so the
 * gateway's read-model follows.
 *
 * A `404` is success: a test that removed its own use case, or a run that never got as far as
 * creating one, must not turn tidying into a failure.
 */
export default async function teardown(): Promise<void> {
  const slugs = rememberedUseCases();
  if (slugs.length === 0) {
    return;
  }

  const browser = await chromium.launch({
    executablePath: process.env.AIRA_E2E_CHROME || undefined,
  });
  const page = await browser.newPage({ baseURL: CONSOLE_URL });
  try {
    await login(page, USERS.globalAdmin);
    const result = await page.evaluate(async (wanted: string[]) => {
      const token =
        sessionStorage.getItem('access_token') ?? localStorage.getItem('access_token') ?? '';
      let removed = 0;
      let absent = 0;
      const failed: string[] = [];
      for (const slug of wanted) {
        const response = await fetch(`/api/v1/use-cases/${encodeURIComponent(slug)}/`, {
          method: 'DELETE',
          headers: { Authorization: `Bearer ${token}` },
        });
        if (response.status === 404) absent += 1;
        else if (response.ok) removed += 1;
        else failed.push(`${slug}:${response.status}`);
      }
      return { removed, absent, failed };
    }, slugs);

    // Said out loud, because a clean-up that silently does nothing is indistinguishable from one
    // that is not running at all — the shape this repository keeps meeting as "a guard that cannot
    // fail". The count is the evidence that it did something.
    console.log(
      `[teardown] use cases: ${result.removed} removed, ${result.absent} already gone` +
        (result.failed.length
          ? `, ${result.failed.length} refused: ${result.failed.join(', ')}`
          : ''),
    );
    // **The register is left in place**, and that is the correction that cost a run to find.
    //
    // Retiring is only half of the clean-up: a tombstone is kept on purpose (`FRD-607`), and the
    // purge that removes it is a separate, demo-guarded step outside this process. Clearing the
    // file here meant that step had nothing to read — measured once, and it left 68 tombstones
    // that nothing could ever name again.
    //
    // So `purge_test_use_cases` consumes the register and the Makefile deletes it afterwards. A
    // purge that fails leaves it for the next run, which is the same rule one stage along.
  } finally {
    await browser.close();
  }
}
