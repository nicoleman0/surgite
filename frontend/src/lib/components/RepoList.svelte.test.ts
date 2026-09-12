// @vitest-environment jsdom
import { mount, tick, unmount } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import RepoList from './RepoList.svelte';
import { deleteRepo } from '$lib/api';
import type { GitConnection, Repo } from '$lib/api';

vi.mock('$lib/api', async (importOriginal) => {
	const actual = await importOriginal<typeof import('$lib/api')>();
	return {
		...actual,
		deleteRepo: vi.fn(),
		setRepoConnection: vi.fn()
	};
});

const deleteRepoMock = vi.mocked(deleteRepo);

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

interface RepoListProps {
	repos: Repo[];
	loading: boolean;
	error: string | null;
	onChanged: () => void;
	syncingIds: Set<number>;
	connections: GitConnection[];
	onSync: (repos: Repo[]) => void;
}

function render(props: Partial<RepoListProps> = {}) {
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

afterEach(() => {
	deleteRepoMock.mockReset();
	document.body.innerHTML = '';
});

describe('RepoList delete confirmation', () => {
	it('does not send a delete request when confirmation is cancelled', async () => {
		const { target, component } = render();

		await click(target.querySelector('button[aria-label="Delete surgite-demo"]'));
		expect(target.querySelector('[role="dialog"]')?.textContent).toContain('surgite-demo');

		await click([...target.querySelectorAll('button')].find((button) => button.textContent?.trim() === 'Cancel') ?? null);

		expect(deleteRepoMock).not.toHaveBeenCalled();
		expect(target.querySelector('[role="dialog"]')).toBeNull();
		unmount(component);
	});

	it('sends one delete request for the confirmed repository', async () => {
		deleteRepoMock.mockResolvedValueOnce(undefined);
		const { target, component, onChanged } = render({ repos: [repo({ id: 7, name: 'api-service' })] });

		await click(target.querySelector('button[aria-label="Delete api-service"]'));
		await click([...target.querySelectorAll('button')].find((button) => button.textContent?.trim() === 'Delete repository') ?? null);
		await flush();

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
		await click([...target.querySelectorAll('button')].find((button) => button.textContent?.trim() === 'Delete repository') ?? null);

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
		await click([...target.querySelectorAll('button')].find((button) => button.textContent?.trim() === 'Delete repository') ?? null);
		await flush();

		expect(onChanged).not.toHaveBeenCalled();
		expect(target.querySelector<HTMLButtonElement>('button[aria-label="Delete surgite-demo"]')?.disabled).toBe(false);
		expect(target.querySelector<HTMLButtonElement>('button[aria-label="Sync surgite-demo"]')?.disabled).toBe(false);
		unmount(component);
	});
});
