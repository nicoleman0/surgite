// @vitest-environment jsdom
import { mount, tick, unmount } from 'svelte';
import type { ComponentProps } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import RepoList from './RepoList.svelte';
import type { Repo } from '$lib/api';

const { deleteRepoMock, setRepoConnectionMock } = vi.hoisted(() => ({
	deleteRepoMock: vi.fn(),
	setRepoConnectionMock: vi.fn()
}));

vi.mock('$lib/api', () => ({
	deleteRepo: deleteRepoMock,
	setRepoConnection: setRepoConnectionMock
}));

function repo(overrides: Partial<Repo> = {}): Repo {
	return {
		id: 42,
		name: 'surgite-demo',
		clone_url: 'https://example.test/surgite-demo.git',
		added_at: null,
		last_ingested_at: null,
		last_ingest_attempt_at: null,
		last_ingest_error: null,
		connection_id: null,
		...overrides
	};
}

function render(props: Partial<ComponentProps<typeof RepoList>> = {}) {
	const target = document.createElement('div');
	document.body.append(target);
	const onChanged = vi.fn();
	const onSync = vi.fn();
	const component = mount(RepoList, {
		target,
		props: {
			repos: [repo()],
			loading: false,
			error: null,
			onChanged,
			syncingIds: new Set<number>(),
			connections: [],
			onSync,
			...props
		}
	});
	return { target, component, onChanged, onSync };
}

async function click(button: Element | null) {
	expect(button).toBeInstanceOf(HTMLButtonElement);
	(button as HTMLButtonElement).click();
	await tick();
}

async function flush() {
	await new Promise((resolve) => setTimeout(resolve, 0));
	await tick();
}

// jsdom does not implement window.confirm -- it emits a jsdomError and returns
// undefined, which would read as "cancelled" and silently pass every test.
let confirmSpy: ReturnType<typeof vi.fn>;

beforeEach(() => {
	confirmSpy = vi.fn().mockReturnValue(true);
	vi.stubGlobal('confirm', confirmSpy);
});

afterEach(() => {
	vi.unstubAllGlobals();
	deleteRepoMock.mockReset();
	document.body.innerHTML = '';
});

describe('RepoList delete confirmation', () => {
	it('does not send a delete request when confirmation is cancelled', async () => {
		confirmSpy.mockReturnValue(false);
		const { target, component } = render();

		await click(target.querySelector('button[aria-label="Delete surgite-demo"]'));

		expect(confirmSpy).toHaveBeenCalledTimes(1);
		expect(confirmSpy.mock.calls[0][0]).toContain('surgite-demo');
		expect(deleteRepoMock).not.toHaveBeenCalled();
		expect(
			target.querySelector<HTMLButtonElement>('button[aria-label="Delete surgite-demo"]')?.disabled
		).toBe(false);
		unmount(component);
	});

	it('sends one delete request for the confirmed repository', async () => {
		deleteRepoMock.mockResolvedValueOnce(undefined);
		const { target, component, onChanged } = render({ repos: [repo({ id: 7, name: 'api-service' })] });

		await click(target.querySelector('button[aria-label="Delete api-service"]'));
		await flush();

		expect(confirmSpy.mock.calls[0][0]).toContain('api-service');
		expect(deleteRepoMock).toHaveBeenCalledTimes(1);
		expect(deleteRepoMock).toHaveBeenCalledWith(7);
		expect(onChanged).toHaveBeenCalledTimes(1);
		unmount(component);
	});

	it('keeps delete and sync controls disabled while deletion is pending', async () => {
		let resolveDelete: (value: void) => void = () => {};
		deleteRepoMock.mockReturnValueOnce(new Promise<void>((resolve) => (resolveDelete = resolve)));
		const { target, component } = render();

		await click(target.querySelector('button[aria-label="Delete surgite-demo"]'));

		expect(target.querySelector<HTMLButtonElement>('button[aria-label="Delete surgite-demo"]')?.disabled).toBe(true);
		expect(target.querySelector<HTMLButtonElement>('button[aria-label="Sync surgite-demo"]')?.disabled).toBe(true);

		resolveDelete();
		await flush();
		expect(target.querySelector<HTMLButtonElement>('button[aria-label="Delete surgite-demo"]')?.disabled).toBe(false);
		unmount(component);
	});

	it('restores usable controls after a delete error', async () => {
		deleteRepoMock.mockRejectedValueOnce(new Error('delete failed'));
		const { target, component, onChanged } = render();

		await click(target.querySelector('button[aria-label="Delete surgite-demo"]'));
		await flush();

		expect(onChanged).not.toHaveBeenCalled();
		expect(target.querySelector<HTMLButtonElement>('button[aria-label="Delete surgite-demo"]')?.disabled).toBe(false);
		expect(target.querySelector<HTMLButtonElement>('button[aria-label="Sync surgite-demo"]')?.disabled).toBe(false);
		unmount(component);
	});
});
