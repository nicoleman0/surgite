// @vitest-environment jsdom

import { mount, tick, unmount } from 'svelte';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

const { signup } = vi.hoisted(() => ({ signup: vi.fn() }));

vi.mock('$lib/api', () => ({ signup }));
vi.mock('$app/navigation', () => ({ goto: vi.fn() }));
vi.mock('$app/state', () => ({
	page: { url: new URL('http://localhost/signup?token=invite-token') }
}));

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
	signup.mockReset();
});

afterAll(() => vi.unstubAllGlobals());

async function submitWithPassword() {
	page = mount(Page, { target: document.body });
	await tick();
	const input = document.querySelector<HTMLInputElement>('input[type="password"]');
	input!.value = 'brand-new-pass';
	input!.dispatchEvent(new Event('input', { bubbles: true }));
	await tick();
	document.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true }));
	await tick();
	await tick();
}

function failWith(status: number, message: string) {
	signup.mockRejectedValue(Object.assign(new Error(message), { status }));
}

describe('signup errors', () => {
	it('explains a 404 rather than relaying "Not found"', async () => {
		failWith(404, 'Not found');
		await submitWithPassword();
		expect(document.body.textContent).toContain('not accepting signups');
		expect(document.body.textContent).toContain('AUTH_MODE=multi_user');
	});

	it('still shows the server reason for a rejected invite', async () => {
		failWith(400, 'Invalid or expired invite');
		await submitWithPassword();
		expect(document.body.textContent).toContain('Invalid or expired invite');
	});
});
