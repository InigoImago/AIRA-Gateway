import { CONSOLE_URL } from './stack';
import { defineConfig, devices } from '@playwright/test';

/**
 * The UI audit (`make ui-audit`): every page, tab and window of the console, for every role, at a
 * desktop and a phone width — screenshots plus the checks in `audit/checks.ts`. Its own config so
 * `make test-e2e` never runs it: it asserts nothing, it reports (`ui-audit-report/report.md`).
 */
const chrome = process.env.AIRA_E2E_CHROME;

export default defineConfig({
  testDir: './audit',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  // One test per role walks the whole console.
  timeout: 20 * 60_000,
  expect: { timeout: 10_000 },
  reporter: [['list']],
  use: {
    baseURL: CONSOLE_URL,
    trace: 'off',
    // No step may wait for ever: an action or a screenshot that hangs is reported as a finding and
    // the walk moves on, rather than one stuck window costing the whole run.
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
    screenshot: 'off',
    video: 'off',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1440, height: 900 },
        launchOptions: chrome ? { executablePath: chrome } : {},
      },
    },
  ],
});
