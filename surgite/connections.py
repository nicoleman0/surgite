"""Git connection storage, GitHub App OAuth, and safe Git credentials."""

import hashlib
import json
import secrets
import threading
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from surgite import config
from surgite.db import GitConnectionRow, GitHubAuthStateRow, RepoRow
from surgite.git import GitCredentials
from surgite.secrets import decrypt, encrypt

# ponytail: global lock, per-connection if refresh contention shows up
_github_lock = threading.Lock()


def github_enabled() -> bool:
    return bool(config.GITHUB_APP_CLIENT_ID and config.GITHUB_APP_CLIENT_SECRET)


def token_host(origin: str) -> str:
    parsed = urlparse(origin)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Origin must be a credential-free HTTPS URL")
    return parsed.netloc.lower()


def _secret(row: GitConnectionRow) -> dict[str, str]:
    if not row.encrypted_secret:
        raise RuntimeError("Connection requires reconnection")
    return json.loads(decrypt(row.encrypted_secret))


def _encrypt_secret(value: dict[str, str]) -> str:
    return encrypt(json.dumps(value, separators=(",", ":")))


def connection_credentials(row: GitConnectionRow, session: Session) -> GitCredentials:
    if row.disconnected_at is not None:
        raise RuntimeError("Connection required")
    secret = _secret(row)
    if row.kind == "token":
        return GitCredentials(secret["origin"], secret["username"], secret["token"])
    if row.kind != "github":
        raise RuntimeError("Connection requires reconnection")
    secret = _refresh_github(row, secret, session)
    return GitCredentials("https://github.com", "x-access-token", secret["access_token"])


def repo_credentials(repo: RepoRow, session: Session) -> GitCredentials | None:
    if not repo.connection_id:
        return None
    connection = session.get(GitConnectionRow, repo.connection_id)
    if connection is None or connection.owner_id != repo.owner_id:
        raise RuntimeError("Connection required")
    return connection_credentials(connection, session)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _fresh(secret: dict[str, str]) -> bool:
    expires_at = _utc(datetime.fromisoformat(secret["expires_at"]))
    return expires_at > datetime.now(UTC) + timedelta(minutes=5)


def _refresh_github(
    row: GitConnectionRow, secret: dict[str, str], session: Session
) -> dict[str, str]:
    if _fresh(secret):
        return secret
    with _github_lock:
        session.refresh(row)
        secret = _secret(row)
        if _fresh(secret):
            return secret
        try:
            secret = github_token(grant_type="refresh_token", refresh_token=secret["refresh_token"])
        except Exception as exc:
            row.encrypted_secret = None
            row.disconnected_at = datetime.now(UTC)
            row.updated_at = datetime.now(UTC)
            session.commit()
            raise RuntimeError("GitHub connection requires reconnection") from exc
        row.encrypted_secret = _encrypt_secret(secret)
        row.updated_at = datetime.now(UTC)
        session.commit()
        return secret


def _github_secret(data: dict) -> dict[str, str]:
    if not data.get("refresh_token"):
        raise ValueError("GitHub App must issue expiring user access tokens")
    return {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_at": (
            datetime.now(UTC) + timedelta(seconds=int(data.get("expires_in", 28800)))
        ).isoformat(),
    }


def start_github_authorization(user_id: str, session: Session) -> str:
    if not github_enabled():
        raise RuntimeError("GitHub App connections are not configured")
    state = secrets.token_urlsafe(32)
    session.add(
        GitHubAuthStateRow(
            state_hash=hashlib.sha256(state.encode()).hexdigest(),
            user_id=user_id,
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
    )
    session.commit()
    return state


def consume_github_state(state: str, user_id: str, session: Session) -> None:
    digest = hashlib.sha256(state.encode()).hexdigest()
    row = session.get(GitHubAuthStateRow, digest)
    if row is None or row.user_id != user_id or _utc(row.expires_at) <= datetime.now(UTC):
        raise ValueError("Invalid or expired GitHub authorisation")
    session.delete(row)
    session.commit()


def github_token(**data: str) -> dict[str, str]:
    """Exchange a code or refresh token at GitHub's token endpoint."""
    response = httpx.post(
        "https://github.com/login/oauth/access_token",
        headers={"Accept": "application/json"},
        data={
            "client_id": config.GITHUB_APP_CLIENT_ID,
            "client_secret": config.GITHUB_APP_CLIENT_SECRET,
            **data,
        },
        timeout=10,
    )
    response.raise_for_status()
    return _github_secret(response.json())


def github_repositories(row: GitConnectionRow, session: Session) -> list[dict]:
    token = connection_credentials(row, session).token
    headers = {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}"}
    response = httpx.get(
        "https://api.github.com/user/installations",
        headers=headers,
        params={"per_page": 100},
        timeout=10,
    )
    response.raise_for_status()
    repos: list[dict] = []
    for installation in response.json().get("installations", []):
        listed = httpx.get(
            f"https://api.github.com/user/installations/{installation['id']}/repositories",
            headers=headers,
            params={"per_page": 100},
            timeout=10,
        )
        listed.raise_for_status()
        repos.extend(
            {"name": item["full_name"], "url": item["clone_url"]}
            for item in listed.json().get("repositories", [])
        )
    return repos


def connection_to_dict(row: GitConnectionRow, session: Session) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "kind": row.kind,
        "host": row.host,
        "status": (
            "disconnected"
            if row.disconnected_at is not None or not row.encrypted_secret
            else "connected"
        ),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "affected_repositories": session.scalar(
            select(func.count()).where(RepoRow.connection_id == row.id)
        )
        or 0,
    }
