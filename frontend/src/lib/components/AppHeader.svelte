<script lang="ts">
	import type { CurrentUser } from '$lib/api';
	import ThemePicker from './ThemePicker.svelte';
	import UserBadge from './UserBadge.svelte';

	let { user }: { user: CurrentUser } = $props();
</script>

<div class="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
	<a href="/" class="flex-1 text-xs text-fg-muted transition hover:text-fg">surgite</a>
	<div class="flex items-center gap-2">
		<a href="/summaries" class="text-xs text-fg-muted transition hover:text-fg">summaries</a>
		<UserBadge {user} />
		<ThemePicker />
	</div>
</div>
