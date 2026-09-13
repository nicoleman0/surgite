// @vitest-environment jsdom
import { mount, tick, unmount } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import UserBadge from './UserBadge.svelte';

const { fetchCurrentUserMock, logoutMock, gotoMock } = vi.hoisted(() => ({
	fetchCurrentUserMock: vi.fn(),
	logoutMock: vi.fn(),
	gotoMock: vi.fn()
}));

vi.mock('$lib/api', () => ({
	fetchCurrentUser: fetchCurrentUserMock,
	logout: logoutMock
}));
vi.mock('$app/navigation', () => ({ goto: gotoMock }));

async function flush() {
	await new Promise((resolve) => setTimeout(resolve, 0));
	await tick();
}

beforeEach(() => {
	fetchCurrentUserMock.mockReset();
	logoutMock.mockReset();
	gotoMock.mockReset();
});

afterEach(() => {
	document.body.innerHTML = '';
});

describe('UserBadge admin navigation', () => {
	it('uses the layout user without fetching it again', async () => {
		const user = {
			id: 'u1',
			email: 'admin@example.test',
			display_name: 'Admin',
			is_admin: true
		};
		const target = document.createElement('div');
		document.body.append(target);
		const component = mount(UserBadge, { target, props: { user } });

		await flush();

		expect(fetchCurrentUserMock).not.toHaveBeenCalled();
		expect(target.querySelector<HTMLAnchorElement>('a')?.getAttribute('href')).toBe('/admin');
		unmount(component);
	});

	it('does not show the admin link to regular users', async () => {
		const user = {
			id: 'u2',
			email: 'user@example.test',
			display_name: 'User',
			is_admin: false
		};
		const target = document.createElement('div');
		document.body.append(target);
		const component = mount(UserBadge, { target, props: { user } });

		await flush();

		expect(target.querySelector('a[href="/admin"]')).toBeNull();
		unmount(component);
	});
});
