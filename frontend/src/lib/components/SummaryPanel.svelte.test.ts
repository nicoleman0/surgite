// @vitest-environment jsdom
import { mount, tick, unmount } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';

import SummaryPanel from './SummaryPanel.svelte';
import type { ProvidersResponse, Repo } from '$lib/api';

const { fetchProvidersMock, streamSummaryMock } = vi.hoisted(() => ({
	fetchProvidersMock: vi.fn(),
	streamSummaryMock: vi.fn()
}));

vi.mock('$lib/api', () => ({
	createShare: vi.fn(),
	fetchProviders: fetchProvidersMock,
	generateSummary: vi.fn(),
	streamSummary: streamSummaryMock
}));

const repo: Repo = {
	id: 1,
	name: 'surgite-demo',
	clone_url: 'https://example.test/surgite-demo.git',
	added_at: null,
	last_ingested_at: null,
	last_ingest_attempt_at: null,
	last_ingest_error: null,
	connection_id: null
};

function catalogue(defaultName: string, available: Record<string, boolean>): ProvidersResponse {
	return {
		default: defaultName,
		providers: Object.entries(available).map(([name, isAvailable]) => ({
			name,
			model: `${name}-model`,
			available: isAvailable,
			default: name === defaultName
		}))
	};
}

async function render() {
	const target = document.createElement('div');
	document.body.append(target);
	const component = mount(SummaryPanel, {
		target,
		props: {
			repos: [repo],
			staleAfterSeconds: null,
			syncingIds: new Set<number>(),
			onSync: vi.fn(),
			onEditPrompt: vi.fn()
		}
	});
	await flush();
	return {
		target,
		component,
		select: () => target.querySelector('select[aria-label="AI provider"]') as HTMLSelectElement | null,
		checkbox: () => target.querySelector('input[type="checkbox"]') as HTMLInputElement,
		generate: async () => {
			(Array.from(target.querySelectorAll('button')).find((b) => b.textContent?.includes('generate')) as HTMLButtonElement).click();
			await flush();
		}
	};
}

async function flush() {
	await new Promise((resolve) => setTimeout(resolve, 0));
	await tick();
}

afterEach(() => {
	fetchProvidersMock.mockReset();
	streamSummaryMock.mockReset();
	document.body.innerHTML = '';
});

describe('SummaryPanel provider selection', () => {
	it('keeps the default provider selected when it has a key', async () => {
		fetchProvidersMock.mockResolvedValue(catalogue('openai', { anthropic: true, openai: true }));
		const { select, component } = await render();

		expect(select()?.value).toBe('openai');
		unmount(component);
	});

	it('falls back to the first usable provider when the default has no key', async () => {
		fetchProvidersMock.mockResolvedValue(catalogue('anthropic', { anthropic: false, openai: true }));
		const { select, generate, component } = await render();

		expect(select()?.value).toBe('openai');
		expect(select()?.querySelector<HTMLOptionElement>('option[value="anthropic"]')?.disabled).toBe(true);
		await generate();

		expect(streamSummaryMock).toHaveBeenCalledTimes(1);
		expect(streamSummaryMock.mock.calls[0][0].provider).toBe('openai');
		unmount(component);
	});

	it('turns AI off with an explanation when no provider has a key', async () => {
		fetchProvidersMock.mockResolvedValue(catalogue('anthropic', { anthropic: false, openai: false }));
		const { target, select, checkbox, component } = await render();

		expect(checkbox().disabled).toBe(true);
		expect(checkbox().checked).toBe(false);
		expect(select()).toBeNull();
		expect(target.textContent).toContain('AI summaries need a provider API key');
		unmount(component);
	});

	it('leaves provider choice to the server when the catalogue is unavailable', async () => {
		fetchProvidersMock.mockRejectedValue(new Error('Admin only'));
		const { target, select, checkbox, component } = await render();

		expect(select()).toBeNull();
		expect(checkbox().disabled).toBe(false);
		expect(checkbox().checked).toBe(true);
		expect(target.textContent).not.toContain('AI summaries need a provider API key');
		unmount(component);
	});
});
