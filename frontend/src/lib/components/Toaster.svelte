<script lang="ts">
	import { toasts } from '$lib/toast.svelte';
</script>

<div
	class="pointer-events-none fixed right-4 bottom-10 left-4 z-50 flex max-w-md flex-col gap-1 sm:left-auto"
	aria-live="polite"
>
	{#each toasts.items as t (t.id)}
		<div
			role="status"
			class="pointer-events-auto flex items-start gap-2 border border-border bg-surface px-3 py-2 text-sm"
		>
			{#if t.kind === 'success'}
				<span class="shrink-0 text-ok">[ ok ]</span>
			{:else}
				<span class="shrink-0 text-err">[fail]</span>
			{/if}
			<span class="flex-1 text-fg">{t.message}</span>
			<button
				onclick={() => toasts.dismiss(t.id)}
				aria-label="Dismiss notification"
				class="shrink-0 text-fg-faint transition hover:text-fg"
			>
				✕
			</button>
		</div>
	{/each}
</div>
