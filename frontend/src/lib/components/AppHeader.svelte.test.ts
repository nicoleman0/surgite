// @vitest-environment jsdom
import { mount, unmount } from 'svelte';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

vi.mock('$lib/api', () => ({ logout: vi.fn() }));
vi.mock('$app/navigation', () => ({ goto: vi.fn() }));

let AppHeader: typeof import('./AppHeader.svelte').default;

beforeAll(async () => {
	vi.stubGlobal('localStorage', { getItem: vi.fn(() => null), setItem: vi.fn() });
	AppHeader = (await import('./AppHeader.svelte')).default;
});

afterEach(() => {
	document.body.innerHTML = '';
});

afterAll(() => vi.unstubAllGlobals());

describe('AppHeader mobile navigation', () => {
	it('exposes every signed-in destination and marks the current route', () => {
		const target = document.createElement('div');
		document.body.append(target);
		const component = mount(AppHeader, {
			target,
			props: {
				currentPath: '/settings',
				user: {
					id: 'u1',
					email: 'admin@example.test',
					display_name: 'Admin',
					is_admin: true
				}
			}
		});
		const nav = target.querySelector('nav[aria-label="Mobile navigation"]');
		const links = [...(nav?.querySelectorAll('a') ?? [])];

		expect(links.map((link) => link.getAttribute('href'))).toEqual([
			'/',
			'/repositories',
			'/settings',
			'/summaries',
			'/admin'
		]);
		expect(nav?.querySelector('a[aria-current="page"]')?.getAttribute('href')).toBe('/settings');
		expect(nav?.className).toContain('sm:hidden');
		unmount(component);
	});

	it('does not expose administration to a regular user', () => {
		const target = document.createElement('div');
		document.body.append(target);
		const component = mount(AppHeader, {
			target,
			props: {
				currentPath: '/',
				user: {
					id: 'u2',
					email: 'user@example.test',
					display_name: 'User',
					is_admin: false
				}
			}
		});

		expect(target.querySelector('nav[aria-label="Mobile navigation"] a[href="/admin"]')).toBeNull();
		unmount(component);
	});
});
