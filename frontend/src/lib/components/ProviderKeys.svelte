<script lang="ts">
	import { ProviderKeysStore } from '$lib/provider-keys.svelte';
	import { relativeTime } from '$lib/time';
	import { toasts } from '$lib/toast.svelte';
	import { onMount } from 'svelte';
	import Skeleton from './Skeleton.svelte';

	const store = new ProviderKeysStore();

	let editing = $state<string | null>(null);
	let drafts = $state<Record<string, string>>({});

	onMount(() => {
		void store.load();
	});

	function edit(provider: string) {
		editing = provider;
		drafts = { ...drafts, [provider]: '' };
	}

	function cancel(provider: string) {
		editing = null;
		const { [provider]: _discarded, ...rest } = drafts;
		drafts = rest;
	}

	async function save(provider: string) {
		const key = (drafts[provider] ?? '').trim();
		if (!key) {
			store.errors = { ...store.errors, [provider]: 'Enter a key first.' };
			return;
		}
		if (await store.save(provider, key)) {
			cancel(provider);
			toasts.success(`${provider} key saved`);
		}
	}

	async function revoke(provider: string) {
		const ok = window.confirm(
			`Revoke your ${provider} key? You'll need to paste it again to restore it.`
		);
		if (!ok) return;
		if (await store.revoke(provider)) toasts.success(`${provider} key revoked`);
	}

	const labelCls = 'text-xs text-fg-muted';
	const btnCls =
		'border border-border px-2 py-1 text-xs text-fg-muted transition hover:text-fg disabled:opacity-50';
</script>

{#if store.supported}
	<section>
		<h2 class="text-sm text-fg-muted"><span class="text-accent">~/keys</span> <span aria-hidden="true">❯</span></h2>
		<p class="mt-1 text-xs text-fg-faint">
			Your own provider keys. Stored encrypted; never shown again after saving.
		</p>

		{#if store.loading && store.rows.length === 0}
			<div class="mt-3"><Skeleton rows={3} /></div>
		{:else}
			{#if store.error}
				<p class="mt-3 text-xs text-err">{store.error}</p>
			{/if}

			<ul class="mt-3 flex flex-col gap-2">
				{#each store.rows as row (row.provider)}
					<li class="border border-border bg-surface px-3 py-2">
						<div class="flex flex-wrap items-center gap-2">
							<span class="text-sm text-fg">{row.provider}</span>
							{#if row.isDefault}<span class="text-xs text-fg-faint">default</span>{/if}

							<span class="flex-1 text-xs text-fg-muted">
								{#if row.status === 'configured'}
									key set · added {relativeTime(row.created_at)}
								{:else if row.status === 'revoked'}
									revoked {relativeTime(row.revoked_at)}
								{:else}
									no key
								{/if}
							</span>

							{#if editing !== row.provider}
								<button
									type="button"
									onclick={() => edit(row.provider)}
									disabled={store.busy[row.provider]}
									class={btnCls}
								>
									{row.status === 'configured' ? '❯ replace' : '❯ add key'}
								</button>
								{#if row.status === 'configured'}
									<button
										type="button"
										onclick={() => revoke(row.provider)}
										disabled={store.busy[row.provider]}
										class={btnCls}
									>
										{store.busy[row.provider] ? 'revoking…' : '❯ revoke'}
									</button>
								{/if}
							{/if}
						</div>

						{#if editing === row.provider}
							<div class="mt-2 flex flex-wrap items-center gap-2">
								<label class="sr-only" for="pk-{row.provider}">{row.provider} API key</label>
								<input
									id="pk-{row.provider}"
									type="password"
									autocomplete="off"
									spellcheck="false"
									placeholder="paste key"
									bind:value={drafts[row.provider]}
									disabled={store.busy[row.provider]}
									class="flex-1 border border-border bg-bg px-2 py-1.5 text-sm text-fg"
								/>
								<button
									type="button"
									onclick={() => save(row.provider)}
									disabled={store.busy[row.provider]}
									class="border border-border bg-accent px-3 py-1.5 text-xs font-medium text-accent-contrast transition hover:bg-accent-hover disabled:opacity-50"
								>
									{store.busy[row.provider] ? 'saving…' : '❯ save'}
								</button>
								<button type="button" onclick={() => cancel(row.provider)} class={btnCls}>cancel</button>
							</div>
						{/if}

						{#if store.errors[row.provider]}
							<p class="mt-2 text-xs text-err">{store.errors[row.provider]}</p>
						{/if}
					</li>
				{/each}
			</ul>
		{/if}
	</section>
{/if}
