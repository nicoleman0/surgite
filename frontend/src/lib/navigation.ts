export function safeReturnPath(candidate: string | null, origin: string): string {
	if (!candidate?.startsWith('/') || candidate.startsWith('//')) return '/';
	try {
		const base = new URL(origin);
		const resolved = new URL(candidate, base);
		return resolved.origin === base.origin
			? `${resolved.pathname}${resolved.search}${resolved.hash}`
			: '/';
	} catch {
		return '/';
	}
}
