// Development uses Vite's separate origin; production is same-origin.
const BASE = import.meta.env.VITE_API_BASE ?? (import.meta.env.DEV ? 'http://localhost:8000' : '');

export interface Repo {
	id: number;
	name: string;
	clone_url: string;
	added_at: string | null;
	last_ingested_at: string | null;
	last_ingest_attempt_at: string | null;
	last_ingest_error: string | null;
	connection_id?: string | null;
}

export interface GitConnection {
	id: string;
	name: string;
	kind: 'token' | 'github';
	host: string;
	status: 'connected' | 'disconnected';
	created_at: string | null;
	updated_at: string | null;
	affected_repositories: number;
}

export interface RepoList {
	repos: Repo[];
	stale_after_seconds: number | null;
}

export interface Commit {
	hash: string;
	short_hash: string;
	date: string;
	author: string;
	message: string;
	repo: string;
	ingested_at: string | null;
}

export interface Summary {
	period: { since: string | null; until: string | null };
	total_commits: number;
	by_repo: Record<string, number>;
	by_day: Record<string, number>;
	source_synced_at: Record<string, string | null>;
	commits: Commit[];
	log_by_repo: Record<string, string> | null;
	ai_summary: string | null;
	ai_provider: string | null;
	ai_model: string | null;
	ai_summaries: Record<string, { summary: string; provider: string; model: string }> | null;
}

const UNSAFE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
const CSRF_HEADER = 'X-Requested-With';
const CSRF_VALUE = 'surgite-web';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
	const method = (init?.method ?? 'GET').toUpperCase();
	const headers: Record<string, string> = {
		'Content-Type': 'application/json',
		...(init?.headers as Record<string, string> | undefined)
	};
	if (UNSAFE_METHODS.has(method)) headers[CSRF_HEADER] = CSRF_VALUE;
	const res = await fetch(`${BASE}${path}`, { ...init, headers });
	if (!res.ok) {
		let detail: unknown = res.statusText;
		try {
			detail = (await res.json()).detail ?? res.statusText;
		} catch {
			/* non-JSON body */
		}
		const message = typeof detail === 'string' ? detail : JSON.stringify(detail);
		const err = new Error(message) as Error & { status?: number; lockoutSeconds?: number };
		err.status = res.status;
		if (res.status === 423) {
			const retryAfter = Number(res.headers.get('Retry-After'));
			if (Number.isFinite(retryAfter) && retryAfter > 0) err.lockoutSeconds = retryAfter;
		}
		throw err;
	}
	return res.status === 204 ? (undefined as T) : res.json();
}

// --- auth (multi_user) -----------------------------------------------------

export interface CurrentUser {
	id: string;
	email: string;
	display_name: string;
	is_admin: boolean;
}

export const fetchCurrentUser = () => request<CurrentUser>('/auth/me');

export const login = (email: string, password: string) =>
	request<CurrentUser>('/auth/login', {
		method: 'POST',
		body: JSON.stringify({ email, password })
	});

export interface SignupRequest {
	token: string;
	password: string;
	email?: string;
	display_name?: string;
}

export const signup = (req: SignupRequest) =>
	request<CurrentUser>('/auth/redeem-invite', {
		method: 'POST',
		body: JSON.stringify(req)
	});

export const logout = () => request<void>('/auth/logout', { method: 'POST' });

export const resetPassword = (token: string, newPassword: string) =>
	request<void>('/auth/password-reset/confirm', {
		method: 'POST',
		body: JSON.stringify({ token, new_password: newPassword })
	});

export const listRepos = (signal?: AbortSignal) => request<RepoList>('/repos', { signal });

export const addRepo = (url: string, connection_id?: string | null) =>
	request<Repo>('/repos', { method: 'POST', body: JSON.stringify({ url, connection_id }) });

export const deleteRepo = (id: number) => request<void>(`/repos/${id}`, { method: 'DELETE' });

export const ingestRepo = (id: number) =>
	request<{ accepted: boolean }>(`/repos/${id}/ingest`, { method: 'POST' });

export const listConnections = () => request<GitConnection[]>('/connections');

export const createTokenConnection = (connection: { name: string; origin: string; username: string; token: string }) =>
	request<GitConnection>('/connections', { method: 'POST', body: JSON.stringify(connection) });

export const disconnectConnection = (id: string) => request<void>(`/connections/${id}`, { method: 'DELETE' });

export const githubConnectionStart = () => request<{ url: string }>('/connections/github/start');

export const listGithubRepositories = (id: string) =>
	request<{ repositories: { name: string; url: string }[] }>(`/connections/${id}/repositories`);

export const setRepoConnection = (repoId: number, connection_id: string | null) =>
	request<Repo>(`/repos/${repoId}/connection`, { method: 'PUT', body: JSON.stringify({ connection_id }) });

export interface SummaryParams {
	repo?: string;
	since?: string;
	until?: string;
	author?: string;
	ai?: boolean;
	provider?: string;
}

export interface ProviderInfo {
	name: string;
	model: string;
	available: boolean;
	default: boolean;
}

export interface ProvidersResponse {
	default: string;
	providers: ProviderInfo[];
}

export function generateSummary(params: SummaryParams = {}, signal?: AbortSignal) {
	const q = new URLSearchParams();
	if (params.repo) q.set('repo', params.repo);
	if (params.since) q.set('since', params.since);
	if (params.until) q.set('until', params.until);
	if (params.author) q.set('author', params.author);
	if (params.ai) q.set('ai', 'true');
	if (params.provider) q.set('provider', params.provider);
	const qs = q.toString();
	return request<Summary>(`/summary${qs ? `?${qs}` : ''}`, { signal });
}

export const fetchProviders = () =>
	request<ProvidersResponse>('/providers');

// --- admin -----------------------------------------------------------------

export interface AdminUser {
	id: string;
	email: string;
	display_name: string;
	is_active: boolean;
	is_admin: boolean;
	created_at: string | null;
	last_login_at: string | null;
	failed_login_count: number;
	locked_until: string | null;
}

export interface AdminInviteRequest {
	email?: string;
	role: 'user' | 'admin';
	ttl_days: number;
}

export interface AdminInviteResponse {
	id: string;
	token: string;
	email: string | null;
	role: string;
	expires_at: string;
}

export function fetchAdminUsers(params: { limit?: number; offset?: number; q?: string } = {}) {
	const q = new URLSearchParams();
	if (params.limit != null) q.set('limit', String(params.limit));
	if (params.offset) q.set('offset', String(params.offset));
	if (params.q) q.set('q', params.q);
	const qs = q.toString();
	return request<{ total: number; users: AdminUser[] }>(`/admin/users${qs ? `?${qs}` : ''}`);
}

export const unlockUser = (id: string) =>
	request<void>(`/admin/users/${id}/unlock`, { method: 'POST' });

export const deactivateUser = (id: string) =>
	request<void>(`/admin/users/${id}/deactivate`, { method: 'POST' });

export const activateUser = (id: string) =>
	request<void>(`/admin/users/${id}/activate`, { method: 'POST' });

export const createInvite = (req: AdminInviteRequest) =>
	request<AdminInviteResponse>('/admin/invites', {
		method: 'POST',
		body: JSON.stringify(req)
	});

export interface PromptSettings {
	repo_id: number | null;
	user_name: string;
	user_role: string;
	tone: string;
	group_count: string;
	output_format: string;
	custom_instructions: string;
	updated_at: string | null;
}

export type PromptSettingsUpdate = Partial<Omit<PromptSettings, 'updated_at' | 'repo_id'>>;

const repoQuery = (repoId?: number | null) =>
	repoId == null ? '' : `?repo_id=${repoId}`;

export const fetchPromptSettings = (repoId?: number | null) =>
	request<PromptSettings>(`/settings/prompt${repoQuery(repoId)}`);

export const updatePromptSettings = (update: PromptSettingsUpdate, repoId?: number | null) =>
	request<PromptSettings>(`/settings/prompt${repoQuery(repoId)}`, {
		method: 'PUT',
		body: JSON.stringify(update)
	});

// --- per-user provider keys (multi_user only) ------------------------------

export interface ProviderKey {
	provider: string;
	created_at: string | null;
	revoked_at: string | null;
}

export interface ProviderKeysResponse {
	providers: string[];
	default: string;
	keys: ProviderKey[];
}

export const fetchProviderKeys = () => request<ProviderKeysResponse>('/settings/provider-keys');

export const setProviderKey = (provider: string, key: string) =>
	request<{ configured: boolean }>('/settings/provider-keys', {
		method: 'PUT',
		body: JSON.stringify({ provider, key })
	});

export const clearProviderKey = (provider: string) =>
	request<{ configured: boolean }>('/settings/provider-keys', {
		method: 'PUT',
		body: JSON.stringify({ provider, clear: true })
	});

// --- shareable summary links ---

export interface ShareResponse {
	slug: string;
	expires_at: string;
}

export interface SharedSummary {
	slug: string;
	params: SummaryParams;
	created_at: string | null;
	expires_at: string | null;
}

export const createShare = (params: SummaryParams) =>
	request<ShareResponse>('/summaries', { method: 'POST', body: JSON.stringify(params) });

export const fetchShare = (slug: string) =>
	request<SharedSummary>(`/summaries/${encodeURIComponent(slug)}`);

export interface MySummary {
	slug: string;
	params: SummaryParams;
	created_at: string | null;
	expires_at: string | null;
}

export function fetchMySummaries(params: { limit?: number; offset?: number } = {}) {
	const q = new URLSearchParams();
	if (params.limit != null) q.set('limit', String(params.limit));
	if (params.offset) q.set('offset', String(params.offset));
	const qs = q.toString();
	return request<{ total: number; summaries: MySummary[] }>(
		`/summaries/mine${qs ? `?${qs}` : ''}`
	);
}

// --- streaming AI summaries (SSE) ---

export interface StreamMeta {
	period: { since: string | null; until: string | null };
	total_commits: number;
	by_repo: Record<string, number>;
	by_day: Record<string, number>;
	source_synced_at: Record<string, string | null>;
	repos: string[];
	provider: string;
	model: string;
}

export interface StreamHandlers {
	onMeta: (meta: StreamMeta) => void;
	onDelta: (repo: string, text: string) => void;
	onRepoDone: (repo: string, provider: string, model: string) => void;
	onRepoError: (repo: string, detail: string) => void;
}

function summaryQuery(params: SummaryParams): string {
	const q = new URLSearchParams();
	if (params.repo) q.set('repo', params.repo);
	if (params.since) q.set('since', params.since);
	if (params.until) q.set('until', params.until);
	if (params.author) q.set('author', params.author);
	if (params.provider) q.set('provider', params.provider);
	return q.toString();
}

/** Stream an AI summary, dispatching server-sent events as they arrive. */
export async function streamSummary(
	params: SummaryParams,
	handlers: StreamHandlers,
	signal?: AbortSignal
): Promise<void> {
	const qs = summaryQuery(params);
	const res = await fetch(`${BASE}/summary/stream${qs ? `?${qs}` : ''}`, {
		headers: { Accept: 'text/event-stream' },
		signal
	});
	if (!res.ok || !res.body) {
		let detail: unknown = res.statusText;
		try {
			detail = (await res.json()).detail ?? res.statusText;
		} catch {
			/* non-JSON body */
		}
		throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
	}

	const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
	let buffer = '';
	let event = 'message';
	let data: string[] = [];
	const processLine = (line: string) => {
		if (!line) {
			dispatchEvent(event, data, handlers);
			event = 'message';
			data = [];
			return;
		}
		if (line.startsWith(':')) return;
		const colon = line.indexOf(':');
		const field = colon === -1 ? line : line.slice(0, colon);
		let value = colon === -1 ? '' : line.slice(colon + 1);
		if (value.startsWith(' ')) value = value.slice(1);
		if (field === 'event') event = value;
		else if (field === 'data') data.push(value);
	};
	for (;;) {
		const { value, done } = await reader.read();
		if (done) break;
		buffer += value;
		let line: string | undefined;
		while ((line = nextLine(buffer)) !== undefined) {
			buffer = buffer.slice(line.length + lineEndingLength(buffer, line.length));
			processLine(line);
		}
	}
	let line: string | undefined;
	while ((line = nextLine(buffer, true)) !== undefined) {
		buffer = buffer.slice(line.length + lineEndingLength(buffer, line.length));
		processLine(line);
	}
}

function nextLine(buffer: string, final = false): string | undefined {
	for (let index = 0; index < buffer.length; index++) {
		if (buffer[index] === '\n') return buffer.slice(0, index);
		if (buffer[index] === '\r' && (final || index + 1 < buffer.length)) return buffer.slice(0, index);
	}
}

function lineEndingLength(buffer: string, lineLength: number): number {
	return buffer[lineLength] === '\r' && buffer[lineLength + 1] === '\n' ? 2 : 1;
}

function dispatchEvent(event: string, data: string[], handlers: StreamHandlers): void {
	if (!data.length) return;
	const payload = JSON.parse(data.join('\n'));
	if (event === 'meta') handlers.onMeta(payload);
	else if (event === 'delta') handlers.onDelta(payload.repo, payload.text);
	else if (event === 'repo_done') handlers.onRepoDone(payload.repo, payload.provider, payload.model);
	else if (event === 'repo_error') handlers.onRepoError(payload.repo, payload.detail);
}
