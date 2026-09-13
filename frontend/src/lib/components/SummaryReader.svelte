<script lang="ts">
	import { onMount } from 'svelte';
	import { browser } from '$app/environment';
	import { downloadText, summaryFilename } from '$lib/download';
	import { renderMarkdown } from '$lib/markdown';
	import {
		parseSummaryLayout,
		resolveActiveRepo,
		sortSummaryEntries,
		summaryPreview,
		type SummaryEntry,
		type SummaryLayout
	} from '$lib/summary-view';
	import type { RepoFreshness } from '$lib/repo-freshness';
	import { relativeTime } from '$lib/time';
	import { toasts } from '$lib/toast.svelte';

	type ReaderFreshness = RepoFreshness & { resultNeedsRefresh: boolean; lastSyncedAt: string | null };

	let {
		entries,
		freshnessByRepo = {},
		refreshingRepo = null,
		onSync,
		onRefresh,
		onEditPrompt
	}: {
		entries: SummaryEntry[];
		freshnessByRepo?: Record<string, ReaderFreshness>;
		refreshingRepo?: string | null;
		onSync?: (repo: string) => void;
		onRefresh?: (entry: SummaryEntry) => void;
		onEditPrompt?: (repo: string) => void;
	} = $props();

	let layout = $state<SummaryLayout>('focus');
	let selectedRepo = $state<string | null>(null);
	let copiedRepo = $state<string | null>(null);

	const sortedEntries = $derived(sortSummaryEntries(entries));
	const activeRepo = $derived(resolveActiveRepo(sortedEntries, selectedRepo));
	const activeIndex = $derived(activeRepo ? sortedEntries.findIndex((entry) => entry.repo === activeRepo) : -1);
	const activeEntry = $derived(activeIndex >= 0 ? sortedEntries[activeIndex] : null);

	$effect(() => {
		if (sortedEntries.length) selectedRepo = resolveActiveRepo(sortedEntries, selectedRepo);
	});

	onMount(() => {
		layout = parseSummaryLayout(localStorage.getItem('summary-layout'));
	});

	function setLayout(next: SummaryLayout) {
		layout = next;
		if (browser) localStorage.setItem('summary-layout', next);
	}

	function selectRepo(repo: string) {
		selectedRepo = repo;
	}

	function move(step: number) {
		const next = sortedEntries[activeIndex + step];
		if (next) selectRepo(next.repo);
	}

	function isEditableTarget(target: EventTarget | null): boolean {
		return (
			target instanceof HTMLElement &&
			(target.matches('input, textarea, select') || target.isContentEditable)
		);
	}

	function handleKeydown(event: KeyboardEvent) {
		if (!(event.target instanceof Element) || !event.target.closest('[data-summary-reader]')) return;
		if (isEditableTarget(event.target)) return;
		if (event.key === 'ArrowLeft' && activeIndex > 0) {
			event.preventDefault();
			move(-1);
		}
		if (event.key === 'ArrowRight' && activeIndex < sortedEntries.length - 1) {
			event.preventDefault();
			move(1);
		}
	}

	function statusLabel(entry: SummaryEntry): string {
		if (entry.status === 'waiting') return 'waiting';
		if (entry.status === 'streaming') return 'streaming';
		if (entry.status === 'error') return 'failed';
		return 'complete';
	}

	function statusIcon(entry: SummaryEntry): string {
		if (entry.status === 'waiting') return '○';
		if (entry.status === 'streaming') return '◌';
		if (entry.status === 'error') return '×';
		return '✓';
	}

	function freshness(entry: SummaryEntry): ReaderFreshness | undefined {
		return freshnessByRepo[entry.repo];
	}

	function freshnessLabel(entry: SummaryEntry): string {
		const current = freshness(entry);
		if (!current) return '';
		if (current.status === 'syncing') return 'source syncing';
		if (current.status === 'failed') return current.lastSyncedAt ? `source sync failed · last good ${relativeTime(current.lastSyncedAt)}` : 'source sync failed';
		if (current.status === 'never') return 'source never synced';
		if (current.status === 'stale') return current.lastSyncedAt ? `source needs sync · last synced ${relativeTime(current.lastSyncedAt)}` : 'source needs sync';
		if (current.resultNeedsRefresh) return 'source updated — refresh result';
		return current.lastSyncedAt ? `source synced ${relativeTime(current.lastSyncedAt)}` : 'source current';
	}

	function freshnessIcon(entry: SummaryEntry): string {
		const status = freshness(entry)?.status;
		if (status === 'failed') return '×';
		if (status === 'syncing') return '◌';
		if (status === 'never' || status === 'stale') return '▲';
		return '✓';
	}

	function freshnessClass(entry: SummaryEntry): string {
		const status = freshness(entry)?.status;
		if (status === 'failed') return 'text-err';
		if (status === 'syncing' || status === 'never' || status === 'stale') return 'text-accent';
		return 'text-ok';
	}

	function hasUsableText(entry: SummaryEntry): boolean {
		return entry.status !== 'waiting' && entry.status !== 'error' && entry.text.trim().length > 0;
	}

	async function copy(entry: SummaryEntry) {
		if (!hasUsableText(entry)) return;
		try {
			await navigator.clipboard.writeText(entry.text);
			copiedRepo = entry.repo;
			setTimeout(() => {
				if (copiedRepo === entry.repo) copiedRepo = null;
			}, 1500);
		} catch {
			toasts.error('could not copy — clipboard needs a secure (HTTPS) context');
		}
	}

	function download(entry: SummaryEntry) {
		if (!hasUsableText(entry)) return;
		downloadText(summaryFilename(entry.repo, entry.kind === 'ai' ? 'summary' : 'log'), entry.text);
	}

	function openEntry(repo: string) {
		selectRepo(repo);
		setLayout('focus');
	}

	function refreshLabel(entry: SummaryEntry): string {
		if (refreshingRepo === entry.repo) return entry.kind === 'ai' ? 'regenerating…' : 'refreshing…';
		return entry.kind === 'ai' ? '◌ regenerate' : '↻ refresh log';
	}
</script>

<svelte:window onkeydown={handleKeydown} />

<section class="mt-3" aria-label="Repository summaries" data-summary-reader>
	<div class="mb-2 flex flex-wrap items-center justify-between gap-2">
		<p class="text-xs text-fg-faint">{sortedEntries.length} repository summaries</p>
		<div class="flex border border-border text-xs" aria-label="Summary layout">
			<button
				type="button"
				onclick={() => setLayout('focus')}
				aria-pressed={layout === 'focus'}
				class="px-2.5 py-1.5 transition {layout === 'focus'
					? 'bg-accent text-accent-contrast'
					: 'bg-bg text-fg-muted hover:bg-surface'}"
			>
				focus
			</button>
			<button
				type="button"
				onclick={() => setLayout('grid')}
				aria-pressed={layout === 'grid'}
				class="border-l border-border px-2.5 py-1.5 transition {layout === 'grid'
					? 'bg-accent text-accent-contrast'
					: 'bg-bg text-fg-muted hover:bg-surface'}"
			>
				grid
			</button>
		</div>
	</div>

	{#if layout === 'focus' && activeEntry}
		<div class="grid border border-border bg-bg sm:grid-cols-[12rem_minmax(0,1fr)]">
			<nav class="hidden divide-y divide-border-subtle bg-surface sm:block" aria-label="Repository summaries">
				{#each sortedEntries as entry (entry.repo)}
					<button
						type="button"
						onclick={() => selectRepo(entry.repo)}
						aria-current={entry.repo === activeRepo ? 'page' : undefined}
						class="flex w-full flex-col gap-1 px-3 py-3 text-left text-xs transition {entry.repo === activeRepo
							? 'border-l-3 border-accent bg-surface-2 text-fg'
							: 'border-l-3 border-transparent text-fg-muted hover:bg-surface-2 hover:text-fg'}"
					>
						<span class="flex min-w-0 items-center gap-2">
							<span class={entry.status === 'error' ? 'text-err' : entry.status === 'complete' ? 'text-ok' : 'text-accent'} aria-label={statusLabel(entry)}>{statusIcon(entry)}</span>
							<span class="truncate">{entry.repo}</span>
						</span>
						<span class="text-fg-faint">{entry.commits} commit{entry.commits === 1 ? '' : 's'} · {statusLabel(entry)}</span>
						{#if freshness(entry)}
							<span class="text-fg-faint"><span class={freshnessClass(entry)}>{freshnessIcon(entry)}</span> {freshnessLabel(entry)}</span>
						{/if}
					</button>
				{/each}
			</nav>

			<div class="min-w-0">
				<select
					bind:value={selectedRepo}
					aria-label="Repository summary"
					class="block w-full min-w-0 border-b border-border bg-surface px-3 py-2.5 text-sm text-fg sm:hidden"
				>
					{#each sortedEntries as entry (entry.repo)}
						<option value={entry.repo}>{entry.repo} — {entry.commits} commits</option>
					{/each}
				</select>

				<header class="flex min-w-0 flex-col items-stretch justify-between gap-3 border-b border-border-subtle bg-surface-2 px-4 py-3 sm:flex-row sm:items-start">
					<div class="min-w-0">
						<h3 class="break-words text-sm font-semibold text-fg sm:truncate">{activeEntry.repo}</h3>
						<p class="mt-0.5 break-words text-xs text-fg-muted">
							{activeEntry.commits} commit{activeEntry.commits === 1 ? '' : 's'} · {statusLabel(activeEntry)}
							{#if activeEntry.provider}
								· {activeEntry.provider}{activeEntry.model ? ` · ${activeEntry.model}` : ''}
							{/if}
						</p>
						{#if freshness(activeEntry)}
							<p class="mt-0.5 text-xs {freshnessClass(activeEntry)}">{freshnessIcon(activeEntry)} {freshnessLabel(activeEntry)}</p>
						{/if}
					</div>
					<div class="flex flex-wrap items-center gap-1 sm:shrink-0 sm:justify-end">
						{#if onSync}
							<button
								type="button"
								onclick={() => onSync?.(activeEntry.repo)}
								disabled={freshness(activeEntry)?.status === 'syncing'}
								class="px-1.5 py-1 text-xs text-fg-muted transition hover:bg-surface hover:text-fg disabled:opacity-40"
							>
								{freshness(activeEntry)?.status === 'failed' ? '↻ retry sync' : '↻ sync'}
							</button>
						{/if}
						{#if onRefresh}
							<button
								type="button"
								onclick={() => onRefresh?.(activeEntry)}
								disabled={refreshingRepo !== null || freshness(activeEntry)?.status === 'syncing'}
								class="px-1.5 py-1 text-xs text-fg-muted transition hover:bg-surface hover:text-fg disabled:opacity-40"
							>
								{refreshLabel(activeEntry)}
							</button>
						{/if}
						{#if onEditPrompt && activeEntry.kind === 'ai'}
							<button type="button" onclick={() => onEditPrompt?.(activeEntry.repo)} class="px-1.5 py-1 text-xs text-fg-muted transition hover:bg-surface hover:text-fg">edit prompt</button>
						{/if}
						<button
							type="button"
							onclick={() => copy(activeEntry)}
							disabled={!hasUsableText(activeEntry)}
							class="px-1.5 py-1 text-xs text-fg-muted transition hover:bg-surface hover:text-fg disabled:opacity-40"
						>
							{copiedRepo === activeEntry.repo ? '✓ copied' : 'copy'}
						</button>
						<button
							type="button"
							onclick={() => download(activeEntry)}
							disabled={!hasUsableText(activeEntry)}
							class="px-1.5 py-1 text-xs text-fg-muted transition hover:bg-surface hover:text-fg disabled:opacity-40"
						>
							↓ download
						</button>
					</div>
				</header>

				<div class="min-h-52 px-4 py-4">
					{#if activeEntry.status === 'error'}
						<p class="text-sm text-err">{activeEntry.text}</p>
					{:else if !activeEntry.text}
						<p class="text-sm text-fg-muted">{activeEntry.status === 'waiting' ? '○ waiting for summary…' : '◌ generating summary…'}</p>
					{:else if activeEntry.kind === 'ai'}
						<div class="space-y-2 text-sm leading-relaxed text-fg">{@html renderMarkdown(activeEntry.text)}</div>
					{:else}
						<pre class="max-h-96 overflow-auto bg-surface p-3 text-xs leading-relaxed text-fg">{activeEntry.text}</pre>
					{/if}
				</div>

				<footer class="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-2 border-t border-border-subtle px-3 py-2 text-xs text-fg-muted">
					<button type="button" onclick={() => move(-1)} disabled={activeIndex <= 0} class="justify-self-start px-1.5 py-1 hover:bg-surface hover:text-fg disabled:opacity-40">← previous</button>
					<span>{activeIndex + 1} of {sortedEntries.length}</span>
					<button type="button" onclick={() => move(1)} disabled={activeIndex >= sortedEntries.length - 1} class="justify-self-end px-1.5 py-1 hover:bg-surface hover:text-fg disabled:opacity-40">next →</button>
				</footer>
			</div>
		</div>
	{:else if layout === 'grid'}
		<div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
			{#each sortedEntries as entry (entry.repo)}
				<article class="border border-border bg-bg">
					<header class="flex items-start justify-between gap-3 border-b border-border-subtle bg-surface-2 px-3 py-2.5">
						<div class="min-w-0">
							<h3 class="truncate text-sm font-semibold text-fg">{entry.repo}</h3>
							<p class="mt-0.5 text-xs text-fg-muted">{entry.commits} commit{entry.commits === 1 ? '' : 's'} · {statusLabel(entry)}</p>
							{#if freshness(entry)}
								<p class="mt-0.5 text-xs {freshnessClass(entry)}">{freshnessIcon(entry)} {freshnessLabel(entry)}</p>
							{/if}
						</div>
						<span class={entry.status === 'error' ? 'text-err' : entry.status === 'complete' ? 'text-ok' : 'text-accent'} aria-label={statusLabel(entry)}>{statusIcon(entry)}</span>
					</header>
					<div class="px-3 py-3">
						{#if entry.status === 'error'}
							<p class="text-sm text-err">{entry.text}</p>
						{:else if !entry.text}
							<p class="text-sm text-fg-muted">{entry.status === 'waiting' ? 'waiting for summary…' : 'generating summary…'}</p>
						{:else}
							<p class="text-sm leading-relaxed text-fg">{summaryPreview(entry.text)}</p>
						{/if}
						<button type="button" onclick={() => openEntry(entry.repo)} class="mt-3 text-xs text-accent transition hover:text-accent-hover">open →</button>
					</div>
				</article>
			{/each}
		</div>
	{/if}
</section>
