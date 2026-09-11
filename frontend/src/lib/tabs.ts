export function nextTab<T extends string>(current: T, key: string, tabs: readonly T[]): T | null {
	const index = tabs.indexOf(current);
	if (key === 'ArrowLeft') return tabs[(index - 1 + tabs.length) % tabs.length] ?? null;
	if (key === 'ArrowRight') return tabs[(index + 1) % tabs.length] ?? null;
	if (key === 'Home') return tabs[0] ?? null;
	if (key === 'End') return tabs.at(-1) ?? null;
	return null;
}
