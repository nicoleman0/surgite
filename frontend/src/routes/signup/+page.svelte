<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { signup } from '$lib/api';

	let password = $state('');
	let email = $state('');
	let displayName = $state('');
	let submitting = $state(false);
	let error = $state<string | null>(null);

	function focusOnMount(node: HTMLInputElement) {
		node.focus();
	}

	const token = $derived(page.url.searchParams.get('token') ?? '');

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		if (!token || !password) return;
		submitting = true;
		error = null;
		try {
			await signup({
				token,
				password,
				email: email.trim() || undefined,
				display_name: displayName.trim() || undefined
			});
			goto('/');
		} catch (e2) {
			error = e2 instanceof Error ? e2.message : 'Signup failed';
			submitting = false;
		}
	}

	function handleKey(e: KeyboardEvent) {
		if (e.key === 'Escape') goto('/');
	}
</script>

<svelte:head>
	<title>signup — surgite</title>
</svelte:head>

<svelte:window onkeydown={handleKey} />

<main class="mx-auto min-h-screen max-w-2xl px-4 py-6 sm:px-6 sm:py-10">
	<div class="border border-border bg-surface">
		<div class="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
			<span class="flex-1 text-xs text-fg-muted">surgite</span>
		</div>

		<div class="px-4 py-6 sm:px-6">
			<div class="flex items-center gap-2">
				<span class="text-accent" aria-hidden="true">&gt;_</span>
				<h1 class="text-lg font-semibold text-fg">signup</h1>
				<span class="cursor" aria-hidden="true"></span>
			</div>
			<p class="mt-1 text-sm text-fg-muted">Redeem your invite to create a surgite account.</p>
			<div class="mt-1 border-b border-dashed border-border-subtle"></div>
		</div>

		{#if !token}
			<div class="px-4 pb-6 sm:px-6">
				<p class="text-sm text-err">
					Missing invite token. Use the link from your invite email.
				</p>
				<a href="/login" class="mt-3 inline-block text-sm text-accent underline">
					back to login
				</a>
			</div>
		{:else}
			<form onsubmit={submit} class="flex flex-col gap-3 px-4 pb-6 sm:px-6">
				<label class="flex flex-col gap-1 text-sm">
					<span class="text-fg-muted"><span class="text-accent">❯</span> password</span>
					<input
						type="password"
						bind:value={password}
						use:focusOnMount
						autocomplete="new-password"
						required
						minlength="8"
						class="border border-border bg-bg px-2 py-1.5 text-sm text-fg placeholder:text-fg-faint"
					/>
				</label>
				<label class="flex flex-col gap-1 text-sm">
					<span class="text-fg-muted">
						<span class="text-accent">❯</span> email <span class="text-fg-faint">(optional)</span>
					</span>
					<input
						type="email"
						bind:value={email}
						autocomplete="email"
						placeholder="only if your invite is open"
						class="border border-border bg-bg px-2 py-1.5 text-sm text-fg placeholder:text-fg-faint"
					/>
				</label>
				<label class="flex flex-col gap-1 text-sm">
					<span class="text-fg-muted">
						<span class="text-accent">❯</span> display name <span class="text-fg-faint">(optional)</span>
					</span>
					<input
						type="text"
						bind:value={displayName}
						autocomplete="name"
						class="border border-border bg-bg px-2 py-1.5 text-sm text-fg placeholder:text-fg-faint"
					/>
				</label>
				{#if error}
					<p class="text-xs text-err">{error}</p>
				{/if}
				<div class="flex flex-col items-stretch gap-2 sm:flex-row sm:items-center">
					<button
						type="submit"
						disabled={submitting}
						class="min-h-11 border border-border bg-surface px-3 py-1.5 text-sm text-fg transition hover:bg-surface-2 disabled:opacity-50 sm:min-h-0"
					>
						<span class="text-accent">❯</span>
						{submitting ? 'creating account…' : 'signup'}
					</button>
					<a href="/login" class="inline-flex min-h-11 items-center px-2 py-1.5 text-sm text-fg-muted transition hover:text-fg sm:min-h-0">
						already have an account? <span class="text-accent">login</span>
					</a>
				</div>
			</form>
		{/if}
	</div>
</main>
