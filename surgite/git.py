import os
import re
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from surgite.models import Commit

REMOTE_PATTERNS = re.compile(r"^(https?://|git@|git://|ssh://)")


@dataclass(frozen=True)
class GitCredentials:
    origin: str
    username: str
    token: str


def is_remote_url(path: str) -> bool:
    """Return whether a path looks like a remote Git URL."""
    return bool(REMOTE_PATTERNS.match(path))


def _repo_name_from_url(url: str) -> str:
    """Extract a repository name from a remote URL."""
    name = url.rstrip("/")
    if name.endswith(".git"):
        name = name[:-4]
    if ":" in name and not name.startswith("http"):
        name = name.split(":")[-1]
    name = name.rstrip("/").split("/")[-1]
    return name


@contextmanager
def _git_auth(url: str, credentials: GitCredentials | None):
    """Yield a non-interactive Git environment without placing secrets in URLs or argv."""
    if credentials is None:
        yield {"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1"}
        return
    parsed_url, parsed_origin = urlparse(url), urlparse(credentials.origin)
    if (
        parsed_url.scheme != "https"
        or parsed_url.username
        or parsed_url.password
        or parsed_url.netloc.lower() != parsed_origin.netloc.lower()
    ):
        raise RuntimeError("Connection credentials do not match this HTTPS repository")
    with tempfile.TemporaryDirectory(prefix="surgite-git-") as temp_dir:
        askpass = Path(temp_dir) / "askpass"
        askpass.write_text(
            '#!/bin/sh\ncase "$1" in *Username*) printf %s "$SURGITE_GIT_USERNAME";; *) printf %s "$SURGITE_GIT_TOKEN";; esac\n'
        )
        askpass.chmod(0o700)
        env = {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_ASKPASS": str(askpass),
            "GIT_ASKPASS_REQUIRE": "force",
            "SURGITE_GIT_USERNAME": credentials.username,
            "SURGITE_GIT_TOKEN": credentials.token,
        }
        yield env


def _run_git(
    args: list[str], *, env: dict[str, str], **kwargs: Any
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "credential.helper=", *args],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        **kwargs,
    )


def ensure_repo(
    name: str,
    url: str,
    cache_dir: str,
    timeout: int = 120,
    credentials: GitCredentials | None = None,
) -> str:
    """Clone or update a metadata-only repository cache."""
    dest = os.path.join(cache_dir, name)
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    with _git_auth(url, credentials) as env:
        if os.path.isdir(os.path.join(dest, ".git")):
            _run_git(["fetch", "origin"], cwd=dest, env=env, check=True, timeout=timeout)
            # Refresh origin/HEAD in case the default branch changed.
            _run_git(["remote", "set-head", "origin", "-a"], cwd=dest, env=env, timeout=timeout)
            _run_git(
                ["reset", "--soft", "origin/HEAD"], cwd=dest, env=env, check=True, timeout=timeout
            )
        else:
            _run_git(
                ["clone", "--filter=blob:none", "--no-checkout", url, dest],
                env=env,
                check=True,
                timeout=timeout,
            )
    return dest


def ls_remote(url: str, timeout: int = 10, credentials: GitCredentials | None = None) -> None:
    """Check remote reachability without cloning."""
    try:
        with _git_auth(url, credentials) as env:
            result = _run_git(["ls-remote", "--heads", url], env=env, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"git ls-remote timed out after {timeout}s") from exc
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git ls-remote failed")


def _is_git_ref(repo_path: str, value: str, timeout: int = 120) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", value],
        capture_output=True,
        cwd=repo_path,
        timeout=timeout,
    )
    return result.returncode == 0


def get_raw_log(
    repo_path: str,
    since: str,
    until: str,
    author: str | None = None,
    since_commit: str | None = None,
    timeout: int = 120,
) -> str:
    """Return the requested raw Git log."""
    cmd = ["git", "log", "--pretty=format:%H\x1f%ad\x1f%an\x1f%s", "--date=short"]

    until_is_ref = _is_git_ref(repo_path, until, timeout)

    if since_commit:
        if until_is_ref:
            cmd.append(f"{since_commit}..{until}")
        else:
            cmd.append(f"{since_commit}..")
            cmd.append(f"--until={until}")
    else:
        if until_is_ref:
            cmd.append(until)
            cmd.append(f"--since={since}")
        else:
            cmd.append(f"--since={since}")
            cmd.append(f"--until={until}")

    if author:
        cmd.append(f"--author={author}")

    result = subprocess.run(
        cmd,
        text=True,
        capture_output=True,
        cwd=repo_path,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


def parse_log(raw_log: str) -> list[Commit]:
    """Parse raw Git log records into commits."""
    lines = raw_log.splitlines()
    commits = []
    for line in lines:
        hash, date_str, author, message = line.split("\x1f", maxsplit=3)
        commits.append(
            Commit(
                hash=hash,
                date=date.fromisoformat(date_str),
                author=author,
                message=message,
            )
        )
    return commits
