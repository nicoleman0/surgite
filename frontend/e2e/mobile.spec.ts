import { expect, test, type Page, type Route } from '@playwright/test';

const now = '2026-09-13T12:00:00Z';
const long = 'very-long-segment-without-natural-breaks-'.repeat(5);

const user = {
	id: 'admin-1',
	email: `administrator+${long}@example.test`,
	display_name: `Administrator ${long}`,
	is_admin: true
};

const repos = [
	{
		id: 1,
		name: `api-${long}`,
		clone_url: `https://git.example.test/organisation/${long}/repository.git`,
		added_at: now,
		last_ingested_at: now,
		last_ingest_attempt_at: now,
		last_ingest_error: null,
		connection_id: 'connection-1'
	},
	{
		id: 2,
		name: `web-${long}`,
		clone_url: `https://git.example.test/organisation/${long}/frontend.git`,
		added_at: now,
		last_ingested_at: now,
		last_ingest_attempt_at: now,
		last_ingest_error: `Authentication failed: ${long}`,
		connection_id: null
	}
];

const connections = [
	{
		id: 'connection-1',
		name: `Production Git connection ${long}`,
		kind: 'github',
		host: `git-${long}.example.test`,
		status: 'connected',
		created_at: now,
		updated_at: now,
		affected_repositories: 1
	}
];

const summary = {
	period: { since: '2026-09-06', until: '2026-09-13' },
	total_commits: 5,
	by_repo: { [repos[0].name]: 3, [repos[1].name]: 2 },
	by_day: { '2026-09-12': 5 },
	source_synced_at: { [repos[0].name]: now, [repos[1].name]: now },
	commits: [],
	log_by_repo: {
		[repos[0].name]: `[2026-09-12] ${long} (Alice) <abcdef1>`,
		[repos[1].name]: `[2026-09-12] ${long} (Bob) <abcdef2>`
	},
	ai_summary: null,
	ai_provider: null,
	ai_model: null,
	ai_summaries: null
};

const promptSettings = (repoId: number | null) => ({
	repo_id: repoId,
	user_name: 'Alice',
	user_role: `Platform engineer ${long}`,
	tone: 'neutral',
	group_count: '2-5',
	output_format: 'markdown',
	custom_instructions: `Preserve links such as https://example.test/${long}`,
	updated_at: now
});

async function json(route: Route, body: unknown, status = 200) {
	await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
}

async function mockApi(page: Page) {
	await page.addInitScript(() => {
		Object.defineProperty(navigator, 'clipboard', {
			configurable: true,
			value: { writeText: async () => undefined }
		});
	});

	await page.route('**/*', async (route) => {
		const request = route.request();
		const url = new URL(request.url());
		const { pathname } = url;
		const method = request.method();

		if (pathname === '/auth/me') return json(route, user);
		if (pathname === '/auth/login' || pathname === '/auth/redeem-invite') return json(route, user);
		if (pathname === '/auth/logout' || pathname === '/auth/password-reset/confirm') {
			return route.fulfill({ status: 204 });
		}
		if (pathname === '/repos' && method === 'GET') {
			return json(route, { repos, stale_after_seconds: 900 });
		}
		if (pathname === '/repos' && method === 'POST') return json(route, { ...repos[0], id: 3 });
		if (/^\/repos\/\d+$/.test(pathname) && method === 'DELETE') return route.fulfill({ status: 204 });
		if (/^\/repos\/\d+\/ingest$/.test(pathname)) return json(route, { accepted: true });
		if (/^\/repos\/\d+\/connection$/.test(pathname)) return json(route, repos[0]);
		if (pathname === '/connections' && method === 'GET') return json(route, connections);
		if (pathname === '/connections' && method === 'POST') return json(route, connections[0]);
		if (/^\/connections\/[^/]+$/.test(pathname) && method === 'DELETE') {
			return route.fulfill({ status: 204 });
		}
		if (/^\/connections\/[^/]+\/repositories$/.test(pathname)) {
			return json(route, {
				repositories: [{ name: `repo-${long}`, url: `https://github.com/example/${long}.git` }]
			});
		}
		if (pathname === '/providers') {
			return json(route, {
				default: `provider-${long}`,
				providers: [
					{ name: `provider-${long}`, model: `model-${long}`, available: true, default: true }
				]
			});
		}
		if (pathname === '/summary/stream') {
			const meta = {
				period: summary.period,
				total_commits: summary.total_commits,
				by_repo: summary.by_repo,
				by_day: summary.by_day,
				source_synced_at: summary.source_synced_at,
				repos: repos.map((repo) => repo.name),
				provider: `provider-${long}`,
				model: `model-${long}`
			};
			const body = [
				`event: meta\ndata: ${JSON.stringify(meta)}\n`,
				...repos.flatMap((repo) => [
					`event: delta\ndata: ${JSON.stringify({ repo: repo.name, text: `## Work\n\n${long} https://example.test/${long}` })}\n`,
					`event: repo_done\ndata: ${JSON.stringify({ repo: repo.name, provider: meta.provider, model: meta.model })}\n`
				]),
				'event: done\ndata: {}\n'
			].join('\n');
			return route.fulfill({ status: 200, contentType: 'text/event-stream', body });
		}
		if (pathname === '/summary') return json(route, summary);
		if (pathname === '/settings/prompt') {
			const repoId = url.searchParams.has('repo_id') ? Number(url.searchParams.get('repo_id')) : null;
			return json(route, promptSettings(repoId));
		}
		if (pathname === '/settings/provider-keys') {
			return json(route, {
				providers: [`provider-${long}`, 'groq'],
				default: `provider-${long}`,
				keys: [{ provider: 'groq', created_at: now, revoked_at: null }]
			});
		}
		if (pathname === '/summaries' && method === 'POST') {
			return json(route, { slug: 'example', expires_at: '2026-09-20T12:00:00Z' });
		}
		if (pathname === '/summaries/mine') {
			return json(route, {
				total: 1,
				summaries: [
					{
						slug: `example-${long}`,
						params: { repo: repos[0].name, since: '2026-09-06', ai: true },
						created_at: now,
						expires_at: '2026-09-20T12:00:00Z'
					}
				]
			});
		}
		if (pathname === '/summaries/example' || pathname.startsWith('/summaries/example-')) {
			return json(route, {
				slug: pathname.split('/').at(-1),
				params: { since: '2026-09-06', ai: false },
				created_at: now,
				expires_at: '2026-09-20T12:00:00Z'
			});
		}
		if (pathname === '/admin/users') {
			return json(route, {
				total: 2,
				users: [
					{
						...user,
						is_active: true,
						created_at: now,
						last_login_at: now,
						failed_login_count: 0,
						locked_until: null
					},
					{
						id: 'user-2',
						email: `person+${long}@example.test`,
						display_name: `Person ${long}`,
						is_admin: false,
						is_active: true,
						created_at: now,
						last_login_at: null,
						failed_login_count: 4,
						locked_until: '2099-09-13T12:00:00Z'
					}
				]
			});
		}
		if (pathname === '/admin/invites') {
			return json(route, {
				id: 'invite-1',
				token: long,
				email: null,
				role: 'user',
				expires_at: '2026-09-20T12:00:00Z'
			});
		}
		if (/^\/admin\/users\/[^/]+\/(unlock|deactivate|activate)$/.test(pathname)) {
			return route.fulfill({ status: 204 });
		}

		await route.continue();
	});
}

async function open(page: Page, path: string, width = 360) {
	await page.setViewportSize({ width, height: 800 });
	await mockApi(page);
	await page.goto(path);
}

async function expectNoPageOverflow(page: Page) {
	await expect
		.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
		.toBe(true);
}

test.describe('mobile routes', () => {
	test('summary generation and reader actions stay usable at 360px', async ({ page }) => {
		await open(page, '/');
		const mobileNav = page.getByRole('navigation', { name: 'Mobile navigation' });
		await expect(mobileNav.getByRole('link')).toHaveCount(5);
		await expect(mobileNav.getByRole('link', { name: 'Summary' })).toHaveAttribute(
			'aria-current',
			'page'
		);
		for (const link of await mobileNav.getByRole('link').all()) {
			expect((await link.boundingBox())?.height).toBeGreaterThanOrEqual(44);
		}
		await expectNoPageOverflow(page);
		await mobileNav.getByRole('link', { name: 'Repositories' }).click();
		await expect(page).toHaveURL(/\/repositories$/);
		await expect(
			page.getByRole('navigation', { name: 'Mobile navigation' }).getByRole('link', {
				name: 'Repositories'
			})
		).toHaveAttribute('aria-current', 'page');
		await page.goto('/');

		const range = page.locator('select').filter({ has: page.locator('option[value="custom"]') });
		await range.selectOption('custom');
		await page.getByLabel('From date').fill('2026-09-06');
		await page.getByLabel('To date').fill('2026-09-13');
		await expectNoPageOverflow(page);

		const generate = page.getByRole('button', { name: /generate/ });
		expect((await generate.boundingBox())?.height).toBeGreaterThanOrEqual(44);
		await generate.click();
		await expect(page.getByRole('heading', { name: 'summary results' })).toBeVisible();
		await expect(page.getByLabel('Repository summary')).toBeVisible();
		await expectNoPageOverflow(page);

		await page.getByRole('button', { name: 'copy markdown' }).click();
		await page.getByRole('button', { name: 'share link' }).click();
		await page.getByRole('button', { name: /sync$/ }).click();
		await page.getByRole('button', { name: 'copy', exact: true }).click();
		const download = page.waitForEvent('download');
		await page.getByRole('button', { name: /download/ }).click();
		await download;
		await page.getByRole('button', { name: /next/ }).click();
		await expect(page.getByRole('button', { name: /previous/ })).toBeEnabled();
		await page.getByRole('button', { name: /previous/ }).click();
		await page.getByRole('button', { name: 'edit prompt' }).click();
		await expect(page).toHaveURL(/\/settings\?repo_id=1$/);
	});

	test('repository creation, saved connections, sync, and confirmed deletion', async ({ page }) => {
		await open(page, '/repositories');
		// The key handler lives in the (app) layout, which mounts after hydration.
		await expect(page.getByRole('button', { name: 'add-repo' })).toBeVisible();
		const konami = [
			'ArrowUp',
			'ArrowUp',
			'ArrowDown',
			'ArrowDown',
			'ArrowLeft',
			'ArrowRight',
			'ArrowLeft',
			'ArrowRight',
			'b',
			'a'
		];
		for (const key of konami) await page.keyboard.press(key);
		await expect(page.locator('html')).toHaveClass(/crt/);
		await page.getByRole('button', { name: 'add-repo' }).click();
		await page.getByPlaceholder('https://github.com/user/repo.git').fill(
			`https://git.example.test/${long}.git`
		);
		const connection = page.locator('select').first();
		await expect(connection.getByRole('option', { name: connections[0].name })).toBeAttached();
		await connection.selectOption('connection-1');
		await expect(page.getByRole('option', { name: `repo-${long}` })).toBeAttached();
		await page.getByRole('button', { name: 'add-repo' }).click();
		await expect(page.getByRole('button', { name: 'add-repo' })).toBeVisible();

		await page.getByRole('button', { name: `Sync ${repos[0].name}` }).click();
		page.once('dialog', (dialog) => dialog.accept());
		await page.getByRole('button', { name: `Delete ${repos[1].name}` }).click();
		await expectNoPageOverflow(page);
	});

	test('repo-scoped settings, connection controls, and provider keys', async ({ page }) => {
		await open(page, '/settings?repo_id=1');
		await expect(page.locator('#ps-scope')).toHaveValue('1');
		await page.locator('#ps-scope').selectOption('');
		await expect(page.locator('#ps-scope')).toHaveValue('');
		await page.locator('#ps-scope').selectOption('1');
		await page.locator('#ps-name').fill('Alice Mobile');
		await page.getByRole('button', { name: /save$/ }).first().click();
		await expect(page.getByText(connections[0].name, { exact: true })).toBeVisible();

		await page.getByPlaceholder('connection name').fill('mobile connection');
		await page.getByPlaceholder('https://git.example.com').fill('https://git.example.test');
		await page.getByPlaceholder('username').fill('alice');
		await page.getByPlaceholder('access token').fill('secret');
		await page.getByRole('button', { name: 'save HTTPS connection' }).click();

		await page.getByRole('button', { name: 'add key' }).click();
		await page.getByPlaceholder('paste key').fill('provider-secret');
		await page.getByRole('button', { name: /save$/ }).last().click();
		await page.keyboard.press('F1');
		await expect(page.getByRole('dialog', { name: 'Help' })).toBeVisible();
		await expectNoPageOverflow(page);
		await page.keyboard.press('Escape');
		await expectNoPageOverflow(page);
	});

	test('saved summaries link to the shared reader', async ({ page }) => {
		await open(page, '/summaries');
		await expect(page.getByRole('link', { name: 'open' })).toHaveAttribute('href', /example-/);
		await expectNoPageOverflow(page);
		await page.getByRole('link', { name: 'open' }).click();
		await expect(page.getByRole('heading', { name: 'shared summary' })).toBeVisible();
	});

	test('admin invite and account actions stay inside the table region', async ({ page }) => {
		await open(page, '/admin');
		await page.getByRole('button', { name: 'invite user' }).click();
		await page.getByRole('button', { name: 'mint invite' }).click();
		await expect(page.getByText('invite created', { exact: true })).toBeVisible();
		await page.getByRole('button', { name: 'copy', exact: true }).click();
		await page.getByRole('button', { name: 'unlock' }).click();
		await page.getByRole('button', { name: 'deactivate' }).click();
		await expectNoPageOverflow(page);
		const tableRegion = page.locator('table').locator('..');
		expect(await tableRegion.evaluate((element) => element.scrollWidth > element.clientWidth)).toBe(true);
	});

	for (const path of ['/login', '/signup?token=x', '/password-reset?token=x']) {
		test(`${path} exposes usable form controls`, async ({ page }) => {
			await open(page, path);
			const passwords = page.getByLabel(/password/i);
			await passwords.first().fill('correct-horse-battery-staple');
			if ((await passwords.count()) > 1) await passwords.nth(1).fill('correct-horse-battery-staple');
			if (path.startsWith('/login')) await page.getByLabel('email').fill('user@example.test');
			await expectNoPageOverflow(page);
		});
	}

	test('public shared summary reader remains usable', async ({ page }) => {
		await open(page, '/s/example');
		await expect(page.getByLabel('Repository summary')).toBeVisible();
		await page.getByLabel('Repository summary').selectOption(repos[1].name);
		await page.getByRole('button', { name: 'copy', exact: true }).click();
		const download = page.waitForEvent('download');
		await page.getByRole('button', { name: /download/ }).click();
		await download;
		await page.getByRole('button', { name: /previous/ }).click();
		await page.getByRole('button', { name: /next/ }).click();
		await expectNoPageOverflow(page);
	});
});

test.describe('640px presentation boundary', () => {
	for (const width of [639, 640]) {
		test(`${width}px uses one coordinated breakpoint`, async ({ page }) => {
			await open(page, '/', width);
			await page.getByRole('button', { name: /generate/ }).click();
			await expect(page.getByRole('heading', { name: 'summary results' })).toBeVisible();
			const mobile = width < 640;
			await expect(page.getByRole('navigation', { name: 'Mobile navigation' })).toBeVisible({
				visible: mobile
			});
			await expect(page.getByRole('navigation', { name: 'Workspace' })).toBeVisible({
				visible: !mobile
			});
			await expect(page.getByLabel('Repository summary')).toBeVisible({ visible: mobile });
			await expect(page.getByRole('navigation', { name: 'Repository summaries' })).toBeVisible({
				visible: !mobile
			});
			await page.getByRole('button', { name: 'grid' }).click();
			const columns = await page
				.locator('[data-summary-reader] article')
				.first()
				.locator('..')
				.evaluate((element) => getComputedStyle(element).gridTemplateColumns.split(' ').length);
			expect(columns).toBe(mobile ? 1 : 2);
			await expect(page.getByTestId('status-bar')).toHaveCSS('position', mobile ? 'static' : 'fixed');
			await expectNoPageOverflow(page);
		});
	}

	test('the admin table remains one locally scrolling table across the boundary', async ({ page }) => {
		await open(page, '/admin', 639);
		await expect(page.locator('table')).toHaveCount(1);
		await expectNoPageOverflow(page);
		await page.setViewportSize({ width: 640, height: 800 });
		await expect(page.locator('table')).toHaveCount(1);
		await expectNoPageOverflow(page);
	});

	test('desktop layout remains dense at 1280px', async ({ page }) => {
		await open(page, '/', 1280);
		await page.getByRole('button', { name: /generate/ }).click();
		await expect(page.getByRole('navigation', { name: 'Workspace' })).toBeVisible();
		await expect(page.getByRole('navigation', { name: 'Repository summaries' })).toBeVisible();
		await expect(page.getByTestId('status-bar')).toHaveCSS('position', 'fixed');
		await expectNoPageOverflow(page);
	});
});
