<script lang="ts">
	import { page } from '$app/state';
	import { getContext } from 'svelte';
	import type { CurrentUser } from '$lib/api';
	import AppHeader from '$lib/components/AppHeader.svelte';
	import GitConnections from '$lib/components/GitConnections.svelte';
	import PromptSettings from '$lib/components/PromptSettings.svelte';
	import ProviderKeys from '$lib/components/ProviderKeys.svelte';
	import WorkspaceNav from '$lib/components/WorkspaceNav.svelte';
	import { WORKSPACE_CONTEXT, type WorkspaceState } from '$lib/workspace.svelte';

	let { data }: { data: { user: CurrentUser } } = $props();
	const workspace = getContext<WorkspaceState>(WORKSPACE_CONTEXT);
	const initialRepoId = $derived.by(() => {
		if (workspace.reposLoading || workspace.reposError) return null;
		const value = Number(page.url.searchParams.get('repo_id'));
		return Number.isInteger(value) && workspace.repos.some((repo) => repo.id === value)
			? value
			: null;
	});
</script>

<svelte:head><title>settings — surgite</title></svelte:head>

<main class="mx-auto min-h-screen max-w-2xl px-4 py-6 sm:px-6 sm:py-10">
	<div class="border border-border bg-surface">
		<AppHeader user={data.user} />
		<div class="px-4 py-6 sm:px-6">
			<div class="flex items-center gap-2"><span class="text-accent" aria-hidden="true">&gt;_</span><h1 class="text-lg font-semibold text-fg">surgite</h1><span class="cursor" aria-hidden="true"></span></div>
			<p class="mt-1 text-sm text-fg-muted">Generate standup summaries from your git history.</p>
			<div class="mt-1 border-b border-border-subtle border-dashed"></div>
			<WorkspaceNav current="settings" />
		</div>
		<div class="px-4 pb-6 sm:px-6">
			{#if workspace.reposLoading}
				<p class="text-sm text-fg-muted">⣾ loading repositories…</p>
			{:else}
				{#if workspace.reposError}<p class="mb-3 text-xs text-err">{workspace.reposError}</p>{/if}
				<PromptSettings repos={workspace.repos} {initialRepoId} />
			{/if}
			<div class="mt-8"><GitConnections {workspace} /></div>
			<div class="mt-8"><ProviderKeys /></div>
		</div>
	</div>
</main>
