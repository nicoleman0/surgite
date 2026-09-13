<script lang="ts">
	import { onMount } from 'svelte';
	import {
		activateUser,
		createInvite,
		deactivateUser,
		fetchAdminUsers,
		fetchCurrentUser,
		unlockUser,
		type AdminInviteResponse,
		type AdminUser,
		type CurrentUser
	} from '$lib/api';
	import { relativeTime } from '$lib/time';
	import { toasts } from '$lib/toast.svelte';
	import Skeleton from '$lib/components/Skeleton.svelte';
	import ThemePicker from '$lib/components/ThemePicker.svelte';

	let me = $state<CurrentUser | null>(null);
	let loadError = $state<string | null>(null);

	let users = $state<AdminUser[]>([]);
	let total = $state(0);
	let loading = $state(true);

	let query = $state('');
	let inviteOpen = $state(false);
	let inviteEmail = $state('');
	let inviteRole = $state<'user' | 'admin'>('user');
	let inviteTtl = $state(7);
	let inviting = $state(false);
	let inviteError = $state<string | null>(null);
	let lastInvite = $state<AdminInviteResponse | null>(null);

	async function load() {
		loading = true;
		loadError = null;
		try {
			const out = await fetchAdminUsers();
			users = out.users;
			total = out.total;
		} catch (e) {
			loadError = e instanceof Error ? e.message : 'Failed to load users';
		} finally {
			loading = false;
		}
	}

	onMount(async () => {
		try {
			me = await fetchCurrentUser();
		} catch {
			me = null;
		}
		if (me?.is_admin) await load();
	});

	async function act(
		row: AdminUser,
		fn: (id: string) => Promise<void>,
		verb: string,
		optimistic: (u: AdminUser) => AdminUser
	) {
		const prev = users;
		users = users.map((u) => (u.id === row.id ? optimistic(u) : u));
		try {
			await fn(row.id);
			toasts.success(`${verb} ${row.email}`);
		} catch (e) {
			users = prev;
			toasts.error(e instanceof Error ? e.message : `${verb} failed`);
		}
	}

	async function sendInvite(e: SubmitEvent) {
		e.preventDefault();
		inviting = true;
		inviteError = null;
		try {
			const inv = await createInvite({
				email: inviteEmail.trim() || undefined,
				role: inviteRole,
				ttl_days: inviteTtl
			});
			lastInvite = inv;
			inviteEmail = '';
			inviteRole = 'user';
			inviteTtl = 7;
			inviteOpen = false;
			toasts.success('invite created');
		} catch (e2) {
			inviteError = e2 instanceof Error ? e2.message : 'invite failed';
		} finally {
			inviting = false;
		}
	}

	function inviteUrl(token: string): string {
		const base = typeof window === 'undefined' ? '' : window.location.origin;
		return `${base}/signup?token=${encodeURIComponent(token)}`;
	}

	async function copy(text: string) {
		try {
			await navigator.clipboard.writeText(text);
			toasts.success('copied to clipboard');
		} catch {
			toasts.error('copy failed — select and copy manually');
		}
	}

	function dismissInvite() {
		lastInvite = null;
	}

	function isLocked(until: string | null): boolean {
		return until != null && new Date(until).getTime() > Date.now();
	}

	const inviteLink = $derived(lastInvite ? inviteUrl(lastInvite.token) : '');
</script>

<svelte:head>
	<title>users — admin · surgite</title>
</svelte:head>

<main class="mx-auto min-h-screen max-w-4xl px-4 py-6 sm:px-6 sm:py-10">
	<div class="border border-border bg-surface">
		<div class="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
			<span class="flex-1 text-xs text-fg-muted">surgite</span>
			<ThemePicker />
		</div>

		<div class="px-4 py-6 sm:px-6">
			<div class="flex items-center gap-2">
				<span class="text-accent" aria-hidden="true">&gt;_</span>
				<h1 class="text-lg font-semibold text-fg">users</h1>
				<span class="cursor" aria-hidden="true"></span>
			</div>
			<p class="mt-1 text-sm text-fg-muted">
				Manage accounts: unlock locked-out users, deactivate strays, send invites.
				<a href="/" class="text-accent underline hover:text-accent-hover">back to surgite ❯</a>
			</p>
			<div class="mt-1 border-b border-dashed border-border-subtle"></div>
		</div>

		{#if me === null}
			<p class="px-4 pb-6 text-sm text-err sm:px-6">
				You need to <a href="/login" class="underline">log in</a> to view this page.
			</p>
		{:else if !me.is_admin}
			<div class="px-4 pb-6 sm:px-6">
				<p class="text-sm text-err">403 — admin only.</p>
				<a href="/" class="mt-3 inline-block text-sm text-accent underline">back to surgite ❯</a>
			</div>
		{:else}
			<div class="px-4 pb-6 sm:px-6">
				<div class="mt-2 flex flex-wrap items-center justify-between gap-2">
					<span class="text-sm text-fg-muted">
						<span class="text-accent">~/users</span> <span aria-hidden="true">❯</span>
						<span class="text-fg-faint">{total} user{total !== 1 ? 's' : ''}</span>
					</span>
					<button
						onclick={() => (inviteOpen = !inviteOpen)}
						class="border border-border bg-surface px-3 py-1.5 text-sm text-fg transition hover:bg-surface-2"
					>
						<span class="text-accent">❯</span> {inviteOpen ? 'cancel invite' : 'invite user'}
					</button>
				</div>

				{#if inviteOpen}
					<form
						onsubmit={sendInvite}
						class="mt-3 flex flex-col gap-2 border border-border bg-bg px-3 py-3"
					>
						<label class="flex flex-col gap-1 text-xs">
							<span class="text-fg-muted">
								<span class="text-accent">❯</span> email
								<span class="text-fg-faint">(optional — leave blank for open invite)</span>
							</span>
							<input
								type="email"
								bind:value={inviteEmail}
								placeholder="user@example.com"
								class="border border-border bg-surface px-2 py-1.5 text-sm text-fg placeholder:text-fg-faint"
							/>
						</label>
						<label class="flex flex-col gap-1 text-xs">
							<span class="text-fg-muted"><span class="text-accent">❯</span> role</span>
							<select
								bind:value={inviteRole}
								class="border border-border bg-surface px-2 py-1.5 text-sm text-fg"
							>
								<option value="user">user</option>
								<option value="admin">admin</option>
							</select>
						</label>
						<label class="flex flex-col gap-1 text-xs">
							<span class="text-fg-muted">
								<span class="text-accent">❯</span> ttl
								<span class="text-fg-faint">(days, 1-90)</span>
							</span>
							<input
								type="number"
								bind:value={inviteTtl}
								min="1"
								max="90"
								required
								class="border border-border bg-surface px-2 py-1.5 text-sm text-fg"
							/>
						</label>
						{#if inviteError}
							<p class="text-xs text-err">{inviteError}</p>
						{/if}
						<button
							type="submit"
							disabled={inviting}
							class="self-start border border-border bg-surface px-3 py-1.5 text-sm text-fg transition hover:bg-surface-2 disabled:opacity-50"
						>
							{inviting ? 'minting…' : 'mint invite'}
						</button>
					</form>
				{/if}

				{#if lastInvite}
					<div class="mt-3 border border-accent bg-bg px-3 py-3">
						<p class="text-xs text-fg-muted">
							<span class="text-accent">[ ok ]</span> invite created — share the URL below
							(out-of-band; email delivery is a 0.6.0 follow-up).
						</p>
						<div class="mt-2 flex items-center gap-2">
							<code
								class="flex-1 select-all break-all border border-border bg-surface px-2 py-1.5 text-xs text-fg"
							>
								{inviteLink}
							</code>
							<button
								onclick={() => copy(inviteLink)}
								class="shrink-0 border border-border bg-surface px-2 py-1.5 text-xs text-fg transition hover:bg-surface-2"
							>
								copy
							</button>
							<button
								onclick={dismissInvite}
								class="shrink-0 px-2 py-1.5 text-xs text-fg-faint transition hover:text-fg"
							>
								dismiss
							</button>
						</div>
						{#if lastInvite.email}
							<p class="mt-2 text-xs text-fg-faint">pinned to {lastInvite.email}</p>
						{:else}
							<p class="mt-2 text-xs text-fg-faint">open invite — any email can claim it</p>
						{/if}
					</div>
				{/if}

				{#if loading}
					<div class="mt-3 border border-border bg-bg">
						<Skeleton rows={3} />
					</div>
				{:else if loadError}
					<p class="mt-3 text-sm text-err">{loadError}</p>
				{:else}
					<div class="mt-3 overflow-x-auto border border-border bg-bg">
						<table class="w-full min-w-[640px] text-sm">
							<thead class="border-b border-border-subtle text-left text-xs text-fg-muted">
								<tr>
									<th class="px-3 py-2 font-normal">email</th>
									<th class="px-3 py-2 font-normal">name</th>
									<th class="px-3 py-2 font-normal">admin</th>
									<th class="px-3 py-2 font-normal">active</th>
									<th class="px-3 py-2 font-normal">last login</th>
									<th class="px-3 py-2 font-normal">fails</th>
									<th class="px-3 py-2 font-normal">locked</th>
									<th class="px-3 py-2 text-right font-normal">actions</th>
								</tr>
							</thead>
							<tbody class="divide-y divide-border-subtle">
								{#each users as u (u.id)}
									<tr class="text-fg">
										<td class="px-3 py-2 font-medium">{u.email}</td>
										<td class="px-3 py-2 text-fg-muted">
											{u.display_name || '—'}
										</td>
										<td class="px-3 py-2">
											{#if u.is_admin}
												<span class="text-accent">yes</span>
											{:else}
												<span class="text-fg-faint">no</span>
											{/if}
										</td>
										<td class="px-3 py-2">
											{#if u.is_active}
												<span class="text-ok">yes</span>
											{:else}
												<span class="text-err">no</span>
											{/if}
										</td>
										<td class="px-3 py-2 text-xs text-fg-muted">
											{relativeTime(u.last_login_at)}
										</td>
										<td class="px-3 py-2 text-xs text-fg-muted">
											{u.failed_login_count > 0
												? u.failed_login_count
												: '—'}
										</td>
										<td class="px-3 py-2 text-xs text-fg-muted">
											{#if isLocked(u.locked_until)}
												<span class="text-warn">until {relativeTime(u.locked_until)}</span>
											{:else}
												—
											{/if}
										</td>
										<td class="px-3 py-2 text-right">
											<div class="flex justify-end gap-2 text-xs">
												{#if isLocked(u.locked_until)}
													<button
														onclick={() => act(u, unlockUser, 'unlocked', (row) => ({ ...row, locked_until: null, failed_login_count: 0 }))}
														class="text-accent transition hover:text-accent-hover"
													>
														unlock
													</button>
												{/if}
												{#if u.is_active && u.id !== me?.id}
													<button
														onclick={() => act(u, deactivateUser, 'deactivated', (row) => ({ ...row, is_active: false }))}
														class="text-fg-faint transition hover:text-err"
													>
														deactivate
													</button>
												{:else if !u.is_active}
													<button
														onclick={() => act(u, activateUser, 'activated', (row) => ({ ...row, is_active: true }))}
														class="text-ok transition hover:text-accent-hover"
													>
														activate
													</button>
												{/if}
											</div>
										</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				{/if}
			</div>
		{/if}
	</div>
</main>
