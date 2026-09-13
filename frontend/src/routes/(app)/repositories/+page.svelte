<script lang="ts">
	import { getContext } from 'svelte';
	import type { CurrentUser, Repo } from '$lib/api';
	import AddRepoForm from '$lib/components/AddRepoForm.svelte';
	import AppHeader from '$lib/components/AppHeader.svelte';
	import RepoList from '$lib/components/RepoList.svelte';
	import WorkspaceNav from '$lib/components/WorkspaceNav.svelte';
	import { WORKSPACE_CONTEXT, type WorkspaceState } from '$lib/workspace.svelte';

	let { data }: { data: { user: CurrentUser } } = $props();
	const workspace = getContext<WorkspaceState>(WORKSPACE_CONTEXT);

	function handleRepoAdded(repo: Repo) {
		void workspace.reloadRepos();
		workspace.trackInitialIngest(repo);
	}
</script>

<svelte:head><title>repositories — surgite</title></svelte:head>

<main class="mx-auto min-h-screen max-w-2xl px-4 py-6 sm:px-6 sm:py-10">
	<div class="border border-border bg-surface">
		<AppHeader user={data.user} />
		<div class="px-4 py-6 sm:px-6">
			<div class="flex items-center gap-2"><span class="text-accent" aria-hidden="true">&gt;_</span><h1 class="text-lg font-semibold text-fg">surgite</h1><span class="cursor" aria-hidden="true"></span></div>
			<p class="mt-1 text-sm text-fg-muted">Generate standup summaries from your git history.</p>
			<div class="mt-1 border-b border-border-subtle border-dashed"></div>
			<WorkspaceNav current="repositories" />
		</div>
		<div class="px-4 pb-6 sm:px-6">
			<h2 class="text-sm text-fg-muted"><span class="text-accent">~/repos</span> <span aria-hidden="true">❯</span></h2>
			{#if workspace.connectionsError}
				<p class="mt-2 text-xs text-err">{workspace.connectionsError}</p>
			{/if}
			<AddRepoForm onAdded={handleRepoAdded} connections={workspace.connections} />
			<RepoList
				repos={workspace.repos}
				loading={workspace.reposLoading}
				error={workspace.reposError}
				onChanged={() => workspace.reloadRepos()}
				syncingIds={workspace.syncingIds}
				connections={workspace.connections}
				onSync={(repos) => workspace.sync(repos)}
			/>
		</div>
	</div>
</main>
