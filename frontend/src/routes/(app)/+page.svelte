<script lang="ts">
	import { goto } from '$app/navigation';
	import { getContext } from 'svelte';
	import type { CurrentUser } from '$lib/api';
	import AppHeader from '$lib/components/AppHeader.svelte';
	import SummaryPanel from '$lib/components/SummaryPanel.svelte';
	import WorkspaceNav from '$lib/components/WorkspaceNav.svelte';
	import { WORKSPACE_CONTEXT, type WorkspaceState } from '$lib/workspace.svelte';

	let { data }: { data: { user: CurrentUser } } = $props();
	let resultActive = $state(false);
	const workspace = getContext<WorkspaceState>(WORKSPACE_CONTEXT);

	function openPromptForRepo(name: string) {
		const repo = workspace.repos.find((item) => item.name === name);
		if (repo) void goto(`/settings?repo_id=${repo.id}`);
	}
</script>

<svelte:head>
	<title>surgite — git standup summaries</title>
	<meta name="description" content="Generate standup summaries from your git history." />
</svelte:head>

<main class="mx-auto min-h-screen px-4 py-6 transition-[max-width] sm:px-6 sm:py-10 {resultActive ? 'max-w-5xl' : 'max-w-2xl'}">
	<div class="border border-border bg-surface">
		<AppHeader user={data.user} />

		<div class="px-4 py-6 sm:px-6">
			<div class="flex items-center gap-2"><span class="text-accent" aria-hidden="true">&gt;_</span><h1 class="text-lg font-semibold text-fg">surgite</h1><span class="cursor" aria-hidden="true"></span></div>
			<p class="mt-1 text-sm text-fg-muted">Generate standup summaries from your git history.</p>
			<div class="mt-1 border-b border-border-subtle border-dashed"></div>
			<WorkspaceNav current="summary" />
		</div>

		<div class="px-4 pb-6 sm:px-6">
			<SummaryPanel
				repos={workspace.repos}
				staleAfterSeconds={workspace.staleAfterSeconds}
				syncingIds={workspace.syncingIds}
				bind:resultActive
				onOpenRepos={() => goto('/repositories')}
				onSync={(repos) => workspace.sync(repos)}
				onEditPrompt={openPromptForRepo}
			/>
		</div>
	</div>
</main>
