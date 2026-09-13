<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { onMount } from 'svelte';
	import { login } from '$lib/api';
	import { safeReturnPath } from '$lib/navigation';

	let email = $state('');
	let password = $state('');
	let submitting = $state(false);
	let error = $state<string | null>(null);

	function focusOnMount(node: HTMLInputElement) {
		node.focus();
	}

	function lockoutMessage(seconds: number): string {
		const minutes = Math.max(1, Math.ceil(seconds / 60));
		return `Account temporarily locked. Try again in ${minutes} minute${minutes === 1 ? '' : 's'}.`;
	}

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		if (!email.trim() || !password) return;
		submitting = true;
		error = null;
		try {
			await login(email.trim(), password);
			goto(safeReturnPath(page.url.searchParams.get('next'), window.location.origin));
		} catch (e2) {
			const err = e2 as Error & { lockoutSeconds?: number };
			error =
				err.lockoutSeconds != null
					? lockoutMessage(err.lockoutSeconds)
					: 'Invalid email or password';
			submitting = false;
		}
	}

	function handleKey(e: KeyboardEvent) {
		if (e.key === 'Escape') goto('/');
	}

	onMount(() => {
		const params = new URLSearchParams(window.location.search);
		if (params.get('retry')) error = 'Please log in to continue.';
		else if (params.get('reset') === 'ok') error = 'Password reset; please log in.';
	});
</script>

<svelte:head>
	<title>login — surgite</title>
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
				<h1 class="text-lg font-semibold text-fg">login</h1>
				<span class="cursor" aria-hidden="true"></span>
			</div>
			<p class="mt-1 text-sm text-fg-muted">Sign in to your surgite account.</p>
			<div class="mt-1 border-b border-dashed border-border-subtle"></div>
		</div>

		<form onsubmit={submit} class="flex flex-col gap-3 px-4 pb-6 sm:px-6">
			<label class="flex flex-col gap-1 text-sm">
				<span class="text-fg-muted"><span class="text-accent">❯</span> email</span>
				<input
					type="email"
					bind:value={email}
					use:focusOnMount
					autocomplete="email"
					required
					class="border border-border bg-bg px-2 py-1.5 text-sm text-fg placeholder:text-fg-faint"
				/>
			</label>
			<label class="flex flex-col gap-1 text-sm">
				<span class="text-fg-muted"><span class="text-accent">❯</span> password</span>
				<input
					type="password"
					bind:value={password}
					autocomplete="current-password"
					required
					class="border border-border bg-bg px-2 py-1.5 text-sm text-fg placeholder:text-fg-faint"
				/>
			</label>
			{#if error}
				<p class="text-xs text-err">{error}</p>
			{/if}
			<div class="flex items-center gap-2">
				<button
					type="submit"
					disabled={submitting}
					class="border border-border bg-surface px-3 py-1.5 text-sm text-fg transition hover:bg-surface-2 disabled:opacity-50"
				>
					<span class="text-accent">❯</span>
					{submitting ? 'logging in…' : 'login'}
				</button>
				<a href="/signup" class="px-2 py-1.5 text-sm text-fg-muted transition hover:text-fg">
					need an account? <span class="text-accent">signup</span>
				</a>
			</div>
			<p class="text-xs text-fg-faint">
				Forgot your password? Ask an admin to issue a reset token. They use
				<code class="text-fg-muted">POST /admin/users/{'{id}'}/reset-password</code> to mint one.
			</p>
		</form>
	</div>
</main>
