<script lang="ts">
	let { onclose }: { onclose: () => void } = $props();
	let dialog: HTMLDialogElement;

	// showModal() is the whole accessibility contract: focus moves in, Tab is
	// trapped, the rest of the page goes inert, and focus returns to whatever
	// was focused before. Hand-rolling that runs ~40 lines and still leaks
	// focus backwards off the first element.
	$effect(() => {
		dialog.showModal();
	});

	function close() {
		// close() has to run while the node is still connected -- that is what
		// restores focus to whatever opened the dialog. Svelte detaches the
		// node before effect teardown, so closing from a cleanup restores
		// nothing and focus lands on <body>.
		dialog.close();
		onclose();
	}

	function handleKey(e: KeyboardEvent) {
		if (e.key !== 'Escape' && e.key !== 'q' && e.key !== 'F1') return;
		// +page.svelte toggles help from a window listener. Stop these there,
		// or closing on F1 would immediately reopen.
		e.stopPropagation();
		e.preventDefault();
		close();
	}

	// A click on the ::backdrop targets the dialog itself. The panel padding
	// lives on the inner div so it cannot be mistaken for the backdrop.
	function handleClick(e: MouseEvent) {
		if (e.target === dialog) close();
	}
</script>

<dialog
	bind:this={dialog}
	onclose={onclose}
	onkeydown={handleKey}
	onclick={handleClick}
	aria-label="Help"
	class="m-auto max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] max-w-lg overflow-y-auto border border-border bg-surface p-0 text-sm text-fg backdrop:bg-bg/80"
>
	<div class="p-4 sm:p-6">
		<div class="mb-4 flex items-center justify-between">
			<h2 class="text-base font-semibold text-fg">:help</h2>
			<button onclick={close} class="text-fg-muted hover:text-fg" aria-label="Close help">✕</button>
		</div>
		<div class="space-y-3 text-fg-muted">
			<p><span class="text-fg">surgite</span> generates standup summaries from your git commit history.</p>
			<div>
				<p class="text-fg">Commands:</p>
				<p>  <span class="text-accent">❯ add-repo</span>        — register a git repository</p>
				<p>  <span class="text-accent">❯ generate</span>        — fetch latest commits and summarize the selected range</p>
			</div>
			<div>
				<p class="text-fg">Keyboard:</p>
				<p>  <span class="text-accent">F1</span>               — toggle this help</p>
				<p>  <span class="text-accent">↑↑↓↓←→←→BA</span>    — ???</p>
			</div>
			<p class="text-fg-faint">Press <span class="text-fg-muted">Esc</span> or <span class="text-fg-muted">q</span> to close.</p>
		</div>
	</div>
</dialog>
