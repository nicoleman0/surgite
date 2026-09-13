import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { WorkspaceState } from './workspace.svelte';

const response = (body: unknown) =>
	({ ok: true, status: 200, statusText: 'OK', json: () => Promise.resolve(body) }) as Response;

describe('WorkspaceState', () => {
	let fetchSpy: ReturnType<typeof vi.fn>;

	beforeEach(() => {
		fetchSpy = vi.fn((url: string) => {
			if (url.endsWith('/repos')) {
				return Promise.resolve(
					response({
						repos: [
							{
								id: 1,
								name: 'surgite',
								clone_url: 'https://example.test/surgite.git',
								added_at: null,
								last_ingested_at: null,
								last_ingest_attempt_at: null,
								last_ingest_error: null,
								connection_id: null
							}
						],
						stale_after_seconds: 600
					})
				);
			}
			return Promise.resolve(
				response([
					{
						id: 'c1',
						name: 'GitHub',
						kind: 'github',
						host: 'github.com',
						status: 'connected',
						created_at: null,
						updated_at: null,
						affected_repositories: 1
					}
				])
			);
		});
		vi.stubGlobal('fetch', fetchSpy);
	});

	afterEach(() => {
		vi.unstubAllGlobals();
	});

	it('loads repositories and connections once for the authenticated layout', async () => {
		const state = new WorkspaceState();

		await state.load();

		expect(fetchSpy).toHaveBeenCalledTimes(2);
		expect(state.repos.map((repo) => repo.name)).toEqual(['surgite']);
		expect(state.staleAfterSeconds).toBe(600);
		expect(state.connections.map((connection) => connection.name)).toEqual(['GitHub']);
		expect(state.reposLoading).toBe(false);
		expect(state.connectionsLoading).toBe(false);
		state.destroy();
	});

	it('refreshes connections explicitly after a mutation', async () => {
		const state = new WorkspaceState();
		await state.load();

		await state.reloadConnections();

		expect(fetchSpy.mock.calls.filter(([url]) => String(url).endsWith('/connections'))).toHaveLength(2);
		state.destroy();
	});

	it('keeps repository and connection failures independent', async () => {
		fetchSpy.mockImplementation((url: string) => {
			if (url.endsWith('/repos')) return Promise.reject(new Error('repos unavailable'));
			return Promise.resolve(response([]));
		});
		const state = new WorkspaceState();

		await state.load();

		expect(state.reposError).toBe('repos unavailable');
		expect(state.connectionsError).toBeNull();
		expect(state.connections).toEqual([]);
		state.destroy();
	});
});
