<script lang="ts">
	import { onMount } from 'svelte';
	import { listRepos, type Repo } from '$lib/api';
	import { RepoSyncManager } from '$lib/repo-sync';
	import { nextTab } from '$lib/tabs';
	import AddRepoForm from '$lib/components/AddRepoForm.svelte';
	import HelpOverlay from '$lib/components/HelpOverlay.svelte';
	import PromptSettings from '$lib/components/PromptSettings.svelte';
	import ProviderKeys from '$lib/components/ProviderKeys.svelte';
	import RepoList from '$lib/components/RepoList.svelte';
	import SummaryPanel from '$lib/components/SummaryPanel.svelte';
	import ThemePicker from '$lib/components/ThemePicker.svelte';
	import UserBadge from '$lib/components/UserBadge.svelte';

	type Workspace = 'summary' | 'repositories' | 'settings';
	const WORKSPACES: Workspace[] = ['summary', 'repositories', 'settings'];

	let repos = $state<Repo[]>([]);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let showHelp = $state(false);
	let workspace = $state<Workspace>('summary');
	let resultActive = $state(false);
	let staleAfterSeconds = $state<number | null>(null);
	let syncingIds = $state(new Set<number>());
	let requestedSettingsRepoId = $state<number | null>(null);
	let settingsRequestVersion = $state(0);

	function applyRepoData(data: { repos: Repo[]; stale_after_seconds: number | null }) {
		repos = data.repos;
		staleAfterSeconds = data.stale_after_seconds;
	}

	async function loadRepos() {
		loading = true;
		error = null;
		try {
			applyRepoData(await listRepos());
		} catch (cause) {
			error = cause instanceof Error ? cause.message : 'Failed to load repos';
		} finally {
			loading = false;
		}
	}

	const syncManager = new RepoSyncManager(applyRepoData, (next) => (syncingIds = next));

	onMount(() => {
		void loadRepos();
		return () => syncManager.destroy();
	});

	const KONAMI = ['ArrowUp', 'ArrowUp', 'ArrowDown', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'ArrowLeft', 'ArrowRight', 'b', 'a'];
	let konamiIdx = 0;

	function handleKey(event: KeyboardEvent) {
		if (event.key === 'F1') {
			event.preventDefault();
			showHelp = !showHelp;
			return;
		}
		if (event.key === KONAMI[konamiIdx]) {
			konamiIdx++;
			if (konamiIdx === KONAMI.length) {
				konamiIdx = 0;
				document.documentElement.classList.toggle('crt');
			}
		} else {
			konamiIdx = 0;
		}
	}

	function openWorkspace(next: Workspace) {
		workspace = next;
	}

	function handleRepoAdded(repo: Repo) {
		void loadRepos();
		syncManager.trackInitialIngest(repo);
	}

	function openPromptForRepo(name: string) {
		const repo = repos.find((item) => item.name === name);
		if (!repo) return;
		requestedSettingsRepoId = repo.id;
		settingsRequestVersion += 1;
		openWorkspace('settings');
	}

	function handleWorkspaceKeydown(event: KeyboardEvent, current: Workspace) {
		const next = nextTab(current, event.key, WORKSPACES);
		if (!next) return;
		event.preventDefault();
		workspace = next;
		document.getElementById(`${next}-tab`)?.focus();
	}
</script>

<svelte:head>
	<title>surgite — git standup summaries</title>
	<meta name="description" content="Generate standup summaries from your git history." />
</svelte:head>

<svelte:window onkeydown={handleKey} />

<main class="mx-auto min-h-screen px-4 py-6 transition-[max-width] sm:px-6 sm:py-10 {workspace === 'summary' && resultActive ? 'max-w-5xl' : 'max-w-2xl'}">
	<div class="border border-border bg-surface">
		<div class="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
			<span class="flex-1 text-xs text-fg-muted">surgite</span>
			<div class="flex items-center gap-2">
				<a href="/summaries" class="text-xs text-fg-muted transition hover:text-fg">summaries</a>
				<UserBadge />
				<ThemePicker />
			</div>
		</div>

		<div class="px-4 py-6 sm:px-6">
			<div class="flex items-center gap-2"><span class="text-accent" aria-hidden="true">&gt;_</span><h1 class="text-lg font-semibold text-fg">surgite</h1><span class="cursor" aria-hidden="true"></span></div>
			<p class="mt-1 text-sm text-fg-muted">Generate standup summaries from your git history.</p>
			<div class="mt-1 border-b border-border-subtle border-dashed"></div>

			<div class="mt-4 flex gap-1 border-b border-border-subtle text-sm" role="tablist" aria-label="Workspace">
				<button id="summary-tab" type="button" role="tab" aria-controls="summary-panel" aria-selected={workspace === 'summary'} onclick={() => openWorkspace('summary')} onkeydown={(event) => handleWorkspaceKeydown(event, 'summary')} class="border-b-2 px-3 py-1.5 transition {workspace === 'summary' ? 'border-accent text-fg' : 'border-transparent text-fg-muted hover:text-fg'}">Summary</button>
				<button id="repositories-tab" type="button" role="tab" aria-controls="repositories-panel" aria-selected={workspace === 'repositories'} onclick={() => openWorkspace('repositories')} onkeydown={(event) => handleWorkspaceKeydown(event, 'repositories')} class="border-b-2 px-3 py-1.5 transition {workspace === 'repositories' ? 'border-accent text-fg' : 'border-transparent text-fg-muted hover:text-fg'}">Repositories</button>
				<button id="settings-tab" type="button" role="tab" aria-controls="settings-panel" aria-selected={workspace === 'settings'} onclick={() => openWorkspace('settings')} onkeydown={(event) => handleWorkspaceKeydown(event, 'settings')} class="border-b-2 px-3 py-1.5 transition {workspace === 'settings' ? 'border-accent text-fg' : 'border-transparent text-fg-muted hover:text-fg'}">Settings</button>
			</div>
		</div>

		<div id="summary-panel" role="tabpanel" aria-labelledby="summary-tab" hidden={workspace !== 'summary'} class="px-4 pb-6 sm:px-6">
			<SummaryPanel
				{repos}
				{staleAfterSeconds}
				{syncingIds}
				bind:resultActive
				onOpenRepos={() => openWorkspace('repositories')}
				onSync={(targets) => syncManager.sync(targets)}
				onEditPrompt={openPromptForRepo}
			/>
		</div>
		<div id="repositories-panel" role="tabpanel" aria-labelledby="repositories-tab" hidden={workspace !== 'repositories'} class="px-4 pb-6 sm:px-6">
			<div class="max-w-2xl">
				<h2 class="text-sm text-fg-muted"><span class="text-accent">~/repos</span> <span aria-hidden="true">❯</span></h2>
				<AddRepoForm onAdded={handleRepoAdded} />
				<RepoList {repos} {loading} {error} onChanged={loadRepos} {syncingIds} onSync={(targets) => syncManager.sync(targets)} />
			</div>
		</div>
		<div id="settings-panel" role="tabpanel" aria-labelledby="settings-tab" hidden={workspace !== 'settings'} class="px-4 pb-6 sm:px-6">
			<div class="max-w-2xl">
				<PromptSettings {repos} active={workspace === 'settings'} requestedRepoId={requestedSettingsRepoId} requestVersion={settingsRequestVersion} />
				<div class="mt-8"><ProviderKeys active={workspace === 'settings'} /></div>
			</div>
		</div>
	</div>
</main>

{#if showHelp}<HelpOverlay onclose={() => (showHelp = false)} />{/if}
