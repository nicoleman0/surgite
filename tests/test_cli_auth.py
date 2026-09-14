"""CLI authentication and session-storage tests."""

import os
import stat

import pytest

from surgite import cli_auth, config
from surgite.db import UserRow, get_session


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Isolate session files and avoid the real OS keyring."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for var in ("SURGITE_API_KEY", "SURGITE_EMAIL", "SURGITE_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    cli_auth.use_file_fallback(True)
    yield
    cli_auth.use_file_fallback(False)


class _FakeKeyring:
    """In-memory keyring backend."""

    def __init__(self):
        self.store: dict[tuple[str, str], str] = {}

    def set_password(self, service, user, value):
        self.store[(service, user)] = value

    def get_password(self, service, user):
        return self.store.get((service, user))

    def delete_password(self, service, user):
        if (service, user) not in self.store:
            raise cli_auth.keyring.errors.PasswordDeleteError("not found")
        del self.store[(service, user)]


@pytest.fixture
def fake_keyring(monkeypatch):
    kr = _FakeKeyring()
    monkeypatch.setattr(cli_auth.keyring, "set_password", kr.set_password)
    monkeypatch.setattr(cli_auth.keyring, "get_password", kr.get_password)
    monkeypatch.setattr(cli_auth.keyring, "delete_password", kr.delete_password)
    monkeypatch.setattr(cli_auth, "_keyring_available", lambda: True)
    cli_auth.use_file_fallback(False)
    return kr


def test_session_round_trips_through_keyring(fake_keyring):
    cli_auth.save_session("http://api", "n", "v")
    assert not cli_auth.session_file().exists()
    assert cli_auth.load_session() == {
        "api_url": "http://api",
        "cookie_name": "n",
        "cookie_value": "v",
    }
    cli_auth.clear_session()
    assert cli_auth.load_session() is None


def test_keyring_migration_from_0600_file(fake_keyring):
    cli_auth._write_session_file(
        '{"api_url": "http://api", "cookie_name": "n", "cookie_value": "old"}'
    )
    assert cli_auth.session_file().exists()
    sess = cli_auth.load_session()
    assert sess["cookie_value"] == "old"
    assert not cli_auth.session_file().exists(), "file should be shredded after migration"
    assert fake_keyring.get_password(cli_auth._KEYRING_SERVICE, cli_auth._KEYRING_USER) is not None


def test_keyring_migration_from_legacy_service_name(fake_keyring):
    blob = '{"api_url": "http://api", "cookie_name": "n", "cookie_value": "v"}'
    fake_keyring.set_password(cli_auth._LEGACY_KEYRING_SERVICE, cli_auth._KEYRING_USER, blob)
    sess = cli_auth.load_session()
    assert sess["cookie_value"] == "v"
    assert fake_keyring.get_password(cli_auth._KEYRING_SERVICE, cli_auth._KEYRING_USER) == blob
    assert (
        fake_keyring.get_password(cli_auth._LEGACY_KEYRING_SERVICE, cli_auth._KEYRING_USER) is None
    )


def test_file_migration_from_legacy_config_dir(fake_keyring):
    legacy = cli_auth._legacy_session_file()
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text('{"api_url": "http://api", "cookie_name": "n", "cookie_value": "old"}')
    assert legacy.exists()
    sess = cli_auth.load_session()
    assert sess["cookie_value"] == "old"
    assert not legacy.exists(), "old-path file should be shredded after migration"
    assert fake_keyring.get_password(cli_auth._KEYRING_SERVICE, cli_auth._KEYRING_USER) is not None


def test_save_load_clear_session(tmp_path):
    assert cli_auth.load_session() is None
    cli_auth.save_session("http://api", "__Host-surgite_session", "abc123")
    sess = cli_auth.load_session()
    assert sess == {
        "api_url": "http://api",
        "cookie_name": "__Host-surgite_session",
        "cookie_value": "abc123",
    }
    cli_auth.clear_session()
    assert cli_auth.load_session() is None


def test_session_file_is_0600():
    cli_auth.save_session("http://api", "n", "v")
    mode = stat.S_IMODE(os.stat(cli_auth.session_file()).st_mode)
    assert mode == 0o600


def test_load_session_tolerates_corruption():
    path = cli_auth.session_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json{")
    assert cli_auth.load_session() is None


@pytest.mark.parametrize(
    "header,expected",
    [
        ("__Host-surgite_session=xyz; HttpOnly; Secure", ("__Host-surgite_session", "xyz")),
        ("session=abc", ("session", "abc")),
        ("", (None, None)),
        (None, (None, None)),
        ("no-equals-sign; Path=/", (None, None)),
    ],
)
def test_parse_set_cookie(header, expected):
    assert cli_auth._parse_set_cookie(header) == expected


def test_resolve_api_key_reads_env(monkeypatch):
    monkeypatch.setenv("SURGITE_API_KEY", "k")
    assert cli_auth.resolve_api_key() == "k"


def test_resolve_api_key_none_when_unset():
    assert cli_auth.resolve_api_key() is None


def test_auth_headers_prefers_matching_session():
    cli_auth.save_session("http://api", "__Host-surgite_session", "sid42")
    headers = cli_auth.auth_headers("http://api")
    assert headers == {"Cookie": "__Host-surgite_session=sid42"}


def test_auth_headers_ignores_session_for_other_url(monkeypatch):
    cli_auth.save_session("http://other", "n", "v")
    monkeypatch.setenv("SURGITE_API_KEY", "k")
    assert cli_auth.auth_headers("http://api") == {"Authorization": "Bearer k"}


def test_auth_headers_empty_when_nothing_configured():
    assert cli_auth.auth_headers("http://api") == {}


def test_cmd_login_saves_session(monkeypatch):
    """cmd_login parses the Set-Cookie from a stubbed response and saves it."""

    class FakeResp:
        status_code = 200
        headers = {"set-cookie": "__Host-surgite_session=tok99; HttpOnly; Secure"}

        @staticmethod
        def json():
            return {"email": "nick@example.com"}

    monkeypatch.setattr(cli_auth.httpx, "post", lambda *a, **k: FakeResp())
    rc = cli_auth.cmd_login("http://api", email="nick@example.com", password="pw")
    assert rc == 0
    assert cli_auth.load_session()["cookie_value"] == "tok99"


def test_cmd_login_failure_returns_1(monkeypatch):
    class FakeResp:
        status_code = 401
        text = "nope"
        headers: dict = {}

    monkeypatch.setattr(cli_auth.httpx, "post", lambda *a, **k: FakeResp())
    assert cli_auth.cmd_login("http://api", email="a@b.c", password="x") == 1
    assert cli_auth.load_session() is None


def test_cmd_logout_clears_session(monkeypatch):
    cli_auth.save_session("http://api", "n", "v")
    monkeypatch.setattr(cli_auth.httpx, "post", lambda *a, **k: None)
    assert cli_auth.cmd_logout("http://api") == 0
    assert cli_auth.load_session() is None


def test_cmd_redeem_invite_creates_account_and_saves_session(monkeypatch):
    """cmd_redeem_invite POSTs {token, password, [email]} and saves the cookie."""

    class FakeResp:
        status_code = 201
        headers = {"set-cookie": "surgite_session=newkid; HttpOnly"}

        @staticmethod
        def json():
            return {"email": "newkid@example.com"}

    captured: dict = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["body"] = json
        return FakeResp()

    monkeypatch.setattr(cli_auth.httpx, "post", fake_post)
    rc = cli_auth.cmd_redeem_invite(
        "http://api", "inv-abc", password="pw", email="newkid@example.com"
    )
    assert rc == 0
    assert captured["url"] == "http://api/auth/redeem-invite"
    assert captured["body"] == {"token": "inv-abc", "password": "pw", "email": "newkid@example.com"}
    sess = cli_auth.load_session()
    assert sess == {
        "api_url": "http://api",
        "cookie_name": "surgite_session",
        "cookie_value": "newkid",
    }


def test_cmd_redeem_invite_omits_email_when_unset(monkeypatch):
    """When no email is provided, the request body must not carry an email key."""

    class FakeResp:
        status_code = 201
        headers = {"set-cookie": "surgite_session=tok"}
        json = staticmethod(lambda: {"email": "x@example.com"})

    captured: dict = {}
    monkeypatch.setattr(
        cli_auth.httpx,
        "post",
        lambda url, json=None, timeout=None: captured.update(body=json) or FakeResp(),
    )
    assert cli_auth.cmd_redeem_invite("http://api", "inv-abc", password="pw") == 0
    assert "email" not in captured["body"]


def test_cmd_redeem_invite_failure_returns_1(monkeypatch):
    class FakeResp:
        status_code = 401
        text = "bad token"
        headers: dict = {}

    monkeypatch.setattr(cli_auth.httpx, "post", lambda *a, **k: FakeResp())
    assert cli_auth.cmd_redeem_invite("http://api", "inv-abc", password="pw") == 1
    assert cli_auth.load_session() is None


def test_cmd_redeem_invite_succeeds_without_cookie(monkeypatch):
    class FakeResp:
        status_code = 201
        headers: dict = {}
        json = staticmethod(lambda: {"email": "x@example.com"})

    monkeypatch.setattr(cli_auth.httpx, "post", lambda *a, **k: FakeResp())
    assert cli_auth.cmd_redeem_invite("http://api", "inv-abc", password="pw") == 0
    assert cli_auth.load_session() is None


def _password_prompts(monkeypatch, *values):
    answers = iter(values)
    monkeypatch.setattr(cli_auth.getpass, "getpass", lambda prompt: next(answers))


def test_cmd_bootstrap_admin_uses_default_email_and_keeps_secrets_out_of_output(
    monkeypatch, capsys
):
    monkeypatch.setattr(config, "AUTH_MODE", "multi_user")
    monkeypatch.setattr(config, "BOOTSTRAP_OWNER_EMAIL", "owner@example.com")
    _password_prompts(monkeypatch, "correct-horse-battery", "correct-horse-battery")

    assert cli_auth.cmd_bootstrap_admin() == 0

    output = capsys.readouterr()
    assert "owner@example.com" in output.out
    assert "correct-horse-battery" not in output.out + output.err
    assert "token" not in (output.out + output.err).lower()
    with get_session() as session:
        user = session.query(UserRow).one()
        assert user.email == "owner@example.com" and user.is_admin


def test_cmd_bootstrap_admin_accepts_email_override(monkeypatch, capsys):
    monkeypatch.setattr(config, "AUTH_MODE", "multi_user")
    monkeypatch.setattr(config, "BOOTSTRAP_OWNER_EMAIL", "ignored@example.com")
    _password_prompts(monkeypatch, "correct-horse-battery", "correct-horse-battery")

    assert cli_auth.cmd_bootstrap_admin("override@example.com") == 0
    assert "override@example.com" in capsys.readouterr().out


def test_cmd_bootstrap_admin_requires_matching_passwords(monkeypatch, capsys):
    monkeypatch.setattr(config, "AUTH_MODE", "multi_user")
    _password_prompts(monkeypatch, "correct-horse-battery", "different-password")

    assert cli_auth.cmd_bootstrap_admin("owner@example.com") == 1
    assert "do not match" in capsys.readouterr().err
    with get_session() as session:
        assert session.query(UserRow).count() == 0


def test_cmd_bootstrap_admin_requires_eight_character_password(monkeypatch, capsys):
    monkeypatch.setattr(config, "AUTH_MODE", "multi_user")
    _password_prompts(monkeypatch, "short", "short")

    assert cli_auth.cmd_bootstrap_admin("owner@example.com") == 1
    assert "at least 8 characters" in capsys.readouterr().err
    with get_session() as session:
        assert session.query(UserRow).count() == 0


def test_cmd_bootstrap_admin_returns_failure_for_wrong_auth_mode(monkeypatch, capsys):
    monkeypatch.setattr(config, "AUTH_MODE", "off")
    _password_prompts(monkeypatch, "correct-horse-battery", "correct-horse-battery")

    assert cli_auth.cmd_bootstrap_admin("owner@example.com") == 1
    assert "AUTH_MODE=multi_user" in capsys.readouterr().err
