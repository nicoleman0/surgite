import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
	testDir: './e2e',
	testMatch: 'auth.spec.ts',
	workers: 1,
	reporter: 'list',
	use: {
		...devices['Desktop Chrome'],
		baseURL: 'https://localhost:8443',
		ignoreHTTPSErrors: true,
		// Cover subresource certificate checks in Chromium as well as page requests.
		launchOptions: { args: ['--ignore-certificate-errors'] },
		trace: 'retain-on-failure',
		screenshot: 'only-on-failure'
	},
	webServer: {
		command: 'uv run --project .. python ../scripts/serve_auth_e2e.py',
		url: 'https://localhost:8443/login',
		ignoreHTTPSErrors: true,
		gracefulShutdown: { signal: 'SIGTERM', timeout: 5000 }
	}
});
