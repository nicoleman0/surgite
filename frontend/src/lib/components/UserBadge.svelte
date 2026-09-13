<script lang="ts">
	import { goto } from '$app/navigation';
	import { logout, type CurrentUser } from '$lib/api';

	let { user, showAdminLink = true }: { user: CurrentUser; showAdminLink?: boolean } = $props();

	async function handleLogout() {
		try {
			await logout();
		} catch {
		}
		goto('/login');
	}
</script>

<span class="text-xs text-fg-muted">
	<span class="text-fg-faint">user:</span>
	{user.display_name || user.email}
</span>
{#if user.is_admin && showAdminLink}
	<a href="/admin" class="text-xs text-fg-muted transition hover:text-fg">admin</a>
{/if}
<button
	onclick={handleLogout}
	class="inline-flex items-center gap-1 border border-border bg-surface px-2 py-1.5 text-xs text-fg-muted transition hover:text-fg"
	aria-label="Log out"
>
	logout
</button>
