"""CLI authentication and session persistence."""

import getpass
import json
import os
import sys
from pathlib import Path

import httpx
import keyring
import keyring.errors

_KEYRING_SERVICE = "surgite"
_KEYRING_USER = "session"
_LEGACY_KEYRING_SERVICE = "standup-gen"

_force_file = False


def use_file_fallback(force: bool) -> None:
    """Choose the file backend instead of the OS keyring."""
    global _force_file
    _force_file = force


def _keyring_available() -> bool:
    """Return whether a usable OS keyring backend is configured."""
    if _force_file:
        return False
    try:
        from keyring.backends.fail import Keyring as _FailKeyring

        return not isinstance(keyring.get_keyring(), _FailKeyring)
    except Exception:
        return False


def _xdg_base() -> Path:
    return Path(
        os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    )


def _config_dir() -> Path:
    return _xdg_base() / "surgite"


def _legacy_session_file() -> Path:
    return _xdg_base() / "standup" / "session"


def session_file() -> Path:
    return _config_dir() / "session"


def _write_session_file(blob: str) -> None:
    d = _config_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = session_file()
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text(blob)


def _read_session_file() -> dict | None:
    for path in (session_file(), _legacy_session_file()):
        if not path.exists():
            continue
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError, OSError:
            return None
    return None


def _shred_session_file() -> None:
    """Best-effort overwrite and remove current and legacy session files."""
    for path in (session_file(), _legacy_session_file()):
        if not path.exists():
            continue
        try:
            with open(path, "r+b") as f:
                f.write(os.urandom(max(path.stat().st_size, 1)))
                f.flush()
                os.fsync(f.fileno())
        except OSError:
            pass
        path.unlink(missing_ok=True)


def save_session(api_url: str, cookie_name: str, cookie_value: str) -> None:
    """Persist the session cookie for `api_url` (keyring, or 0600 file)."""
    blob = json.dumps(
        {"api_url": api_url, "cookie_name": cookie_name, "cookie_value": cookie_value}
    )
    if _keyring_available():
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_USER, blob)
        _shred_session_file()
        return
    _write_session_file(blob)


def load_session() -> dict | None:
    if not _keyring_available():
        return _read_session_file()
    try:
        blob = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USER)
        legacy = (
            None
            if blob is not None
            else keyring.get_password(_LEGACY_KEYRING_SERVICE, _KEYRING_USER)
        )
    except keyring.errors.KeyringError:
        return _read_session_file()
    if legacy is not None:
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_USER, legacy)
        keyring.delete_password(_LEGACY_KEYRING_SERVICE, _KEYRING_USER)
        print("Migrated CLI session from the old keyring service name.", file=sys.stderr)
        blob = legacy
    if blob is None:
        migrated = _read_session_file()
        if migrated is None:
            return None
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_USER, json.dumps(migrated))
        _shred_session_file()
        print("Migrated CLI session from the 0600 file into the OS keyring.", file=sys.stderr)
        return migrated
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        return None


def clear_session() -> None:
    if _keyring_available():
        for service in (_KEYRING_SERVICE, _LEGACY_KEYRING_SERVICE):
            try:
                keyring.delete_password(service, _KEYRING_USER)
            except keyring.errors.PasswordDeleteError:
                pass
    _shred_session_file()


def _parse_set_cookie(header: str | None) -> tuple[str | None, str | None]:
    """Extract a name and value from a Set-Cookie header."""
    if not header:
        return None, None
    first = header.split(";", 1)[0].strip()
    if "=" not in first:
        return None, None
    name, value = first.split("=", 1)
    return name, value


def resolve_api_key() -> str | None:
    return os.environ.get("SURGITE_API_KEY")


def auth_headers(api_url: str) -> dict[str, str]:
    """Prefer a matching saved session, then a Bearer API key."""
    sess = load_session()
    if sess and sess.get("api_url") == api_url and sess.get("cookie_value"):
        return {"Cookie": f"{sess['cookie_name']}={sess['cookie_value']}"}
    key = resolve_api_key()
    if key:
        return {"Authorization": f"Bearer {key}"}
    return {}


def cmd_login(api_url: str, email: str | None = None, password: str | None = None) -> int:
    email = email or os.environ.get("SURGITE_EMAIL") or input("Email: ")
    password = password or os.environ.get("SURGITE_PASSWORD") or getpass.getpass("Password: ")
    try:
        resp = httpx.post(
            f"{api_url}/auth/login", json={"email": email, "password": password}, timeout=30
        )
    except httpx.HTTPError as e:
        print(f"Login failed: {e}", file=sys.stderr)
        return 1
    if resp.status_code != 200:
        print(f"Login failed: {resp.status_code} {resp.text}", file=sys.stderr)
        return 1
    name, value = _parse_set_cookie(resp.headers.get("set-cookie"))
    if not value:
        print("Login succeeded but no session cookie was returned.", file=sys.stderr)
        return 1
    save_session(api_url, name or "session", value)
    print(f"Logged in as {resp.json().get('email')}.")
    return 0


def cmd_logout(api_url: str) -> int:
    sess = load_session()
    headers = {}
    if sess and sess.get("cookie_value"):
        headers = {"Cookie": f"{sess['cookie_name']}={sess['cookie_value']}"}
    try:
        httpx.post(f"{api_url}/auth/logout", headers=headers, timeout=30)
    except httpx.HTTPError:
        pass  # Local cookie removal is what matters; best-effort server revoke.
    clear_session()
    print("Logged out.")
    return 0


def cmd_redeem_invite(
    api_url: str, token: str, password: str | None = None, email: str | None = None
) -> int:
    password = (
        password or os.environ.get("SURGITE_PASSWORD") or getpass.getpass("Choose a password: ")
    )
    body: dict[str, str] = {"token": token, "password": password}
    if email or os.environ.get("SURGITE_EMAIL"):
        body["email"] = email or os.environ["SURGITE_EMAIL"]
    try:
        resp = httpx.post(f"{api_url}/auth/redeem-invite", json=body, timeout=30)
    except httpx.HTTPError as e:
        print(f"Invite redemption failed: {e}", file=sys.stderr)
        return 1
    if resp.status_code != 201:
        print(f"Invite redemption failed: {resp.status_code} {resp.text}", file=sys.stderr)
        return 1
    name, value = _parse_set_cookie(resp.headers.get("set-cookie"))
    if value:
        save_session(api_url, name or "session", value)
    print(f"Account created; logged in as {resp.json().get('email')}.")
    return 0
