import io
from datetime import UTC, date, datetime

import pytest
from fastapi import HTTPException

from surgite import api
from surgite.auth import ensure_bootstrap_user
from surgite.cli import main
from surgite.db import get_session


@pytest.fixture(autouse=True)
def _mock_ingest(monkeypatch):
    from surgite import api as api_module

    monkeypatch.setattr(
        api_module,
        "_ingest_repo",
        lambda *a, **kw: {"repo": "mocked", "inserted": 0, "updated": 0, "unchanged": 0},
    )
    monkeypatch.setattr(
        api_module,
        "_ingest_all_repos",
        lambda *a, **kw: [],
    )


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_commits_empty(client):
    r = client.get("/commits")
    assert r.status_code == 200
    assert r.json() == {"total": 0, "commits": []}


def test_commits_returns_inserted_rows(client, add_commit):
    add_commit(hash="b" * 40, short_hash="bbbbbbb", author="Bob")
    r = client.get("/commits")
    body = r.json()
    assert body["total"] == 1
    assert body["commits"][0]["author"] == "Bob"


def test_commits_filter_by_repo_exact(client, add_commit):
    add_commit(hash="1" * 40, short_hash="1111111", repo="surgite")
    add_commit(hash="2" * 40, short_hash="2222222", repo="other-project")
    r = client.get("/commits?repo=surgite")
    body = r.json()
    assert body["total"] == 1
    assert body["commits"][0]["repo"] == "surgite"


def test_commits_author_filter_escapes_wildcards(client, add_commit):
    add_commit(hash="1" * 40, short_hash="1111111", author="Alice")
    add_commit(hash="2" * 40, short_hash="2222222", author="Bob")
    add_commit(hash="3" * 40, short_hash="3333333", author="100%")
    r = client.get("/commits?author=100%")
    body = r.json()
    assert body["total"] == 1
    assert body["commits"][0]["author"] == "100%"


def test_commits_author_filter_escapes_underscore(client, add_commit):
    add_commit(hash="1" * 40, short_hash="1111111", author="foo_bar")
    add_commit(hash="2" * 40, short_hash="2222222", author="fooXbar")
    r = client.get("/commits?author=foo_bar")
    body = r.json()
    assert body["total"] == 1
    assert body["commits"][0]["author"] == "foo_bar"


def test_commit_by_short_hash(client, add_commit):
    add_commit(hash="abc1234" + "0" * 33, short_hash="abc1234")
    r = client.get("/commits/abc1234")
    assert r.status_code == 200
    assert r.json()["short_hash"] == "abc1234"


def test_commit_hash_too_short_returns_400(client):
    assert client.get("/commits/abc12").status_code == 400


def test_commit_hash_not_found(client):
    assert client.get("/commits/" + "0" * 40).status_code == 404


def test_commit_ambiguous_prefix_returns_409(client, add_commit):
    add_commit(hash="abc1234" + "1" * 33, short_hash="abc1234")
    add_commit(hash="abc1234" + "2" * 33, short_hash="abc1234")
    r = client.get("/commits/abc1234")
    assert r.status_code == 409
    assert len(r.json()["detail"]["candidates"]) == 2


def test_commit_lookup_scopes_shared_hash_to_requested_repo(add_repo, add_commit):
    first = add_repo(name="upstream", clone_url="https://example.com/upstream.git")
    second = add_repo(name="fork", clone_url="https://example.com/fork.git")
    shared_hash = "b" * 40
    add_commit(repo_id=first, repo="upstream", hash=shared_hash, short_hash="b" * 7)
    add_commit(repo_id=second, repo="fork", hash=shared_hash, short_hash="b" * 7)

    with get_session() as session:
        user = ensure_bootstrap_user(session)
        assert (
            api.get_commit(shared_hash[:7], repo="fork", session=session, current_user=user)["repo"]
            == "fork"
        )
        assert (
            api.get_commit(shared_hash[:7], session=session, current_user=user)["repo"]
            == "upstream"
        )


def test_summary_preparation_rejects_more_than_500_ai_commits(add_commit):
    for number in range(501):
        add_commit(hash=f"{number:040x}", short_hash=f"{number:07x}")
    with get_session() as session:
        user = ensure_bootstrap_user(session)
    with pytest.raises(HTTPException, match="Too many commits") as exc:
        api._prepare_summary(
            since=None, until=None, author=None, repo=None, owner_id=user.id, ai=True
        )
    assert exc.value.status_code == 413


def test_summary_counts_all_matching_commits(client, add_commit):
    """Regression: /summary used to inherit list_commits' default limit=50,
    so by_repo/by_day undercounted whenever total > 50."""
    for i in range(60):
        add_commit(hash=f"{i:040x}", short_hash=f"{i:07x}", repo="demo", date=date(2026, 5, 19))
    body = client.get("/summary").json()
    assert body["total_commits"] == 60
    assert sum(body["by_repo"].values()) == 60
    assert body["by_day"]["2026-05-19"] == 60


def test_summary_by_day_is_chronologically_sorted(client, add_commit):
    for i, d in enumerate([date(2026, 5, 21), date(2026, 5, 19), date(2026, 5, 20)]):
        add_commit(hash=f"{i:040x}", short_hash=f"{i:07x}", date=d)
    assert list(client.get("/summary").json()["by_day"].keys()) == [
        "2026-05-19",
        "2026-05-20",
        "2026-05-21",
    ]


def test_summary_includes_the_source_sync_snapshot(client, add_repo, add_commit):
    synced_at = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)
    repo_id = add_repo(name="demo", last_ingested_at=synced_at)
    add_commit(repo_id=repo_id, repo="demo")

    body = client.get("/summary?repo=demo").json()

    assert body["source_synced_at"]["demo"].startswith("2026-05-19T12:00:00")


def test_summary_ai_without_key_returns_400(client):
    r = client.get("/summary?ai=true")
    assert r.status_code == 400


def test_providers_lists_all_with_anthropic_default(client):
    body = client.get("/providers").json()
    assert body["default"] == "anthropic"
    names = {p["name"] for p in body["providers"]}
    assert {"anthropic", "groq", "deepseek"} <= names
    # conftest clears every key, so nothing is available in tests.
    assert all(p["available"] is False for p in body["providers"])


def test_summary_ai_unknown_provider_returns_400(client, add_commit):
    add_commit()
    assert client.get("/summary?ai=true&provider=bogus").status_code == 400


def _fake_generate(calls):
    async def fake(
        commit_log, provider=None, model=None, settings=None, client=None, user_id=None, **kwargs
    ):
        calls.append(commit_log)
        return {"summary": "## Features\n- shipped it", "provider": "groq", "model": "x"}

    return fake


def test_summary_ai_uses_selected_provider(client, add_commit, monkeypatch):
    add_commit()
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    from surgite import summarizer

    monkeypatch.setattr(summarizer, "generate_summary", _fake_generate([]))
    body = client.get("/summary?ai=true&provider=groq&combined=true").json()
    assert body["ai_summary"].startswith("## Features")
    assert body["ai_provider"] == "groq"
    assert body["ai_model"] == "x"


def test_summary_ai_skips_combined_summary_by_default(client, add_commit, monkeypatch):
    add_commit()
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    from surgite import summarizer

    calls = []
    monkeypatch.setattr(summarizer, "generate_summary", _fake_generate(calls))
    body = client.get("/summary?ai=true&provider=groq").json()
    assert body["ai_summaries"]["demo"]["summary"].startswith("## Features")
    assert body["ai_summary"] is None
    assert body["ai_provider"] is None
    assert len(calls) == 1  # one per-repo call, no whole-log call


def test_summary_ai_combined_makes_one_extra_call(client, add_commit, monkeypatch):
    add_commit()
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    from surgite import summarizer

    calls = []
    monkeypatch.setattr(summarizer, "generate_summary", _fake_generate(calls))
    body = client.get("/summary?ai=true&provider=groq&combined=true").json()
    assert body["ai_summary"] is not None
    assert len(calls) == 2  # per-repo + whole-log


def test_summary_ai_without_key_returns_400_even_with_no_commits(client):
    # The up-front provider check must catch a missing key before any LLM work,
    # including when the period has no commits at all.
    r = client.get("/summary?ai=true&provider=groq")
    assert r.status_code == 400
    assert "GROQ_API_KEY" in r.json()["detail"]


# --- /repos -----------------------------------------------------------------


def test_list_repos_empty(client):
    r = client.get("/repos")
    assert r.status_code == 200
    assert r.json() == {"repos": [], "stale_after_seconds": None}


def test_list_repos_exposes_the_scheduler_derived_stale_window(client, monkeypatch):
    monkeypatch.setenv("INGEST_INTERVAL", "45")
    assert client.get("/repos").json()["stale_after_seconds"] == 90


def test_create_repo_returns_201(client):
    r = client.post("/repos", json={"url": "https://github.com/user/repo.git"})
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "repo"
    assert body["clone_url"] == "https://github.com/user/repo.git"
    assert body["last_ingested_at"] is None
    assert body["last_ingest_attempt_at"] is None
    assert body["last_ingest_error"] is None
    assert body["id"] is not None


def test_create_repo_appears_in_list(client):
    client.post("/repos", json={"url": "https://github.com/user/repo.git"})
    repos = client.get("/repos").json()["repos"]
    assert len(repos) == 1
    assert repos[0]["clone_url"] == "https://github.com/user/repo.git"


def test_create_repo_duplicate_url_returns_409(client):
    client.post("/repos", json={"url": "https://github.com/user/repo.git"})
    r = client.post("/repos", json={"url": "https://github.com/user/repo.git"})
    assert r.status_code == 409


def test_create_repo_non_remote_url_returns_400(client):
    r = client.post("/repos", json={"url": "/some/local/path"})
    assert r.status_code == 400


def test_create_repo_duplicate_name_returns_409(client):
    client.post("/repos", json={"url": "https://github.com/user/repo.git"})
    r = client.post("/repos", json={"url": "https://gitlab.com/other/repo.git"})
    assert r.status_code == 409
    assert "name" in r.json()["detail"].lower()


def test_create_repo_runs_ingest_in_background(client, monkeypatch):
    from surgite import api as api_module

    calls = []

    def fake_ingest(*args, **kwargs):
        calls.append(args)
        return {"repo": "mocked", "inserted": 0, "updated": 0, "unchanged": 0}

    monkeypatch.setattr(api_module, "_ingest_repo", fake_ingest)
    r = client.post("/repos", json={"url": "https://github.com/user/repo.git"})
    assert r.status_code == 201
    assert r.json()["last_ingested_at"] is None
    assert len(calls) == 1
    stored = client.get("/repos").json()["repos"][0]
    assert stored["last_ingest_attempt_at"] == stored["last_ingested_at"]
    assert stored["last_ingest_error"] is None


def test_delete_repo(client, add_repo):
    repo_id = add_repo()
    r = client.delete(f"/repos/{repo_id}")
    assert r.status_code == 204
    assert client.get("/repos").json()["repos"] == []


def test_manual_ingest_returns_202(client, add_repo):
    repo_id = add_repo()
    r = client.post(f"/repos/{repo_id}/ingest")
    assert r.status_code == 202
    assert r.json() == {"accepted": True}
    stored = client.get("/repos").json()["repos"][0]
    assert stored["last_ingest_attempt_at"] == stored["last_ingested_at"]


def test_manual_ingest_not_found_returns_404(client):
    assert client.post("/repos/999/ingest").status_code == 404


def test_manual_ingest_already_reserved_returns_409(client, add_repo):
    from surgite import api as api_module

    repo_id = add_repo()
    assert api_module._claim_ingest(repo_id)
    r = client.post(f"/repos/{repo_id}/ingest")
    assert r.status_code == 409
    assert r.json() == {"detail": "Ingest already in progress"}


def test_delete_repo_not_found_returns_404(client):
    assert client.delete("/repos/999").status_code == 404


def test_delete_repo_cascades_commits(client, add_repo, add_commit):
    repo_id = add_repo(name="doomed")
    add_commit(hash="b" * 40, short_hash="bbbbbbb", repo="doomed")
    client.delete(f"/repos/{repo_id}")
    r = client.get("/commits", params={"repo": "doomed"})
    assert r.json()["commits"] == []


def test_cli_without_ingest_prints_formatted_log(monkeypatch):
    monkeypatch.setattr(
        "surgite.cli.get_raw_log", lambda *a, **kw: "abc1234\x1f2026-06-01\x1fAlice\x1ffix bug"
    )
    monkeypatch.setattr("sys.argv", ["surgite", "/r"])
    stdout = io.StringIO()
    monkeypatch.setattr("sys.stdout", stdout)

    main()

    assert "Alice" in stdout.getvalue()
    assert "abc1234" in stdout.getvalue()


def test_cli_summarize_runs_the_summarizer(monkeypatch):
    monkeypatch.setattr(
        "surgite.cli.get_raw_log", lambda *a, **kw: "abc1234\x1f2026-06-01\x1fAlice\x1ffix bug"
    )
    monkeypatch.setattr(
        "surgite.cli.summarize_commits", lambda text, **kw: "Accomplishments:\n- fixed a bug"
    )
    monkeypatch.setattr("sys.argv", ["surgite", "/r", "--summarize"])
    stdout = io.StringIO()
    monkeypatch.setattr("sys.stdout", stdout)

    main()

    assert "Accomplishments:" in stdout.getvalue()


def test_cli_output_writes_to_file(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "surgite.cli.get_raw_log", lambda *a, **kw: "abc1234\x1f2026-06-01\x1fAlice\x1ffix bug"
    )
    out_file = tmp_path / "summary.txt"
    monkeypatch.setattr("sys.argv", ["surgite", "/r", "--output", str(out_file)])

    main()

    written = out_file.read_text()
    assert "fix bug" in written
    assert "Alice" in written


# --- /settings/prompt -------------------------------------------------------


def test_get_prompt_settings_returns_defaults(client):
    r = client.get("/settings/prompt")
    assert r.status_code == 200
    body = r.json()
    assert body["user_name"] == ""
    assert body["user_role"] == ""
    assert body["tone"] == "neutral"
    assert body["group_count"] == "2-5"
    assert body["output_format"] == "markdown"
    assert body["custom_instructions"] == ""


def test_put_prompt_settings_partial_update(client):
    r = client.put("/settings/prompt", json={"user_name": "Alice", "tone": "first-person"})
    assert r.status_code == 200
    body = r.json()
    assert body["user_name"] == "Alice"
    assert body["tone"] == "first-person"
    assert body["user_role"] == ""
    assert body["group_count"] == "2-5"


def test_put_prompt_settings_persists(client):
    client.put("/settings/prompt", json={"user_name": "Bob"})
    r = client.get("/settings/prompt")
    assert r.json()["user_name"] == "Bob"


def test_put_prompt_settings_overwrites(client):
    client.put("/settings/prompt", json={"user_name": "Alice"})
    client.put("/settings/prompt", json={"user_name": "Bob"})
    r = client.get("/settings/prompt")
    assert r.json()["user_name"] == "Bob"


def test_put_prompt_settings_all_fields(client):
    r = client.put(
        "/settings/prompt",
        json={
            "user_name": "Alice",
            "user_role": "backend engineer",
            "tone": "formal",
            "group_count": "1-3",
            "output_format": "plain",
            "custom_instructions": "Focus on bug fixes.",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["user_name"] == "Alice"
    assert body["user_role"] == "backend engineer"
    assert body["tone"] == "formal"
    assert body["group_count"] == "1-3"
    assert body["output_format"] == "plain"
    assert body["custom_instructions"] == "Focus on bug fixes."


# --- item 5: /summary omits the commits list by default ---------------------


def test_summary_omits_commits_list_by_default(client, add_commit):
    add_commit()
    body = client.get("/summary").json()
    assert body["total_commits"] == 1
    assert body["commits"] == []
    # stats are still computed from the (internally queried) commits
    assert body["by_repo"]["demo"] == 1


def test_summary_includes_commits_when_requested(client, add_commit):
    add_commit()
    body = client.get("/summary?commits=true").json()
    assert len(body["commits"]) == 1
    assert body["commits"][0]["repo"] == "demo"


# --- item 7: per-repo prompt settings ---------------------------------------


def test_prompt_settings_default_repo_id_is_null(client):
    assert client.get("/settings/prompt").json()["repo_id"] is None


def test_prompt_settings_per_repo_override(client, add_repo):
    repo_id = add_repo()
    client.put("/settings/prompt", json={"user_name": "Global"})
    r = client.put(f"/settings/prompt?repo_id={repo_id}", json={"user_name": "RepoSpecific"})
    assert r.status_code == 200
    assert r.json()["repo_id"] == repo_id
    assert r.json()["user_name"] == "RepoSpecific"
    # The two scopes are independent.
    assert client.get("/settings/prompt").json()["user_name"] == "Global"
    assert client.get(f"/settings/prompt?repo_id={repo_id}").json()["user_name"] == "RepoSpecific"


def test_prompt_settings_repo_without_row_falls_back_to_global(client, add_repo):
    repo_id = add_repo()
    client.put("/settings/prompt", json={"user_name": "Global"})
    body = client.get(f"/settings/prompt?repo_id={repo_id}").json()
    # No repo-specific row yet -> the global default is returned (repo_id null
    # signals the value is inherited, not repo-specific).
    assert body["user_name"] == "Global"
    assert body["repo_id"] is None


def test_prompt_settings_put_unknown_repo_returns_404(client):
    assert client.put("/settings/prompt?repo_id=999", json={"user_name": "x"}).status_code == 404


# --- item 6: shareable summary links ----------------------------------------


def test_create_share_returns_slug(client):
    r = client.post("/summaries", json={"repo": "demo", "since": "2026-05-01", "ai": True})
    assert r.status_code == 201
    body = r.json()
    assert body["slug"]
    assert body["expires_at"]


def test_resolve_share_returns_params(client):
    slug = client.post(
        "/summaries", json={"repo": "demo", "since": "2026-05-01", "author": "Alice"}
    ).json()["slug"]
    body = client.get(f"/summaries/{slug}").json()
    assert body["params"]["repo"] == "demo"
    assert body["params"]["since"] == "2026-05-01"
    assert body["params"]["author"] == "Alice"


def test_resolve_unknown_share_returns_404(client):
    assert client.get("/summaries/nope").status_code == 404


def test_resolve_expired_share_returns_404(client):
    from datetime import UTC, datetime, timedelta

    from surgite.auth import ensure_bootstrap_user
    from surgite.db import SharedSummaryRow, get_session

    with get_session() as s:
        owner_id = ensure_bootstrap_user(s).id
        s.add(
            SharedSummaryRow(
                slug="stale",
                owner_id=owner_id,
                params={"repo": "demo"},
                created_at=datetime.now(UTC) - timedelta(days=30),
                expires_at=datetime.now(UTC) - timedelta(days=1),
            )
        )
        s.commit()
    assert client.get("/summaries/stale").status_code == 404


def test_expired_share_cleanup():
    from datetime import UTC, datetime, timedelta

    from surgite import api
    from surgite.auth import ensure_bootstrap_user
    from surgite.db import SharedSummaryRow, get_session

    now = datetime.now(UTC)
    with get_session() as s:
        owner_id = ensure_bootstrap_user(s).id
        s.add(
            SharedSummaryRow(
                slug="old",
                owner_id=owner_id,
                params={},
                created_at=now,
                expires_at=now - timedelta(1),
            )
        )
        s.add(
            SharedSummaryRow(
                slug="live",
                owner_id=owner_id,
                params={},
                created_at=now,
                expires_at=now + timedelta(1),
            )
        )
        s.commit()
    assert api._delete_expired_summaries() == 1
    with get_session() as s:
        remaining = [r.slug for r in s.query(SharedSummaryRow).all()]
    assert remaining == ["live"]


# --- item 12: /health/deep --------------------------------------------------


def test_health_deep_no_repos_no_keys_is_ok(client):
    r = client.get("/health/deep")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    components = body["components"]
    assert components["db"] == "ok"
    assert components["git"] == "no_repos"
    # conftest clears every key -> all providers report missing_key, not failure.
    assert all(state == "missing_key" for state in components["providers"].values())


def test_health_deep_git_ok(client, add_repo, monkeypatch):
    add_repo()
    monkeypatch.setattr("surgite.api.ls_remote", lambda url, timeout=10, credentials=None: None)
    body = client.get("/health/deep").json()
    assert body["components"]["git"] == "ok"


def test_health_deep_git_failure_returns_503(client, add_repo, monkeypatch):
    add_repo()

    def boom(url, timeout=10, credentials=None):
        raise RuntimeError("unreachable host")

    monkeypatch.setattr("surgite.api.ls_remote", boom)
    r = client.get("/health/deep")
    assert r.status_code == 503
    assert r.json()["components"]["git"] == "error"


# --- item 4: /summary/stream (SSE) ------------------------------------------


def test_summary_stream_emits_meta_deltas_and_done(client, add_commit, monkeypatch):
    add_commit()
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    from surgite import summarizer

    async def fake_stream(
        log_text, provider=None, settings=None, client=None, user_id=None, **kwargs
    ):
        yield "Hello "
        yield "world"

    monkeypatch.setattr(summarizer, "stream_summary", fake_stream)
    r = client.get("/summary/stream?ai=true&provider=groq")
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    text = r.text
    assert "event: meta" in text
    assert '"source_synced_at"' in text
    assert "event: delta" in text
    assert "Hello " in text and "world" in text
    assert "event: repo_done" in text
    assert "event: done" in text


def test_summary_stream_without_key_returns_400(client):
    assert client.get("/summary/stream?provider=groq").status_code == 400


def test_summary_stream_model_discovery_error_finishes(client, add_commit, monkeypatch):
    add_commit()
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    from surgite import summarizer

    async def fail_discovery(*args):
        raise summarizer.ProviderError("choose a model")

    monkeypatch.setattr(summarizer, "resolve_model", fail_discovery)
    text = client.get("/summary/stream?provider=groq").text

    assert "event: meta" in text
    assert "event: repo_error" in text
    assert "choose a model" in text
    assert text.count("event: done") == 1


def test_summary_stream_unexpected_producer_error_finishes(client, add_commit, monkeypatch):
    add_commit()
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    from surgite import summarizer

    async def fail_stream(*args, **kwargs):
        raise RuntimeError("secret provider failure")
        yield

    monkeypatch.setattr(summarizer, "stream_summary", fail_stream)
    text = client.get("/summary/stream?provider=groq").text

    assert "event: repo_error" in text
    assert "Summary stream failed." in text
    assert "secret provider failure" not in text
    assert text.count("event: done") == 1


# --- item 8: CLI --registered -----------------------------------------------


class _FakeApiResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_cli_registered_log_mode(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeApiResp({"log_by_repo": {"demo": "[2026-06-01] fix bug (Alice) <abc1234>"}})

    monkeypatch.setattr("surgite.cli.httpx.get", fake_get)
    monkeypatch.setattr("sys.argv", ["surgite", "--registered", "demo", "--since", "2026-06-01"])
    stdout = io.StringIO()
    monkeypatch.setattr("sys.stdout", stdout)

    main()

    assert captured["url"].endswith("/summary")
    assert captured["params"]["repo"] == "demo"
    assert "fix bug" in stdout.getvalue()


def test_cli_registered_summarize_mode(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        assert params["ai"] == "true"
        return _FakeApiResp(
            {"ai_summaries": {"demo": {"summary": "## Work\n- shipped", "provider": "groq"}}}
        )

    monkeypatch.setattr("surgite.cli.httpx.get", fake_get)
    monkeypatch.setattr("sys.argv", ["surgite", "--registered", "demo", "--summarize"])
    stdout = io.StringIO()
    monkeypatch.setattr("sys.stdout", stdout)

    main()

    assert "## Work" in stdout.getvalue()


def test_cli_requires_path_or_registered(monkeypatch):
    monkeypatch.setattr("sys.argv", ["surgite"])
    with pytest.raises(SystemExit):
        main()
