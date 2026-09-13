import { describe, expect, it } from 'vitest';

import { safeReturnPath } from './navigation';

describe('safeReturnPath', () => {
	it('preserves a same-origin path, query and fragment', () => {
		expect(safeReturnPath('/settings?repo_id=3#prompt', 'https://surgite.example')).toBe(
			'/settings?repo_id=3#prompt'
		);
	});

	it.each([
		'https://evil.example/settings',
		'//evil.example/settings',
		'http://[',
		'javascript:alert(1)',
		''
	])('rejects unsafe return path %s', (candidate) => {
		expect(safeReturnPath(candidate, 'https://surgite.example')).toBe('/');
	});
});
