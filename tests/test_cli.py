"""CLI argument and output tests."""

import builtins
import importlib.metadata
import sys

import pytest

from surgite import cli
from surgite.cli import _resolve_since, main


def test_version_flag_prints_the_installed_version(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["surgite", "--version"])

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 0
    assert importlib.metadata.version("surgite") in capsys.readouterr().out


def test_version_flag_is_listed_in_help(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["surgite", "--help"])

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 0
    assert "--version" in capsys.readouterr().out


def test_resolve_since_translates_git_relative_dates():
    from datetime import date, timedelta

    assert _resolve_since("7.days.ago") == (date.today() - timedelta(days=7)).isoformat()
    assert _resolve_since("2.weeks.ago") == (date.today() - timedelta(days=14)).isoformat()


def test_resolve_since_passes_through_iso_dates():
    assert _resolve_since("2026-08-01") == "2026-08-01"


def test_resolve_since_defaults_to_7_days():
    from datetime import date, timedelta

    assert _resolve_since(None) == (date.today() - timedelta(days=7)).isoformat()


def test_output_is_written_as_utf8(monkeypatch, tmp_path):
    summary = "fix: café → 日本語 🎉"
    out = tmp_path / "summary.txt"
    open_kwargs = []
    real_open = builtins.open

    def recording_open(*args, **kwargs):
        open_kwargs.append(kwargs)
        return real_open(*args, **kwargs)

    monkeypatch.setattr(builtins, "open", recording_open)
    monkeypatch.setattr(cli, "_run_local", lambda args: summary)
    monkeypatch.setattr(sys, "argv", ["surgite", "some/repo", "--output", str(out)])

    main()

    assert {"encoding": "utf-8"} in open_kwargs
    assert out.read_text(encoding="utf-8") == summary


def test_provider_flag_is_listed_in_help(capsys, monkeypatch):
    """#66: --provider must be documented in CLI help."""
    monkeypatch.setattr("sys.argv", ["surgite", "--help"])

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 0
    assert "--provider" in capsys.readouterr().out


def test_invalid_provider_fails_with_usage_error(capsys, monkeypatch):
    """#66: Unknown provider fails as a concise CLI usage error."""
    monkeypatch.setattr("sys.argv", ["surgite", "some/repo", "--provider", "not-a-real-provider"])

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "invalid choice: 'not-a-real-provider'" in err


def test_provider_forwarded_to_local_summarize(monkeypatch):
    """#66: Local summarization forwards the chosen provider to summarize_commits()."""
    called = {}

    def fake_summarize(summary, provider=None):
        called["provider"] = provider
        return "summarized"

    monkeypatch.setattr("surgite.cli.get_raw_log", lambda *a, **kw: "")
    monkeypatch.setattr("surgite.cli.parse_log", lambda raw: [])
    monkeypatch.setattr("surgite.cli.format_log", lambda commits: "log")
    monkeypatch.setattr("surgite.cli.summarize_commits", fake_summarize)
    monkeypatch.setattr(sys, "argv", ["surgite", "some/repo", "--summarize", "--provider", "groq"])

    main()

    assert called.get("provider") == "groq"


def test_provider_forwarded_to_registered_summary_params(monkeypatch):
    """#66: --registered includes provider in /summary query params with --summarize."""
    requested_params = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ai_summaries": {"myrepo": {"summary": "ai summary"}}}

    def fake_get(url, params=None, headers=None, timeout=None):
        requested_params.update(params or {})
        return FakeResponse()

    monkeypatch.setattr("httpx.get", fake_get)
    monkeypatch.setattr("surgite.cli_auth.auth_headers", lambda base: {})
    monkeypatch.setattr(
        sys,
        "argv",
        ["surgite", "--registered", "myrepo", "--summarize", "--provider", "deepseek"],
    )

    main()

    assert requested_params.get("ai") == "true"
    assert requested_params.get("provider") == "deepseek"


def test_provider_omitted_without_summarize_in_registered(monkeypatch):
    """#66: --provider is irrelevant and omitted from params when --summarize is unset."""
    requested_params = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"log_by_repo": {"myrepo": "commit log"}}

    def fake_get(url, params=None, headers=None, timeout=None):
        requested_params.update(params or {})
        return FakeResponse()

    monkeypatch.setattr("httpx.get", fake_get)
    monkeypatch.setattr("surgite.cli_auth.auth_headers", lambda base: {})
    monkeypatch.setattr(
        sys, "argv", ["surgite", "--registered", "myrepo", "--provider", "anthropic"]
    )

    main()

    assert "ai" not in requested_params
    assert "provider" not in requested_params
