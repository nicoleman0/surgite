<script lang="ts">
	import { theme } from '$lib/theme.svelte';

	const VERSION = __APP_VERSION__;
</script>

<div data-testid="status-bar" class="static flex flex-wrap items-center justify-between gap-1 border-t border-border bg-surface-2 px-4 py-1 text-xs text-fg-faint sm:fixed sm:right-0 sm:bottom-0 sm:left-0 sm:z-40">
	<span>[surgite]</span>
	<span class="flex flex-wrap items-center gap-2">
		<a href="https://github.com/nicoleman0/surgite" class="text-fg-muted transition hover:text-accent" target="_blank" rel="noopener">github</a>
		<span aria-hidden="true">│</span>
		<span>scheme: <span class="text-fg-muted">{theme.current}</span></span>
		<span aria-hidden="true">│</span>
		<span>v{VERSION}</span>
	</span>
</div>
