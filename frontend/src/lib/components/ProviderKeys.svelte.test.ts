// @vitest-environment jsdom
import { mount, tick, unmount } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';

import ProviderKeys from './ProviderKeys.svelte';

const response = (body: unknown) =>
	({ ok: true, status: 200, statusText: 'OK', json: () => Promise.resolve(body) }) as Response;

describe('ProviderKeys route loading', () => {
	afterEach(() => {
		vi.unstubAllGlobals();
		document.body.innerHTML = '';
	});

	it('loads when the settings route mounts it', async () => {
		const fetchSpy = vi.fn(() =>
			Promise.resolve(
				response({ providers: ['anthropic'], default: 'anthropic', keys: [] })
			)
		);
		vi.stubGlobal('fetch', fetchSpy);
		const target = document.createElement('div');
		document.body.append(target);
		const component = mount(ProviderKeys, { target });

		await new Promise((resolve) => setTimeout(resolve, 0));
		await tick();

		expect(fetchSpy).toHaveBeenCalledOnce();
		expect(target.textContent).toContain('anthropic');
		unmount(component);
	});
});
