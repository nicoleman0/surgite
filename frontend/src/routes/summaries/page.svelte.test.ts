// @vitest-environment jsdom

import { mount, tick, unmount } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import Page from './+page.svelte';

const { fetchMySummaries } = vi.hoisted(() => ({ fetchMySummaries: vi.fn() }));

vi.mock('$lib/api', () => ({ fetchMySummaries }));

let page: ReturnType<typeof mount>;

beforeEach(async () => {
	fetchMySummaries.mockResolvedValue({ summaries: [], total: 0 });
	page = mount(Page, { target: document.body });
	await tick();
});

afterEach(async () => {
	await unmount(page);
	fetchMySummaries.mockReset();
});

describe('summary ownership tabs', () => {
	it('renders linked tabs and panels with roving focus', () => {
		const tabs = [...document.querySelectorAll<HTMLButtonElement>('[role="tab"]')];
		const panels = [...document.querySelectorAll<HTMLElement>('[role="tabpanel"]')];

		expect(document.querySelector('[role="tablist"]')?.getAttribute('aria-label')).toBe(
			'Summary ownership'
		);
		expect(tabs.map((tab) => [tab.id, tab.getAttribute('aria-controls')])).toEqual([
			['mine-tab', 'mine-panel'],
			['shared-tab', 'shared-panel']
		]);
		expect(panels.map((panel) => [panel.id, panel.getAttribute('aria-labelledby')])).toEqual([
			['mine-panel', 'mine-tab'],
			['shared-panel', 'shared-tab']
		]);
		expect(tabs.map((tab) => [tab.getAttribute('aria-selected'), tab.tabIndex])).toEqual([
			['true', 0],
			['false', -1]
		]);
		expect(panels.map((panel) => panel.hidden)).toEqual([false, true]);
	});

	it('moves focus, selection, and panel visibility with the arrow keys', async () => {
		const mine = document.querySelector<HTMLButtonElement>('#mine-tab')!;
		const shared = document.querySelector<HTMLButtonElement>('#shared-tab')!;

		mine.focus();
		mine.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
		await tick();

		expect(document.activeElement).toBe(shared);
		expect([mine.getAttribute('aria-selected'), mine.tabIndex]).toEqual(['false', -1]);
		expect([shared.getAttribute('aria-selected'), shared.tabIndex]).toEqual(['true', 0]);
		expect(document.querySelector<HTMLElement>('#mine-panel')?.hidden).toBe(true);
		expect(document.querySelector<HTMLElement>('#shared-panel')?.hidden).toBe(false);
		expect(
			[...document.querySelectorAll<HTMLButtonElement>('[role="tab"]')].filter(
				(tab) => tab.tabIndex === 0
			)
		).toEqual([shared]);
	});
});
