<script lang="ts">
	import { onMount, setContext } from 'svelte';
	import HelpOverlay from '$lib/components/HelpOverlay.svelte';
	import { WORKSPACE_CONTEXT, WorkspaceState } from '$lib/workspace.svelte';

	let { children } = $props();
	let showHelp = $state(false);
	const workspace = new WorkspaceState();
	setContext(WORKSPACE_CONTEXT, workspace);

	onMount(() => {
		void workspace.load();
		return () => workspace.destroy();
	});

	const KONAMI = [
		'ArrowUp',
		'ArrowUp',
		'ArrowDown',
		'ArrowDown',
		'ArrowLeft',
		'ArrowRight',
		'ArrowLeft',
		'ArrowRight',
		'b',
		'a'
	];
	let konamiIndex = 0;

	function handleKey(event: KeyboardEvent) {
		if (event.key === 'F1') {
			event.preventDefault();
			showHelp = !showHelp;
			return;
		}
		if (event.key === KONAMI[konamiIndex]) {
			konamiIndex += 1;
			if (konamiIndex === KONAMI.length) {
				konamiIndex = 0;
				document.documentElement.classList.toggle('crt');
			}
		} else {
			konamiIndex = 0;
		}
	}
</script>

<svelte:window onkeydown={handleKey} />
{@render children()}
{#if showHelp}<HelpOverlay onclose={() => (showHelp = false)} />{/if}
