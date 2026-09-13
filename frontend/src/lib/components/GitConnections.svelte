<script lang="ts">
	import { createTokenConnection, disconnectConnection, githubConnectionStart, type GitConnection } from '$lib/api';
	import { toasts } from '$lib/toast.svelte';
	import type { WorkspaceState } from '$lib/workspace.svelte';

	let { workspace }: { workspace: WorkspaceState } = $props();
	let error = $state<string | null>(null);
	let name = $state('');
	let origin = $state('https://');
	let username = $state('');
	let token = $state('');
	let busy = $state(false);

	const visibleError = $derived(error ?? workspace.connectionsError);

	async function save() {
		busy = true; error = null;
		try {
			await createTokenConnection({ name, origin, username, token });
			name = ''; origin = 'https://'; username = ''; token = '';
			await workspace.reloadConnections(); toasts.success('Git connection saved');
		} catch (cause) { error = cause instanceof Error ? cause.message : 'failed to save connection'; }
		finally { busy = false; }
	}
	async function disconnect(connection: GitConnection) {
		if (!window.confirm(`Disconnect ${connection.name}? Repositories will keep their history but cannot sync.`)) return;
		await disconnectConnection(connection.id); await workspace.reloadConnections();
	}
	async function connectGithub() {
		try { window.location.assign((await githubConnectionStart()).url); }
		catch (cause) { error = cause instanceof Error ? cause.message : 'GitHub is unavailable'; }
	}
</script>

<section>
	<h2 class="text-sm text-fg-muted"><span class="text-accent">~/git-connections</span> <span aria-hidden="true">❯</span></h2>
	<p class="mt-1 text-xs text-fg-faint">Credentials are encrypted and never shown again.</p>
	{#if visibleError}<p class="mt-2 text-xs text-err">{visibleError}</p>{/if}
	<ul class="mt-3 space-y-2">
		{#each workspace.connections as connection (connection.id)}
			<li class="flex items-center gap-2 border border-border px-3 py-2 text-sm"><span>{connection.name}</span><span class="text-xs text-fg-muted">{connection.kind} · {connection.host} · {connection.affected_repositories} repos</span><span class="ml-auto text-xs {connection.status === 'connected' ? 'text-accent' : 'text-err'}">{connection.status}</span><button onclick={() => disconnect(connection)} class="text-xs text-fg-muted hover:text-err">disconnect</button></li>
		{/each}
	</ul>
	<form onsubmit={(event) => { event.preventDefault(); void save(); }} class="mt-3 grid gap-2 sm:grid-cols-2">
		<input bind:value={name} required placeholder="connection name" class="border border-border bg-bg px-2 py-1.5 text-sm text-fg" />
		<input bind:value={origin} required placeholder="https://git.example.com" class="border border-border bg-bg px-2 py-1.5 text-sm text-fg" />
		<input bind:value={username} required placeholder="username" class="border border-border bg-bg px-2 py-1.5 text-sm text-fg" />
		<input bind:value={token} required type="password" autocomplete="off" placeholder="access token" class="border border-border bg-bg px-2 py-1.5 text-sm text-fg" />
		<button disabled={busy} class="border border-border px-3 py-1.5 text-sm text-fg-muted hover:text-fg">{busy ? 'saving…' : 'save HTTPS connection'}</button>
		<button type="button" onclick={connectGithub} class="border border-border px-3 py-1.5 text-sm text-fg-muted hover:text-fg">connect GitHub</button>
	</form>
</section>
