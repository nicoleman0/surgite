<script lang="ts">
	import type { CurrentUser } from '$lib/api';
	import ThemePicker from './ThemePicker.svelte';
	import UserBadge from './UserBadge.svelte';

	let { user, currentPath }: { user: CurrentUser; currentPath: string } = $props();

	const links = [
		{ href: '/', label: 'Summary' },
		{ href: '/repositories', label: 'Repositories' },
		{ href: '/settings', label: 'Settings' },
		{ href: '/summaries', label: 'Saved' }
	];
</script>

<div class="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
	<a href="/" class="flex-1 text-xs text-fg-muted transition hover:text-fg">surgite</a>
	<div class="hidden items-center gap-2 sm:flex">
		<a href="/summaries" class="text-xs text-fg-muted transition hover:text-fg">summaries</a>
		<UserBadge {user} />
	</div>
	<ThemePicker />
</div>
<nav
	class="flex flex-wrap border-b border-border px-2 sm:hidden"
	aria-label="Mobile navigation"
>
	{#each links as link}
		<a
			href={link.href}
			aria-current={currentPath === link.href ? 'page' : undefined}
			class="inline-flex min-h-11 items-center border-b-2 px-2 text-xs transition {currentPath ===
			link.href
				? 'border-accent text-fg'
				: 'border-transparent text-fg-muted hover:text-fg'}"
		>
			{link.label}
		</a>
	{/each}
	{#if user.is_admin}
		<a
			href="/admin"
			aria-current={currentPath === '/admin' ? 'page' : undefined}
			class="inline-flex min-h-11 items-center border-b-2 px-2 text-xs transition {currentPath ===
			'/admin'
				? 'border-accent text-fg'
				: 'border-transparent text-fg-muted hover:text-fg'}"
		>
			Admin
		</a>
	{/if}
</nav>
<div class="flex min-w-0 flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2 sm:hidden">
	<UserBadge {user} showAdminLink={false} />
</div>
