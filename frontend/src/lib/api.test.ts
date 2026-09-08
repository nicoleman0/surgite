import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// The api module reads `import.meta.env` at top level. `VITE_API_BASE` is set
// before the dynamic import below so the import picks up our test URL.
import.meta.env.VITE_API_BASE = 'http://api.test';

// Dynamic import so the module evaluates with our env stubs in place.
const { ingestRepo, login, signup } = await import('./api');

const okBody = (body: unknown) =>
	({
		ok: true,
		status: 200,
		statusText: 'OK',
		json: () => Promise.resolve(body)
	}) as Response;

const errBody = (status: number, detail: unknown, headers: Record<string, string> = {}) =>
	({
		ok: false,
		status,
		statusText: 'Error',
		headers: new Headers(headers),
		json: () => Promise.resolve({ detail })
	}) as Response;

let fetchSpy: ReturnType<typeof vi.fn>;

beforeEach(() => {
	fetchSpy = vi.fn();
	vi.stubGlobal('fetch', fetchSpy);
});

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('request() — CSRF header', () => {
	it('sends X-Requested-With on POST', async () => {
		fetchSpy.mockResolvedValueOnce(okBody({ id: 'u1', email: 'a@b.c' }));
		await login('a@b.c', 'pw');
		const init = fetchSpy.mock.calls[0][1] as RequestInit;
		const headers = init.headers as Record<string, string>;
		expect(headers['X-Requested-With']).toBe('surgite-web');
		expect(headers['Content-Type']).toBe('application/json');
	});

	it('does not send X-Requested-With on GET', async () => {
		const { fetchCurrentUser } = await import('./api');
		fetchSpy.mockResolvedValueOnce(okBody({ id: 'u1' }));
		await fetchCurrentUser();
		const init = fetchSpy.mock.calls[0][1] as RequestInit;
		const headers = init.headers as Record<string, string>;
		expect(headers['X-Requested-With']).toBeUndefined();
	});

	it('sends a CSRF-protected POST for manual repo sync', async () => {
		fetchSpy.mockResolvedValueOnce(okBody({ accepted: true }));
		await ingestRepo(42);
		const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('http://api.test/repos/42/ingest');
		expect(init.method).toBe('POST');
		expect((init.headers as Record<string, string>)['X-Requested-With']).toBe('surgite-web');
	});
});

describe('request() — error parsing', () => {
	it('throws with the detail string on a 4xx', async () => {
		fetchSpy.mockResolvedValueOnce(errBody(401, 'Invalid email or password'));
		await expect(login('a@b.c', 'pw')).rejects.toThrow('Invalid email or password');
	});

	it('falls back to statusText when body is not JSON', async () => {
		fetchSpy.mockResolvedValueOnce({
			ok: false,
			status: 500,
			statusText: 'Server Error',
			headers: new Headers(),
			json: () => Promise.reject(new Error('not json'))
		} as Response);
		await expect(login('a@b.c', 'pw')).rejects.toThrow('Server Error');
	});

	it('attaches lockoutSeconds on 423 from Retry-After header', async () => {
		fetchSpy.mockResolvedValueOnce(
			errBody(423, 'Account temporarily locked. Try again later.', {
				'Retry-After': '300'
			})
		);
		try {
			await login('a@b.c', 'pw');
			expect.fail('expected throw');
		} catch (e) {
			const err = e as Error & { lockoutSeconds?: number; status?: number };
			expect(err.lockoutSeconds).toBe(300);
			expect(err.status).toBe(423);
			expect(err.message).toContain('locked');
		}
	});

	it('omits lockoutSeconds on 423 without Retry-After', async () => {
		fetchSpy.mockResolvedValueOnce(errBody(423, 'locked'));
		try {
			await login('a@b.c', 'pw');
			expect.fail('expected throw');
		} catch (e) {
			expect((e as Error & { lockoutSeconds?: number }).lockoutSeconds).toBeUndefined();
		}
	});

	it('attaches status to the thrown Error on any 4xx/5xx', async () => {
		fetchSpy.mockResolvedValueOnce(errBody(401, 'Not authenticated'));
		try {
			await login('a@b.c', 'pw');
			expect.fail('expected throw');
		} catch (e) {
			expect((e as Error & { status?: number }).status).toBe(401);
		}
	});
});

describe('signup()', () => {
	it('POSTs to /auth/redeem-invite with the token', async () => {
		fetchSpy.mockResolvedValueOnce(okBody({ id: 'u1', email: 'a@b.c' }));
		await signup({ token: 'tok', password: 'pw', display_name: 'Alice' });
		const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('http://api.test/auth/redeem-invite');
		expect(init.method).toBe('POST');
		expect(JSON.parse(init.body as string)).toEqual({
			token: 'tok',
			password: 'pw',
			display_name: 'Alice'
		});
	});

	it('omits undefined email/display_name from the body', async () => {
		fetchSpy.mockResolvedValueOnce(okBody({ id: 'u1' }));
		await signup({ token: 'tok', password: 'pw' });
		const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
		expect(JSON.parse(init.body as string)).toEqual({ token: 'tok', password: 'pw' });
	});
});

describe('admin endpoints', () => {
	it('fetchAdminUsers GETs /admin/users with query params', async () => {
		const { fetchAdminUsers } = await import('./api');
		fetchSpy.mockResolvedValueOnce(okBody({ total: 0, users: [] }));
		await fetchAdminUsers({ limit: 10, q: 'ali' });
		const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('http://api.test/admin/users?limit=10&q=ali');
		expect(init.method).toBeUndefined();
		expect((init.headers as Record<string, string>)['X-Requested-With']).toBeUndefined();
	});

	it('POST endpoints send the CSRF header', async () => {
		const { unlockUser, deactivateUser, activateUser, createInvite } = await import('./api');
		fetchSpy.mockResolvedValue({ ok: true, status: 204, statusText: '', headers: new Headers() } as Response);
		await unlockUser('u1');
		await deactivateUser('u1');
		await activateUser('u1');
		fetchSpy.mockResolvedValueOnce(
			okBody({ id: 'i1', token: 'tok', email: null, role: 'user', expires_at: 'x' })
		);
		await createInvite({ role: 'user', ttl_days: 7 });
		for (let i = 0; i < 4; i++) {
			const init = fetchSpy.mock.calls[i][1] as RequestInit;
			expect((init.headers as Record<string, string>)['X-Requested-With']).toBe('surgite-web');
		}
	});

	it('createInvite omits empty email and serialises role/ttl', async () => {
		const { createInvite } = await import('./api');
		fetchSpy.mockResolvedValueOnce(okBody({ id: 'i1', token: 'tok' }));
		await createInvite({ role: 'admin', ttl_days: 14 });
		const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('http://api.test/admin/invites');
		expect(JSON.parse(init.body as string)).toEqual({ role: 'admin', ttl_days: 14 });
	});
});

describe('fetchMySummaries', () => {
	it('GETs /summaries/mine with no query params by default', async () => {
		const { fetchMySummaries } = await import('./api');
		fetchSpy.mockResolvedValueOnce(okBody({ total: 0, summaries: [] }));
		const out = await fetchMySummaries();
		const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('http://api.test/summaries/mine');
		expect(init.method).toBeUndefined();
		expect(out.total).toBe(0);
	});

	it('passes limit and offset as query params when provided', async () => {
		const { fetchMySummaries } = await import('./api');
		fetchSpy.mockResolvedValueOnce(okBody({ total: 5, summaries: [] }));
		await fetchMySummaries({ limit: 10, offset: 20 });
		const [url] = fetchSpy.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('http://api.test/summaries/mine?limit=10&offset=20');
	});
});

describe('logout', () => {
	it('POSTs to /auth/logout and returns void on 204', async () => {
		const { logout } = await import('./api');
		fetchSpy.mockResolvedValueOnce({
			ok: true,
			status: 204,
			statusText: '',
			headers: new Headers()
		} as Response);
		await expect(logout()).resolves.toBeUndefined();
		const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('http://api.test/auth/logout');
		expect(init.method).toBe('POST');
		const headers = init.headers as Record<string, string>;
		expect(headers['X-Requested-With']).toBe('surgite-web');
	});
});

describe('resetPassword', () => {
	it('POSTs to /auth/password-reset/confirm with token + new_password', async () => {
		const { resetPassword } = await import('./api');
		fetchSpy.mockResolvedValueOnce({
			ok: true,
			status: 204,
			statusText: '',
			headers: new Headers()
		} as Response);
		await expect(resetPassword('tok', 'newpw1234')).resolves.toBeUndefined();
		const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
		expect(url).toBe('http://api.test/auth/password-reset/confirm');
		expect(init.method).toBe('POST');
		expect(JSON.parse(init.body as string)).toEqual({
			token: 'tok',
			new_password: 'newpw1234'
		});
		const headers = init.headers as Record<string, string>;
		expect(headers['X-Requested-With']).toBe('surgite-web');
	});

	it('attaches status=400 on an expired token', async () => {
		const { resetPassword } = await import('./api');
		fetchSpy.mockResolvedValueOnce(errBody(400, 'Invalid or expired reset token'));
		try {
			await resetPassword('tok', 'newpw1234');
			expect.fail('expected throw');
		} catch (e) {
			expect((e as Error & { status?: number }).status).toBe(400);
		}
	});
});
