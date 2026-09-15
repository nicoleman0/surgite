// @vitest-environment jsdom

import { mount, tick, unmount } from 'svelte';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import type { AuthMode } from '$lib/api';

const { fetchAdminUsers, createInvite } = vi.hoisted(() => ({
	fetchAdminUsers: vi.fn(),
	createInvite: vi.fn()
}));

vi.mock('$lib/api', () => ({
	fetchAdminUsers,
	createInvite,
	activateUser: vi.fn(),
	deactivateUser: vi.fn(),
	unlockUser: vi.fn(),
	logout: vi.fn()
}));
vi.mock('$app/navigation', () => ({ goto: vi.fn() }));

let Page: typeof import('./+page.svelte').default;
let page: ReturnType<typeof mount> | null = null;

beforeAll(async () => {
	vi.stubGlobal('localStorage', { getItem: vi.fn(() => null), setItem: vi.fn() });
	Page = (await import('./+page.svelte')).default;
});

afterEach(async () => {
	if (page) await unmount(page);
	page = null;
	document.body.innerHTML = '';
	fetchAdminUsers.mockReset();
	createInvite.mockReset();
});

afterAll(() => vi.unstubAllGlobals());

async function render(auth_mode: AuthMode | undefined) {
	fetchAdminUsers.mockResolvedValue({ total: 0, users: [] });
	page = mount(Page, {
		target: document.body,
		props: {
			data: {
				user: {
					id: '1',
					email: 'owner@example.test',
					display_name: 'Owner',
					is_admin: true,
					auth_mode
				}
			}
		}
	});
	await tick();
}

describe('admin invites', () => {
	it('offers the invite form in multi_user mode', async () => {
		await render('multi_user');
		expect(document.body.textContent).toContain('invite user');
		expect(document.body.textContent).not.toContain('AUTH_MODE=multi_user');
	});

	it.each(['off', 'single_user'] as const)(
		'explains the requirement instead of minting a dead link in %s mode',
		async (mode) => {
			await render(mode);
			expect(document.body.textContent).not.toContain('invite user');
			expect(document.body.textContent).toContain('AUTH_MODE=multi_user');
			expect(createInvite).not.toHaveBeenCalled();
		}
	);
});
