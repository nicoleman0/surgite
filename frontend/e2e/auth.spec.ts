import { expect, test, type Page } from '@playwright/test';

const userPassword = 'invited-user-password';

async function login(page: Page, email: string, password: string) {
	await page.goto('/login');
	await page.getByLabel('email').fill(email);
	await page.getByLabel('password').fill(password);
	await page.getByRole('button', { name: /login$/ }).click();
	await expect(page).toHaveURL('/');
	// Only the header for the active viewport should be visible.
	await expect(page.getByRole('button', { name: 'Log out' })).toHaveCount(1);
	await expect(page.getByRole('button', { name: 'Log out' })).toBeVisible();
}

for (const pinned of [true, false]) {
	test(`${pinned ? 'email-pinned' : 'open'} invite creates a usable account in a separate browser`, async ({ page: admin, browser, baseURL }, testInfo) => {
		const email = `${pinned ? 'pinned' : 'open'}-${testInfo.repeatEachIndex}@example.test`;
		await login(admin, 'admin@example.test', 'admin-e2e-password');
		await admin.getByRole('link', { name: 'admin', exact: true }).click();
		await admin.getByRole('button', { name: 'invite user' }).click();
		if (pinned) await admin.getByLabel('email').fill(email);
		await admin.getByRole('button', { name: 'mint invite' }).click();
		const link = admin.locator('code').filter({ hasText: '/signup?token=' });
		await expect(link).toBeVisible();
		const inviteUrl = (await link.innerText()).trim();
		expect(new URL(inviteUrl).origin).toBe(baseURL);

		const context = await browser.newContext({ baseURL, ignoreHTTPSErrors: true });
		try {
			const user = await context.newPage();
			expect((await context.request.get('/auth/me')).status()).toBe(401);
			await user.goto(inviteUrl);
			await user.getByLabel('password').fill(userPassword);
			if (!pinned) await user.getByLabel('email').fill(email);
			await user.getByLabel('display name').fill('Invited colleague');
			const redeemed = user.waitForResponse((response) =>
				response.url().endsWith('/auth/redeem-invite') && response.request().method() === 'POST'
			);
			await user.getByRole('button', { name: /signup$/ }).click();
			expect((await redeemed).status()).toBe(201);
			await expect(user).toHaveURL('/');
			await expect(user.getByRole('button', { name: 'Log out' })).toHaveCount(1);
			await expect(user.getByText('Invited colleague').filter({ visible: true })).toBeVisible();
			await expect(user.getByRole('link', { name: 'admin', exact: true })).toHaveCount(0);
			const me = await context.request.get('/auth/me');
			expect(me.status()).toBe(200);
			expect(await me.json()).toMatchObject({ email, is_admin: false, auth_mode: 'multi_user' });
			expect((await context.cookies()).find((cookie) => cookie.name === '__Host-surgite_session'))
				.toMatchObject({ secure: true, httpOnly: true, sameSite: 'Lax', path: '/' });

			// Reload needs the saved cookie, rather than just client-side signup state.
			await user.reload();
			await expect(user.getByRole('button', { name: 'Log out' })).toBeVisible();
			expect((await context.request.get('/admin/users')).status()).toBe(403);
			expect((await context.request.post('/admin/invites', {
				headers: { 'X-Requested-With': 'surgite-web' }, data: {}
			})).status()).toBe(403);
			await user.getByRole('link', { name: 'Settings', exact: true }).click();
			await expect(user).toHaveURL('/settings');
			expect((await context.request.get('/settings/provider-keys')).status()).toBe(200);

			await user.getByRole('button', { name: 'Log out' }).click();
			await expect(user).toHaveURL('/login');
			expect((await context.request.get('/auth/me')).status()).toBe(401);
			await user.goto(inviteUrl);
			await user.getByLabel('password').fill(userPassword);
			if (!pinned) await user.getByLabel('email').fill(email);
			await user.getByRole('button', { name: /signup$/ }).click();
			await expect(user.getByText('Invalid or expired invite')).toBeVisible();
			await login(user, email, userPassword);
			expect(await (await context.request.get('/auth/me')).json()).toMatchObject({ email });

			await admin.reload();
			await expect(admin.getByRole('cell', { name: email, exact: true })).toBeVisible();
			expect(await (await admin.request.get('/auth/me')).json()).toMatchObject({
				email: 'admin@example.test', is_admin: true
			});
		} finally {
			await context.close();
		}
	});
}
