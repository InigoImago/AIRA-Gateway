import { expect, test } from '@playwright/test';
import { remember } from '../created';
import {
  USERS,
  api,
  createUseCase,
  grantGroup,
  login,
  releaseAllModels,
  uniqueSlug,
} from './support';

/** A laptop screen: the size at which a long answer and the window's foot compete for room. */
const LAPTOP = { width: 1366, height: 768 };
/** About sixty lines — a model's ordinary long answer, and more than the window's body shows. */
const ANSWER_LINES = 60;

/**
 * The rating window keeps its verdicts within reach (`FRD-504`).
 *
 * The three verdicts at the window's foot are what the window is for. The answer is the part that
 * grows, so the window's **body** is the one part that scrolls and its foot stays where it is;
 * otherwise a long answer pushes "Acceptable" below the screen, inside a fixed box that no amount
 * of scrolling brings into view. Only a browser at a real size can see that.
 *
 * The run and its answer are made here, through the API, and removed by the teardown: a run is
 * evidence the API keeps on purpose, and one about a test's own use case is not.
 */
test.describe('Rating a pipeline-test answer', () => {
  test('a long answer leaves the verdicts within reach on a laptop screen', async ({ page }) => {
    test.slow();
    await login(page, USERS.globalAdmin);
    const slug = await createUseCase(page, uniqueSlug('rating'), 'Rating window probe');
    // Starting a run needs a use case the starter both administers and can call, and a released
    // model to enter at (`may_run_tests_queryset`, `FRD-504` §5.8).
    await grantGroup(page, slug, '/aira/global-admins', 'admin');
    await releaseAllModels(page, slug);

    const run = await api<{ id: number }>(page, 'POST', '/api/v1/test-runs/', { use_case: slug });
    expect(run.status, JSON.stringify(run.body)).toBe(201);
    remember('run', String(run.body.id));

    const results = await api<{ id: number }[]>(
      page,
      'GET',
      `/api/v1/test-runs/${run.body.id}/results/`,
    );
    expect(results.status).toBe(200);
    expect(results.body.length, 'the run has no questions to answer').toBeGreaterThan(0);
    const answer = Array.from(
      { length: ANSWER_LINES },
      (_, index) => `Line ${index + 1} of an answer long enough to need its own scroll.`,
    ).join('\n');
    // One answer, so the window opens on it: a question the run never asked is skipped.
    const written = await api(page, 'PATCH', `/api/v1/test-results/${results.body[0].id}/`, {
      response: answer,
    });
    expect(written.status, JSON.stringify(written.body)).toBe(200);

    await page.setViewportSize(LAPTOP);
    await page.goto('/pipeline-tests');
    await page.getByTestId('tab-runs').click();
    const opener = page.getByTestId(`open-run-${run.body.id}`);
    await expect(opener).toBeVisible({ timeout: 20_000 });

    await opener.click();
    // Found as what it is, a dialog holding the answer, rather than by a test id only one way of
    // building it carries: the question here is where the verdicts are, whatever renders them.
    const window_ = page.getByRole('dialog').filter({ has: page.getByTestId('rate-response') });
    await expect(window_).toBeVisible({ timeout: 20_000 });
    await expect(window_.getByTestId('rate-response')).toContainText(`Line ${ANSWER_LINES} of`);

    for (const testid of ['rate-pass', 'rate-fail', 'rate-unclear']) {
      const verdict = window_.getByTestId(testid);
      await verdict.scrollIntoViewIfNeeded();
      const box = await verdict.boundingBox();
      expect(box, `${testid} has no box`).not.toBeNull();
      expect(box!.y, `${testid} starts above the screen`).toBeGreaterThanOrEqual(0);
      expect(box!.y + box!.height, `${testid} ends below the screen`).toBeLessThanOrEqual(
        LAPTOP.height,
      );
      expect(box!.x + box!.width, `${testid} ends past the screen`).toBeLessThanOrEqual(
        LAPTOP.width,
      );
    }
    // The window's body scrolled, not the page: a page that grew to fit the answer would put the
    // verdicts in reach only by scrolling the page out from under the window. Measured as the
    // window's own share of the page's height, with and without it in one evaluation, from one
    // layout: the page behind it goes on loading what it shows beside the runs, so a height read
    // before the window opened belongs to a page that has since grown on its own.
    const share = await window_.evaluate((dialog) => {
      const page = document.documentElement;
      const shown = dialog.style.display;
      const withWindow = page.scrollHeight;
      dialog.style.display = 'none';
      const withoutWindow = page.scrollHeight;
      dialog.style.display = shown;
      return withWindow - withoutWindow;
    });
    expect(share, 'the window made the page taller').toBeLessThanOrEqual(1);
  });
});
