<script lang="ts">
	import { onDestroy } from 'svelte';
	import { deleteRepo, ingestRepo, listRepos, type Repo } from '$lib/api';
	import { relativeTime } from '$lib/time';
	import { toasts } from '$lib/toast.svelte';
	import Skeleton from './Skeleton.svelte';

	let {
		repos,
		loading,
		error,
		onChanged
	}: {
		repos: Repo[];
		loading: boolean;
		error: string | null;
		onChanged: () => void;
	} = $props();

	let deletingId = $state<number | null>(null);
	let syncingIds = $state(new Set<number>());
	const pollControllers = new Map<number, AbortController>();

	function setSyncing(id: number, syncing: boolean) {
		const next = new Set(syncingIds);
		if (syncing) next.add(id);
		else next.delete(id);
		syncingIds = next;
	}

	function delay(ms: number, signal: AbortSignal) {
		return new Promise<void>((resolve) => {
			const finish = () => {
				clearTimeout(timer);
				signal.removeEventListener('abort', finish);
				resolve();
			};
			const timer = setTimeout(finish, ms);
			signal.addEventListener('abort', finish, { once: true });
		});
	}

	async function handleSync(repo: Repo) {
		const controller = new AbortController();
		pollControllers.set(repo.id, controller);
		setSyncing(repo.id, true);
		try {
			try {
				await ingestRepo(repo.id);
			} catch (e) {
				if ((e as Error & { status?: number }).status !== 409) throw e;
			}

			const deadline = Date.now() + 120_000;
			while (!controller.signal.aborted && Date.now() < deadline) {
				await delay(2_000, controller.signal);
				if (controller.signal.aborted) return;
				const current = (await listRepos(controller.signal)).find((item) => item.id === repo.id);
				if (!current || current.last_ingest_attempt_at !== repo.last_ingest_attempt_at) {
					await onChanged();
					if (current?.last_ingest_error) toasts.error(`${repo.name}: sync failed`);
					else toasts.success(`synced ${repo.name}`);
					return;
				}
			}
			if (!controller.signal.aborted) {
				toasts.error(`${repo.name}: sync continues in the background`);
			}
		} catch (e) {
			if (!controller.signal.aborted) {
				toasts.error(e instanceof Error ? e.message : 'sync failed');
			}
		} finally {
			pollControllers.delete(repo.id);
			setSyncing(repo.id, false);
		}
	}

	onDestroy(() => {
		for (const controller of pollControllers.values()) controller.abort();
		pollControllers.clear();
	});

	async function handleDelete(id: number) {
		deletingId = id;
		const name = repos.find((r) => r.id === id)?.name ?? 'repository';
		try {
			await deleteRepo(id);
			toasts.success(`removed ${name}`);
			onChanged();
		} catch (e) {
			toasts.error(e instanceof Error ? e.message : 'delete failed');
		} finally {
			deletingId = null;
		}
	}
</script>

<div class="mt-3 border border-border bg-bg">
	{#if loading}
		<Skeleton rows={3} />
	{:else if error}
		<p class="px-4 py-6 text-sm text-err">{error}</p>
	{:else if repos.length === 0}
		<p class="px-4 py-6 text-sm text-fg-muted">No repos registered yet.</p>
	{:else}
		<div class="flex items-center justify-between border-b border-border-subtle px-4 py-2">
			<span class="text-xs text-fg-muted">{repos.length} repo{repos.length !== 1 ? 's' : ''}</span>
		</div>
		<ul class="divide-y divide-border-subtle">
			{#each repos as repo (repo.id)}
				<li class="flex items-center justify-between gap-4 px-4 py-3">
					<div class="min-w-0">
						<p class="truncate text-fg">{repo.name}</p>
						<p class="flex items-center gap-1 text-xs text-fg-muted">
							<span aria-hidden="true">↗</span>
							<span class="truncate">{repo.clone_url}</span>
						</p>
						{#if syncingIds.has(repo.id)}
							<p class="mt-1 text-xs text-accent">syncing…</p>
						{:else if repo.last_ingest_error}
							<p class="mt-1 text-xs text-err">
								sync failed {relativeTime(repo.last_ingest_attempt_at)} — {repo.last_ingest_error}
							</p>
							{#if repo.last_ingested_at}
								<p class="text-xs text-fg-faint">
									last successful sync {relativeTime(repo.last_ingested_at)}
								</p>
							{/if}
						{:else if repo.last_ingested_at}
							<p class="mt-1 text-xs text-fg-faint">
								synced {relativeTime(repo.last_ingested_at)}
							</p>
						{:else}
							<p class="mt-1 text-xs text-fg-faint">never synced</p>
						{/if}
					</div>
					<div class="flex shrink-0 items-center gap-3">
						<button
							onclick={() => handleSync(repo)}
							disabled={syncingIds.has(repo.id) || deletingId === repo.id}
							class="text-base text-fg-faint transition hover:text-accent disabled:opacity-50"
							aria-label="Sync {repo.name}"
						>
							↻
						</button>
						<button
							onclick={() => handleDelete(repo.id)}
							disabled={deletingId === repo.id || syncingIds.has(repo.id)}
							class="text-sm text-fg-faint transition hover:text-err disabled:opacity-50"
							aria-label="Delete {repo.name}"
						>
							✕
						</button>
					</div>
				</li>
			{/each}
		</ul>
	{/if}
</div>
