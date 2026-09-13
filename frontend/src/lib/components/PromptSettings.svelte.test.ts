// @vitest-environment jsdom
import { mount, tick, unmount } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import PromptSettings from './PromptSettings.svelte';

const response = (body: unknown) =>
	({ ok: true, status: 200, statusText: 'OK', json: () => Promise.resolve(body) }) as Response;

async function flush() {
	await new Promise((resolve) => setTimeout(resolve, 0));
	await tick();
}

describe('PromptSettings route scope', () => {
	let fetchSpy: ReturnType<typeof vi.fn>;

	beforeEach(() => {
		fetchSpy = vi.fn(() =>
			Promise.resolve(
				response({
					repo_id: 3,
					user_name: '',
					user_role: '',
					tone: 'neutral',
					group_count: '2-5',
					output_format: 'markdown',
					custom_instructions: ''
				})
			)
		);
		vi.stubGlobal('fetch', fetchSpy);
	});

	afterEach(() => {
		vi.unstubAllGlobals();
		document.body.innerHTML = '';
	});

	it('loads the initial repository selected by the route', async () => {
		const target = document.createElement('div');
		document.body.append(target);
		const component = mount(PromptSettings, {
			target,
			props: {
				repos: [
					{
						id: 3,
						name: 'api',
						clone_url: 'https://example.test/api.git',
						added_at: null,
						last_ingested_at: null,
						last_ingest_attempt_at: null,
						last_ingest_error: null
					}
				],
				initialRepoId: 3
			}
		});

		await flush();

		expect(fetchSpy).toHaveBeenCalledWith(
			expect.stringContaining('/settings/prompt?repo_id=3'),
			expect.anything()
		);
		expect(target.querySelector<HTMLSelectElement>('#ps-scope')?.value).toBe('3');
		unmount(component);
	});
});
