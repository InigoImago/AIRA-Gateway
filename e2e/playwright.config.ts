import { CONSOLE_URL } from './stack';
import { defineConfig, devices } from '@playwright/test';

/**
 * Browser-driven end-to-end tests for the whole AIRA stack.
 *
 * These are the checks that unit tests structurally cannot make: that a real browser can
 * complete the Keycloak code flow, that the SPA does not overflow its viewport at real widths,
 * and that the gateway accepts the very token the SPA holds. They need the live stack and the
 * three services running — see `e2e/README.md` (or `make e2e`).
 *
 * `AIRA_E2E_CHROME` points Playwright at an existing Chrome/Chromium binary. Leave it unset to
 * use Playwright's own download (`npx playwright install chromium`); set it in environments
 * where that download is unavailable.
 */
const chrome = process.env.AIRA_E2E_CHROME;

export default defineConfig({
  testDir: './tests',
  // Runs once, in its own process, after every project. See `teardown.ts` for why the suite has
  // to remove what it creates and why it may only remove *that*.
  globalTeardown: './teardown.ts',
  // The suite drives shared, stateful services; running files in parallel would make the
  // use-case fixtures collide.
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : [['list']],
  use: {
    baseURL: CONSOLE_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        launchOptions: chrome ? { executablePath: chrome } : {},
      },
    },
  ],
});
