<script lang="ts">
	type Workspace = 'summary' | 'repositories' | 'settings';

	let { current }: { current: Workspace } = $props();

	const links: { id: Workspace; href: string; label: string }[] = [
		{ id: 'summary', href: '/', label: 'Summary' },
		{ id: 'repositories', href: '/repositories', label: 'Repositories' },
		{ id: 'settings', href: '/settings', label: 'Settings' }
	];
</script>

<nav class="mt-4 flex gap-1 border-b border-border-subtle text-sm" aria-label="Workspace">
	{#each links as link}
		<a
			href={link.href}
			aria-current={current === link.id ? 'page' : undefined}
			class="border-b-2 px-3 py-1.5 transition {current === link.id
				? 'border-accent text-fg'
				: 'border-transparent text-fg-muted hover:text-fg'}"
		>
			{link.label}
		</a>
	{/each}
</nav>
