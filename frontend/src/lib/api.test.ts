import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// The api module reads `import.meta.env` at top level. `VITE_API_BASE` is set
// before the dynamic import below so the import picks up our test URL.
import.meta.env.VITE_API_BASE = 'http://api.test';

// Dynamic import so the module evaluates with our env stubs in place.
const { clearProviderKey, fetchProviderKeys, ingestRepo, login, setProviderKey, signup, streamSummary } =
	await import('./api');

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

const encoder = new TextEncoder();

const streamResponse = (...chunks: string[]) =>
	new Response(
		new ReadableStream<Uint8Array>({
			start(controller) {
				for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
				controller.close();
			}
		})
	);

const byteStreamResponse = (...chunks: Uint8Array[]) =>
	new Response(
		new ReadableStream<Uint8Array>({
			start(controller) {
				for (const chunk of chunks) controller.enqueue(chunk);
				controller.close();
			}
		})
	);

const handlers = (calls: unknown[][]) => ({
	onMeta: (meta: unknown) => calls.push(['meta', meta]),
	onDelta: (repo: string, text: string) => calls.push(['delta', repo, text]),
	onRepoDone: (repo: string, provider: string, model: string) =>
		calls.push(['repo_done', repo, provider, model]),
	onRepoError: (repo: string, detail: string) => calls.push(['repo_error', repo, detail])
});

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


describe('provider keys', () => {
	const listBody = { providers: ['groq', 'local'], default: 'groq', keys: [] };

	it('lists without a CSRF header', async () => {
		fetchSpy.mockResolvedValueOnce(okBody(listBody));
		await fetchProviderKeys();
		const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit | undefined];
		expect(url).toBe('http://api.test/settings/provider-keys');
		expect(init?.method).toBeUndefined();
		expect((init?.headers as Record<string, string>)['X-Requested-With']).toBeUndefined();
	});

	it('sets a key with a PUT carrying only provider and key', async () => {
		fetchSpy.mockResolvedValueOnce(okBody({ configured: true }));
		await setProviderKey('groq', 'gsk_x');
		const init = fetchSpy.mock.calls[0][1] as RequestInit;
		expect(init.method).toBe('PUT');
		expect((init.headers as Record<string, string>)['X-Requested-With']).toBe('surgite-web');
		expect(JSON.parse(init.body as string)).toEqual({ provider: 'groq', key: 'gsk_x' });
	});

	it('clears a key without putting key material on the wire', async () => {
		fetchSpy.mockResolvedValueOnce(okBody({ configured: false }));
		await clearProviderKey('groq');
		const body = JSON.parse((fetchSpy.mock.calls[0][1] as RequestInit).body as string);
		expect(body).toEqual({ provider: 'groq', clear: true });
		expect('key' in body).toBe(false);
	});

	it('surfaces the off/single_user 404 as err.status', async () => {
		fetchSpy.mockResolvedValueOnce(errBody(404, 'Not found'));
		await expect(fetchProviderKeys()).rejects.toMatchObject({ status: 404 });
	});
});

describe('streamSummary()', () => {
	const meta = {
		period: { since: '2026-09-01', until: '2026-09-02' },
		total_commits: 1,
		by_repo: { repo: 1 },
		by_day: { '2026-09-01': 1 },
		source_synced_at: { repo: null },
		repos: ['repo'],
		provider: 'local',
		model: 'test-model'
	};

	const events = (newline: string) =>
		[
			`: keepalive${newline}${newline}`,
			`event: meta${newline}data: ${JSON.stringify(meta)}${newline}${newline}`,
			`event: delta${newline}data: {"repo":"repo","text":"hello"}${newline}${newline}`,
			`event: repo_done${newline}data: {"repo":"repo","provider":"local","model":"test-model"}${newline}${newline}`,
			`event: repo_error${newline}data: {"repo":"other","detail":"failed"}${newline}${newline}`
		].join('');

	it.each(['\n', '\r\n', '\r'])('parses %j-delimited events in order', async (newline) => {
		fetchSpy.mockResolvedValueOnce(streamResponse(events(newline)));
		const calls: unknown[][] = [];

		await streamSummary({}, handlers(calls));

		expect(calls).toEqual([
			['meta', meta],
			['delta', 'repo', 'hello'],
			['repo_done', 'repo', 'local', 'test-model'],
			['repo_error', 'other', 'failed']
		]);
	});

	it('handles byte-by-byte CRLF chunks and split UTF-8 text', async () => {
		const source = `event: delta\r\ndata: {"repo":"repo","text":"£"}\r\n\r\n`;
		const bytes = encoder.encode(source);
		fetchSpy.mockResolvedValueOnce(byteStreamResponse(...Array.from(bytes, (byte) => new Uint8Array([byte]))));
		const calls: unknown[][] = [];

		await streamSummary({}, handlers(calls));

		expect(calls).toEqual([['delta', 'repo', '£']]);
	});

	it('joins multiline data fields with a newline', async () => {
		const parse = vi.spyOn(JSON, 'parse');
		fetchSpy.mockResolvedValueOnce(
			streamResponse('event: delta\ndata: {"repo":"repo",\ndata: "text":"hello"}\n\n')
		);
		const calls: unknown[][] = [];

		await streamSummary({}, handlers(calls));

		expect(parse).toHaveBeenCalledWith('{"repo":"repo",\n"text":"hello"}');
		expect(calls).toEqual([['delta', 'repo', 'hello']]);
		parse.mockRestore();
	});

	it('ignores empty and comment-only frames and discards an incomplete final event', async () => {
		fetchSpy.mockResolvedValueOnce(
			streamResponse('\n: keepalive\n\nevent: delta\ndata: {"repo":"repo","text":"complete"}\n\nevent: delta\ndata: {"repo":"repo","text":"discarded"}')
		);
		const calls: unknown[][] = [];

		await streamSummary({}, handlers(calls));

		expect(calls).toEqual([['delta', 'repo', 'complete']]);
	});

	it('rejects malformed JSON from a CR-delimited event', async () => {
		fetchSpy.mockResolvedValueOnce(streamResponse('event: delta\rdata: not-json\r\r'));

		await expect(streamSummary({}, handlers([]))).rejects.toThrow(SyntaxError);
	});

	it('forwards abort signals and propagates AbortError', async () => {
		const controller = new AbortController();
		fetchSpy.mockImplementationOnce((_url: string, init: RequestInit) =>
			new Promise((_, reject) =>
				(init.signal as AbortSignal).addEventListener('abort', () => reject(init.signal?.reason))
			)
		);
		const promise = streamSummary({}, handlers([]), controller.signal);
		controller.abort(new DOMException('Aborted', 'AbortError'));

		await expect(promise).rejects.toMatchObject({ name: 'AbortError' });
		expect((fetchSpy.mock.calls[0][1] as RequestInit).signal).toBe(controller.signal);
	});

	it('keeps HTTP error details', async () => {
		fetchSpy.mockResolvedValueOnce(errBody(502, 'Provider unavailable'));

		await expect(streamSummary({}, handlers([]))).rejects.toThrow('Provider unavailable');
	});
});
