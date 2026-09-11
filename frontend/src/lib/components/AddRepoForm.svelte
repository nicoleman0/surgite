<script lang="ts">
	import { addRepo, listGithubRepositories } from '$lib/api';
	import { toasts } from '$lib/toast.svelte';

	import type { GitConnection, Repo } from '$lib/api';

	let { onAdded, connections }: { onAdded: (repo: Repo) => void; connections: GitConnection[] } = $props();

	function focusOnMount(node: HTMLInputElement) {
		node.focus();
	}

	let open = $state(false);
	let input = $state('');
	let submitting = $state(false);
	let error = $state<string | null>(null);
	let connectionId = $state('');
	let githubRepos = $state<{ name: string; url: string }[]>([]);

	async function loadGithubRepos() {
		const connection = connections.find((item) => item.id === connectionId);
		if (!connection || connection.kind !== 'github') return;
		try {
			githubRepos = (await listGithubRepositories(connection.id)).repositories;
		} catch (cause) {
			error = cause instanceof Error ? cause.message : 'failed to load GitHub repositories';
		}
	}

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		if (!input.trim()) return;
		submitting = true;
		error = null;
		try {
			const repo = await addRepo(input.trim(), connectionId || null);
			submitting = false;
			input = '';
			open = false;
			toasts.success(`added ${repo.name}`);
			onAdded(repo);
		} catch (e2) {
			error = e2 instanceof Error ? e2.message : 'failed to add repo';
			submitting = false;
		}
	}

	function cancel() {
		open = false;
		error = null;
		input = '';
		submitting = false;
	}
</script>

<div class="mt-3">
	{#if open}
		<form onsubmit={submit} class="flex flex-col gap-2">
			<div class="flex items-center gap-2">
				<span class="text-accent" aria-hidden="true">❯</span>
			<input
					bind:value={input}
					use:focusOnMount
					placeholder="https://github.com/user/repo.git"
					class="flex-1 border border-border bg-bg px-2 py-1.5 text-sm text-fg placeholder:text-fg-faint"
				/>
				<select bind:value={connectionId} onchange={loadGithubRepos} class="border border-border bg-bg px-2 py-1.5 text-sm text-fg">
					<option value="">no saved connection</option>
					{#each connections.filter((item) => item.status === 'connected') as connection}
						<option value={connection.id}>{connection.name}</option>
					{/each}
				</select>
				<button
					type="submit"
					disabled={submitting}
					class="border border-border bg-surface px-3 py-1.5 text-sm text-fg transition hover:bg-surface-2 disabled:opacity-50"
				>
					{submitting ? 'adding…' : 'add-repo'}
				</button>
				<button type="button" onclick={cancel} class="px-2 py-1.5 text-sm text-fg-muted transition hover:text-fg">
					cancel
				</button>
			</div>
			{#if githubRepos.length}
				<select onchange={(event) => (input = (event.currentTarget as HTMLSelectElement).value)} class="border border-border bg-bg px-2 py-1.5 text-sm text-fg">
					<option value="">select a GitHub repository</option>
					{#each githubRepos as repo}<option value={repo.url}>{repo.name}</option>{/each}
				</select>
			{/if}
		</form>
		{#if error}
			<p class="mt-2 text-xs text-err">{error}</p>
		{/if}
	{:else}
		<button
			onclick={() => (open = true)}
			class="border border-border bg-surface px-3 py-1.5 text-sm text-fg transition hover:bg-surface-2"
		>
			<span class="text-accent">❯</span> add-repo
		</button>
	{/if}
</div>
