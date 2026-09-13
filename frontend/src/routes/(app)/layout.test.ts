import { beforeEach, describe, expect, it, vi } from 'vitest';

const fetchCurrentUser = vi.fn();
const redirect = vi.fn((status: number, location: string) => {
	const err = new Error(`Redirect to ${location}`) as Error & { __redirect?: unknown };
	err.__redirect = { status, location };
	throw err;
});

vi.mock('$lib/api', () => ({ fetchCurrentUser }));
vi.mock('@sveltejs/kit', () => ({ redirect }));

const { load } = await import('./+layout');

describe('authenticated layout', () => {
	beforeEach(() => {
		fetchCurrentUser.mockReset();
		redirect.mockClear();
	});

	it('returns the current user', async () => {
		const user = { id: 'u1', email: 'a@b.c', display_name: 'A', is_admin: false };
		fetchCurrentUser.mockResolvedValueOnce(user);

		await expect(load({ url: new URL('https://surgite.example/settings') })).resolves.toEqual({
			user
		});
	});

	it('preserves the protected destination when authentication is required', async () => {
		const error = new Error('Not authenticated') as Error & { status?: number };
		error.status = 401;
		fetchCurrentUser.mockRejectedValueOnce(error);

		await expect(
			load({ url: new URL('https://surgite.example/settings?repo_id=3#prompt') })
		).rejects.toThrow(/Redirect/);
		expect(redirect).toHaveBeenCalledWith(
			302,
			'/login?retry=1&next=%2Fsettings%3Frepo_id%3D3%23prompt'
		);
	});

	it('does not turn service failures into login redirects', async () => {
		const error = new Error('Database unavailable') as Error & { status?: number };
		error.status = 503;
		fetchCurrentUser.mockRejectedValueOnce(error);

		await expect(load({ url: new URL('https://surgite.example/admin') })).rejects.toBe(error);
		expect(redirect).not.toHaveBeenCalled();
	});
});
