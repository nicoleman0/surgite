// @vitest-environment jsdom
import { mount, unmount } from 'svelte';
import { afterEach, describe, expect, it } from 'vitest';

import WorkspaceNav from './WorkspaceNav.svelte';

afterEach(() => {
	document.body.innerHTML = '';
});

describe('WorkspaceNav', () => {
	it('renders route links and marks the current workspace', () => {
		const target = document.createElement('div');
		document.body.append(target);
		const component = mount(WorkspaceNav, { target, props: { current: 'repositories' } });
		const links = [...target.querySelectorAll('a')];

		expect(links.map((link) => link.getAttribute('href'))).toEqual([
			'/',
			'/repositories',
			'/settings'
		]);
		expect(target.querySelector('a[aria-current="page"]')?.textContent).toContain('Repositories');
		expect(target.querySelector('nav')?.className).toContain('hidden');
		expect(target.querySelector('nav')?.className).toContain('sm:flex');
		unmount(component);
	});
});
