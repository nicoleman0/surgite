import { listConnections, listRepos, type GitConnection, type Repo, type RepoList } from './api';
import { RepoSyncManager } from './repo-sync';

export const WORKSPACE_CONTEXT = Symbol('workspace');

export class WorkspaceState {
	repos = $state<Repo[]>([]);
	staleAfterSeconds = $state<number | null>(null);
	connections = $state<GitConnection[]>([]);
	reposLoading = $state(true);
	connectionsLoading = $state(true);
	reposError = $state<string | null>(null);
	connectionsError = $state<string | null>(null);
	syncingIds = $state(new Set<number>());

	private readonly syncManager = new RepoSyncManager(
		(data) => this.applyRepoData(data),
		(ids) => (this.syncingIds = ids)
	);

	async load(): Promise<void> {
		await Promise.all([this.reloadRepos(), this.reloadConnections()]);
	}

	async reloadRepos(): Promise<void> {
		this.reposLoading = true;
		this.reposError = null;
		try {
			this.applyRepoData(await listRepos());
		} catch (cause) {
			this.reposError = cause instanceof Error ? cause.message : 'Failed to load repos';
		} finally {
			this.reposLoading = false;
		}
	}

	async reloadConnections(): Promise<void> {
		this.connectionsLoading = true;
		this.connectionsError = null;
		try {
			this.connections = await listConnections();
		} catch (cause) {
			this.connectionsError =
				cause instanceof Error ? cause.message : 'Failed to load connections';
		} finally {
			this.connectionsLoading = false;
		}
	}

	sync(repos: Repo[]): void {
		this.syncManager.sync(repos);
	}

	trackInitialIngest(repo: Repo): void {
		this.syncManager.trackInitialIngest(repo);
	}

	destroy(): void {
		this.syncManager.destroy();
	}

	private applyRepoData(data: RepoList): void {
		this.repos = data.repos;
		this.staleAfterSeconds = data.stale_after_seconds;
	}
}
