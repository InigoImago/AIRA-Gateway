import { CONSOLE_URL } from './stack';
import { defineConfig, devices } from '@playwright/test';

/**
 * The Windows sign-on (`FRD-140`, `make test-windows-sso`): people of a Windows domain reaching the
 * console and the gateway with the ticket they got at logon — and the people who must not.
 *
 * Its own config, because its precondition is a domain the rest of the suite must not have: the
 * global setup gives the development realm a federation with Kerberos allowed, and the teardown
 * takes it away again, passed or not. Every other spec runs against a realm of local accounts.
 */
const chrome = process.env.AIRA_E2E_CHROME;

export default defineConfig({
  testDir: './windows-sso',
  globalSetup: './windows-sso/setup.ts',
  globalTeardown: './windows-sso/teardown.ts',
  // The domain, the realm and the stack are shared state; one person's test at a time.
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  timeout: 90_000,
  expect: { timeout: 15_000 },
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
