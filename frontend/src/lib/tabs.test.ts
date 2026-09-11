import { describe, expect, it } from 'vitest';

import { nextTab } from './tabs';

describe('nextTab', () => {
	const tabs = ['mine', 'shared'] as const;

	it('wraps arrow navigation and supports Home and End', () => {
		expect(nextTab('mine', 'ArrowLeft', tabs)).toBe('shared');
		expect(nextTab('mine', 'ArrowRight', tabs)).toBe('shared');
		expect(nextTab('shared', 'ArrowRight', tabs)).toBe('mine');
		expect(nextTab('shared', 'Home', tabs)).toBe('mine');
		expect(nextTab('mine', 'End', tabs)).toBe('shared');
	});

	it('leaves unrelated keys alone', () => {
		expect(nextTab('mine', 'Enter', tabs)).toBeNull();
	});
});
