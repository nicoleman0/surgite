<script lang="ts">
	import { deleteRepo, setRepoConnection, type GitConnection, type Repo } from '$lib/api';
	import { relativeTime } from '$lib/time';
	import { toasts } from '$lib/toast.svelte';
	import Skeleton from './Skeleton.svelte';

	let {
		repos,
		loading,
		error,
		onChanged,
		syncingIds,
		connections,
		onSync
	}: {
		repos: Repo[];
		loading: boolean;
		error: string | null;
		onChanged: () => void;
		syncingIds: Set<number>;
		connections: GitConnection[];
		onSync: (repos: Repo[]) => void;
	} = $props();

	let deletingId = $state<number | null>(null);
	let confirmingRepo = $state<Repo | null>(null);
	async function updateConnection(repo: Repo, event: Event) {
		try {
			await setRepoConnection(repo.id, (event.currentTarget as HTMLSelectElement).value || null);
			onChanged();
		} catch (cause) { toasts.error(cause instanceof Error ? cause.message : 'failed to update connection'); }
	}

	async function handleDelete(repo: Repo) {
		deletingId = repo.id;
		confirmingRepo = null;
		try {
			await deleteRepo(repo.id);
			toasts.success(`removed ${repo.name}`);
			onChanged();
		} catch (e) {
			toasts.error(e instanceof Error ? e.message : 'delete failed');
		} finally {
			deletingId = null;
		}
	}

	function confirmDelete() {
		if (confirmingRepo) void handleDelete(confirmingRepo);
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
						<select value={repo.connection_id ?? ''} onchange={(event) => updateConnection(repo, event)} class="mt-1 border border-border bg-bg px-1 py-0.5 text-xs text-fg-muted">
							<option value="">no saved connection</option>
							{#each connections as connection}<option value={connection.id} disabled={connection.status !== 'connected'}>{connection.name} ({connection.status})</option>{/each}
						</select>
						{#if repo.connection_id && connections.find((connection) => connection.id === repo.connection_id)?.status === 'disconnected'}
							<p class="mt-1 text-xs text-err">connection required</p>
						{/if}
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
							onclick={() => onSync([repo])}
							disabled={syncingIds.has(repo.id) || deletingId === repo.id}
							class="text-base text-fg-faint transition hover:text-accent disabled:opacity-50"
							aria-label="Sync {repo.name}"
						>
							↻
						</button>
						<button
							onclick={() => (confirmingRepo = repo)}
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

{#if confirmingRepo}
	<div class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
		<div
			class="max-w-sm border border-border bg-bg p-4 shadow-lg"
			role="dialog"
			aria-modal="true"
			aria-labelledby="delete-repo-title"
			aria-describedby="delete-repo-description"
		>
			<h2 id="delete-repo-title" class="text-base font-semibold text-fg">
				Delete {confirmingRepo.name}?
			</h2>
			<p id="delete-repo-description" class="mt-2 text-sm text-fg-muted">
				This will remove {confirmingRepo.name} and its registered data from Surgite.
			</p>
			<div class="mt-4 flex justify-end gap-3">
				<button
					class="border border-border px-3 py-1 text-sm text-fg-muted transition hover:text-fg"
					onclick={() => (confirmingRepo = null)}
				>
					Cancel
				</button>
				<button
					class="border border-err px-3 py-1 text-sm text-err transition hover:bg-err hover:text-bg"
					onclick={confirmDelete}
				>
					Delete repository
				</button>
			</div>
		</div>
	</div>
{/if}
