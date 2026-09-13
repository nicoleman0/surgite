import { redirect } from '@sveltejs/kit';
import { fetchCurrentUser, type CurrentUser } from '$lib/api';

export async function load({ url }: { url: URL }): Promise<{ user: CurrentUser }> {
	try {
		return { user: await fetchCurrentUser() };
	} catch (cause) {
		if ((cause as Error & { status?: number }).status !== 401) throw cause;
		const next = `${url.pathname}${url.search}${url.hash}`;
		const query = new URLSearchParams({ retry: '1', next });
		throw redirect(302, `/login?${query}`);
	}
}
