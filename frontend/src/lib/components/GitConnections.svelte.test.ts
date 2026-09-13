// @vitest-environment jsdom
import { mount, unmount } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';

import GitConnections from './GitConnections.svelte';
import type { WorkspaceState } from '$lib/workspace.svelte';

vi.mock('$lib/api', () => ({
	createTokenConnection: vi.fn(),
	disconnectConnection: vi.fn(),
	githubConnectionStart: vi.fn()
}));

afterEach(() => {
	document.body.innerHTML = '';
});

describe('GitConnections shared state', () => {
	it('renders connections already loaded by the authenticated layout', () => {
		const workspace = {
			connections: [
				{
					id: 'c1',
					name: 'GitHub',
					kind: 'github',
					host: 'github.com',
					status: 'connected',
					created_at: null,
					updated_at: null,
					affected_repositories: 2
				}
			],
			connectionsError: null,
			reloadConnections: vi.fn()
		} as unknown as WorkspaceState;
		const target = document.createElement('div');
		document.body.append(target);
		const component = mount(GitConnections, { target, props: { workspace } });

		expect(target.textContent).toContain('GitHub');
		expect(target.textContent).toContain('2 repos');
		unmount(component);
	});
});
