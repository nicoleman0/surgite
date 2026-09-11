"""Background repository-ingest tests."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest

from surgite import api
from surgite.db import CommitRow, RepoRow, get_session
from surgite.models import Commit


@pytest.fixture
def fake_git(monkeypatch):
    """Provide a recording Git stub."""

    class Recorder:
        commits: list[Commit] = []
        ensured: list[str] = []
        calls: int = 0

    rec = Recorder()
    monkeypatch.setattr(
        "surgite.git.ensure_repo",
        lambda name, url, cache, timeout=120: rec.ensured.append(name) or f"/fake/{name}",
    )

    def fake_get_raw_log(*a, **kw):
        rec.calls += 1
        return ""

    monkeypatch.setattr(api, "get_raw_log", fake_get_raw_log)
    monkeypatch.setattr(api, "parse_log", lambda raw: list(rec.commits))
    return rec


def make_commits(n: int, commit_date=None) -> list[Commit]:
    from datetime import date

    d = commit_date or date(2026, 6, 1)
    return [
        Commit(hash=f"{i:040x}", date=d, author="Alice", message=f"commit {i}") for i in range(n)
    ]


# --- _ingest_all_repos: now a pure, no-arg, in-bulk ingester ----------------


def test_ingest_all_repos_ingests_every_registered_repo(add_repo, fake_git):
    repo_id = add_repo(last_ingest_error="old failure")
    fake_git.commits = make_commits(3)

    results = api._ingest_all_repos()

    assert results == [{"repo": "demo", "inserted": 3, "updated": 0, "unchanged": 0}]
    with get_session() as s:
        repo = s.get(RepoRow, repo_id)
        assert repo.last_ingest_attempt_at == repo.last_ingested_at
        assert repo.last_ingest_error is None


def test_ingest_all_repos_visits_every_registered_repo(add_repo, fake_git):
    add_repo(name="a", clone_url="https://example.com/a.git")
    add_repo(name="b", clone_url="https://example.com/b.git")
    fake_git.commits = make_commits(3)

    results = api._ingest_all_repos()
    by_name = {r["repo"]: r for r in results}
    assert set(by_name) == {"a", "b"}
    assert "inserted" in by_name["a"]
    assert "inserted" in by_name["b"] or "updated" in by_name["b"]
    assert sorted(fake_git.ensured) == ["a", "b"]


def test_ingest_keeps_shared_commit_for_each_repository(add_repo, fake_git):
    first = add_repo(name="upstream", clone_url="https://example.com/upstream.git")
    second = add_repo(name="fork", clone_url="https://example.com/fork.git")
    fake_git.commits = make_commits(1)

    results = api._ingest_all_repos()

    assert {result["repo"] for result in results} == {"upstream", "fork"}
    with get_session() as s:
        rows = s.query(CommitRow).filter(CommitRow.hash == fake_git.commits[0].hash).all()
    assert {row.repo_id for row in rows} == {first, second}


def test_ingest_all_repos_runs_jobs_concurrently(add_repo, monkeypatch):
    add_repo(name="a", clone_url="https://example.com/a.git")
    add_repo(name="b", clone_url="https://example.com/b.git")
    started = threading.Event()
    lock = threading.Lock()
    running = 0
    peak = 0

    def fake_run(repo_id, fallback_name=None):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
            if running == 2:
                started.set()
        assert started.wait(1)
        with lock:
            running -= 1
        api._release_ingest(repo_id)
        return {"repo": fallback_name}

    monkeypatch.setattr(api, "_run_ingest", fake_run)
    with ThreadPoolExecutor(max_workers=2) as executor:
        api._ingest_all_repos(executor)

    assert peak == 2


def test_ingest_all_repos_handles_empty_registry(add_repo, fake_git):
    add_repo()
    results = api._ingest_all_repos()
    # One repo registered, zero commits produced by the fake git layer.
    assert results == [{"repo": "demo", "inserted": 0, "updated": 0, "unchanged": 0}]


def test_ingest_all_repos_continues_after_one_repo_fails(add_repo, fake_git, monkeypatch):
    add_repo(name="good", clone_url="https://example.com/good.git")
    add_repo(name="bad", clone_url="https://example.com/bad.git")

    def selective_get_raw_log(path, *a, **kw):
        if path.endswith("/bad"):
            raise RuntimeError("simulated git failure")
        return ""

    monkeypatch.setattr(api, "get_raw_log", selective_get_raw_log)

    results = api._ingest_all_repos()
    by_name = {r["repo"]: r for r in results}
    assert "inserted" in by_name["good"]
    assert "error" in by_name["bad"]


def test_success_records_attempt_and_success_and_clears_error(add_repo, fake_git):
    repo_id = add_repo(last_ingest_error="old failure")
    assert api._claim_ingest(repo_id)

    api._run_ingest(repo_id)

    with get_session() as s:
        repo = s.get(RepoRow, repo_id)
        assert repo.last_ingest_attempt_at == repo.last_ingested_at
        assert repo.last_ingest_error is None


def test_failure_records_safe_outcome_and_preserves_success(
    client, add_repo, fake_git, monkeypatch, caplog
):
    previous_success = datetime(2026, 1, 1, tzinfo=UTC)
    repo_id = add_repo(last_ingested_at=previous_success)
    monkeypatch.setattr(
        api,
        "get_raw_log",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("secret raw git failure")),
    )
    assert api._claim_ingest(repo_id)

    result = api._run_ingest(repo_id)

    assert result["error"] == api.INGEST_ERROR
    assert "secret raw git failure" in caplog.text
    with get_session() as s:
        repo = s.get(RepoRow, repo_id)
        assert repo.last_ingest_attempt_at is not None
        assert repo.last_ingested_at.replace(tzinfo=UTC) == previous_success
        assert repo.last_ingest_error == api.INGEST_ERROR
    response_text = client.get("/repos").text
    assert api.INGEST_ERROR in response_text
    assert "secret raw git failure" not in response_text


def test_scheduler_skips_reserved_repo_and_continues(add_repo, fake_git):
    busy_id = add_repo(name="busy", clone_url="https://example.com/busy.git")
    add_repo(name="free", clone_url="https://example.com/free.git")
    assert api._claim_ingest(busy_id)

    results = api._ingest_all_repos()

    assert [result["repo"] for result in results] == ["free"]
    assert fake_git.ensured == ["free"]
    api._release_ingest(busy_id)


@pytest.mark.parametrize("fails", [False, True])
def test_reservation_released_after_ingest(add_repo, fake_git, monkeypatch, fails):
    repo_id = add_repo()
    if fails:
        monkeypatch.setattr(
            api, "get_raw_log", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("failure"))
        )
    assert api._claim_ingest(repo_id)

    api._run_ingest(repo_id)

    assert api._claim_ingest(repo_id)
    api._release_ingest(repo_id)


def test_deleted_repo_exits_before_git_or_commit_insert(add_repo, fake_git):
    repo_id = add_repo()
    fake_git.commits = make_commits(1)
    assert api._claim_ingest(repo_id)
    with get_session() as s:
        s.delete(s.get(RepoRow, repo_id))
        s.commit()

    result = api._run_ingest(repo_id)

    assert result["skipped"] == "deleted"
    assert fake_git.ensured == []
    with get_session() as s:
        assert s.query(CommitRow).count() == 0


# --- /summary no longer triggers ingest (regression for the old behaviour) ---


def test_summary_does_not_call_ingest(client, monkeypatch):
    called = []

    def record():
        called.append(True)
        return []

    monkeypatch.setattr(api, "_ingest_all_repos", record)
    client.get("/summary")
    assert called == []


# --- background scheduler: runs on a timer, can be cancelled ----------------


async def test_lifespan_clears_ingest_executor(monkeypatch):
    monkeypatch.setenv("INGEST_INTERVAL", "0")

    async with api._lifespan(api.app):
        assert api.app.state.ingest_executor is not None

    assert api.app.state.ingest_executor is None


def test_scheduler_runs_periodically(client_with_scheduler, add_repo, fake_git):
    add_repo()
    fake_git.commits = make_commits(2)
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        if fake_git.calls >= 2:
            break
        time.sleep(0.05)
    assert fake_git.calls >= 2, f"expected >=2 ingest calls, got {fake_git.calls}"


def test_scheduler_logs_and_continues_after_ingest_failure(
    client_with_scheduler, add_repo, fake_git, caplog
):
    import logging

    add_repo()
    fake_git.commits = make_commits(1)

    real_get_raw_log = api.get_raw_log

    def boom(*a, **kw):
        raise RuntimeError("scheduled failure")

    def maybe_boom(*a, **kw):
        if fake_git.calls == 0:
            fake_git.calls += 1
            return real_get_raw_log(*a, **kw)
        boom()

    with caplog.at_level(logging.WARNING, logger="surgite.api"):
        deadline = time.monotonic() + 3.5
        while time.monotonic() < deadline:
            if fake_git.calls >= 3:
                break
            time.sleep(0.1)
    assert fake_git.calls >= 2


def test_scheduler_disabled_when_interval_is_zero(monkeypatch, add_repo, fake_git):
    monkeypatch.setenv("INGEST_INTERVAL", "0")
    from fastapi.testclient import TestClient

    add_repo()
    fake_git.commits = make_commits(1)
    with TestClient(api.app):
        time.sleep(0.5)
    assert fake_git.calls == 0
