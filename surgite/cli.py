import argparse
import importlib.metadata
import os
import re
import sys
from datetime import date, timedelta

import httpx
from dotenv import load_dotenv

from surgite import cli_auth
from surgite.formatter import format_log
from surgite.git import get_raw_log, parse_log
from surgite.summarizer import PROVIDERS, summarize_commits

_GIT_RELATIVE_RE = re.compile(r"^(\d+)\.(days?|weeks?)\.ago$")


def _resolve_since(value: str | None, default_days: int = 7) -> str:
    """Convert supported Git-relative dates to ISO for the API."""
    if value is None:
        return (date.today() - timedelta(days=default_days)).isoformat()
    m = _GIT_RELATIVE_RE.match(value)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        days = n * 7 if unit.startswith("week") else n
        return (date.today() - timedelta(days=days)).isoformat()
    return value


def _api_base() -> str:
    return os.environ.get("SURGITE_API_URL", "http://localhost:8000").rstrip("/")


def _run_local(args) -> str:
    """Summarize a local repository without the API."""
    raw_log = get_raw_log(
        args.repo_path,
        args.since or "7.days.ago",
        args.until or "now",
        args.author,
        args.since_commit,
    )
    commits = parse_log(raw_log)
    summary = format_log(commits)
    if args.summarize:
        summary = summarize_commits(summary, provider=args.provider)
    return summary


def _run_registered(args) -> str:
    """Summarize a repository registered with the API."""
    base = _api_base()
    headers = cli_auth.auth_headers(base)

    since = _resolve_since(args.since)
    params: dict[str, str] = {"repo": args.registered, "since": since}
    if args.until:
        params["until"] = args.until
    if args.author:
        params["author"] = args.author
    if args.summarize:
        params["ai"] = "true"
        if args.provider:
            params["provider"] = args.provider

    resp = httpx.get(f"{base}/summary", params=params, headers=headers, timeout=180)
    resp.raise_for_status()
    data = resp.json()

    if args.summarize:
        sections = data.get("ai_summaries") or {}
        parts = [f"## {repo}\n{s['summary']}" for repo, s in sections.items()]
    else:
        parts = list((data.get("log_by_repo") or {}).values())
    return "\n\n".join(p for p in parts if p.strip()) or "No commits in this period."


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="Generate a standup summary from git log.")
    parser.add_argument(
        "--version",
        action="version",
        version=f"surgite {importlib.metadata.version('surgite')}",
    )
    parser.add_argument("repo_path", nargs="?", help="Path to a local git repository")
    parser.add_argument(
        "--registered",
        metavar="NAME",
        help="Pull a repo registered in a running surgite API "
        "(set SURGITE_API_URL; auth via `surgite --login` or SURGITE_API_KEY) "
        "instead of a local path",
    )

    auth_group = parser.add_argument_group("auth (multi_user deployments)")
    auth_group.add_argument(
        "--login", action="store_true", help="Log in to the API and save a session"
    )
    auth_group.add_argument(
        "--logout", action="store_true", help="Revoke and forget the saved session"
    )
    auth_group.add_argument(
        "--redeem-invite",
        metavar="TOKEN",
        help="Redeem an invite token: create an account and log in",
    )
    auth_group.add_argument("--email", help="Email for --login / --redeem-invite (or prompt)")
    auth_group.add_argument(
        "--keyring-file",
        action="store_true",
        help="Store the session in a 0600 file instead of the OS keyring "
        "(for headless servers and CI)",
    )

    since_group = parser.add_mutually_exclusive_group()
    since_group.add_argument("--since", help="Start date for git log (default: 7.days.ago)")
    since_group.add_argument("--since-commit", help="Starting commit hash (overrides --since)")
    parser.add_argument("--until", help="End date or commit ref for git log (default: now)")
    parser.add_argument("--author", help="Filter commits by author (optional)")
    parser.add_argument("--output", help="Output file for the summary (optional)")
    parser.add_argument(
        "--summarize", action="store_true", help="Summarize the commit messages with AI. (optional)"
    )
    parser.add_argument(
        "--provider",
        choices=sorted(PROVIDERS),
        help="LLM provider for --summarize (choices: %(choices)s). "
        "Overrides the LLM_PROVIDER environment variable.",
    )

    args = parser.parse_args()

    if args.keyring_file:
        cli_auth.use_file_fallback(True)

    # Auth subcommands short-circuit before the repo/summary path.
    if args.login or args.logout or args.redeem_invite:
        if sum(bool(x) for x in (args.login, args.logout, args.redeem_invite)) > 1:
            parser.error("use only one of --login, --logout, --redeem-invite")
        base = _api_base()
        if args.login:
            sys.exit(cli_auth.cmd_login(base, email=args.email))
        if args.logout:
            sys.exit(cli_auth.cmd_logout(base))
        sys.exit(cli_auth.cmd_redeem_invite(base, args.redeem_invite, email=args.email))

    if bool(args.repo_path) == bool(args.registered):
        parser.error("provide either a repo_path or --registered <name>, not both")
    if args.registered and args.since_commit:
        parser.error("--since-commit is only supported for a local repo_path")

    summary = _run_registered(args) if args.registered else _run_local(args)

    if args.output:
        # UTF-8 is right rather than merely convenient here: the file holds git
        # commit data, git stores commit messages as UTF-8 by default, and the
        # summary may also contain LLM-generated prose with arbitrary Unicode.
        # On Windows the platform default is cp1252, which would fail on any
        # non-Latin-1 character (emoji, diacritics, CJK).
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(summary)
    else:
        print(summary)


if __name__ == "__main__":
    main()
