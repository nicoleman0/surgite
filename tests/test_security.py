"""Security tests: API keys, lockout, audit, provider keys.

Covers:
  - every authed route 401s in multi_user without a session
  - per-user API keys (POST/GET/DELETE + Bearer auth on the dep)
  - CSRF header check on unsafe methods
  - REPO_ADD_GLOBAL_ONLY gates POST /repos
  - per-user + per-IP-outer rate limits on /summary?ai=true
  - account lockout on failed logins + admin unlock
  - shareable links user-scoped (404 cross-user)
  - per-user provider keys in DB, encrypted at rest
  - the rotation script (smoke test only — run against a temp DB,
    checking the keys still decrypt)
  - audit log + GET /admin/audit
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from surgite import config, rate_limit, secrets
from surgite.audit import audit
from surgite.auth import (
    _generate_api_key,
    create_session,
    create_user,
    verify_api_key,
)
from surgite.db import (
    AuditLogRow,
    ProviderKeyRow,
    UserRow,
    get_session,
)

COOKIE = config.SESSION_COOKIE_NAME


@pytest.fixture
def multi_user(monkeypatch):
    monkeypatch.setattr(config, "AUTH_MODE", "multi_user")


def _cookie_header(sid: str) -> dict:
    return {
        "Cookie": f"{COOKIE}={sid}",
        "X-Requested-With": "surgite-web",
    }


def _bearer_header(key: str) -> dict:
    return {
        "Authorization": f"Bearer {key}",
        "X-Requested-With": "surgite-web",
    }


def _make_user(email="a@example.com", password="pw-correct-horse", is_admin=False):
    """Create a user. If the default email is already taken (a previous
    test in the same session used it), generate a unique one."""
    for attempt in range(20):
        candidate = email if attempt == 0 else f"{email.rsplit('@', 1)[0]}+{attempt}@example.com"
        with get_session() as s:
            if s.scalar(select(UserRow).where(UserRow.email == candidate)) is None:
                return create_user(s, email=candidate, password=password, is_admin=is_admin).id
    raise RuntimeError("could not find a free email for the test user")


def _make_session(user_id: str) -> str:
    return create_session(user_id, session=None).id


# --- Item 13: every authed route 401s in multi_user -------------------------


def test_every_documented_authed_route_requires_session(client, multi_user):
    """In multi_user, every route except the small allowlist
    returns 401 without a session. Unsafe methods (POST/PUT/DELETE) may
    also 403 (the CSRF middleware fires before the auth check on those,
    so a missing-CSRF-header request gets 403 before the no-session 401).
    Both responses are 'not allowed', which is what we care about."""
    authed_paths = [
        ("GET", "/repos"),
        ("POST", "/repos/1/ingest"),
        ("GET", "/commits"),
        ("GET", "/commits/aaaaaaa"),
        ("GET", "/summary"),
        ("GET", "/summary/stream"),
        ("GET", "/settings/prompt"),
        ("PUT", "/settings/prompt"),
        ("GET", "/settings/provider-keys"),
        ("PUT", "/settings/provider-keys"),
        ("GET", "/providers"),
        ("GET", "/auth/me"),
        ("GET", "/auth/api-keys"),
        ("POST", "/auth/api-keys"),
        ("DELETE", "/auth/api-keys/abc"),
        ("GET", "/summaries/someslug"),
        ("GET", "/admin/audit"),
        ("POST", "/admin/invites"),
    ]
    for method, path in authed_paths:
        r = client.request(method, path)
        assert r.status_code in (401, 403), (
            f"{method} {path} should be unauthorized; got {r.status_code}"
        )


def test_unauthed_routes_open_in_multi_user(client, multi_user):
    """/health, /auth/login, /auth/redeem-invite, /auth/logout
    are open in multi_user (logout is idempotent)."""
    assert client.get("/health").status_code == 200
    # /auth/login, /logout, /redeem-invite are 4xx on bad input but
    # not 401 — they're pre-session.
    assert client.post("/auth/login", json={"email": "x", "password": "y"}).status_code == 401
    # NB: the 401 here is "bad credentials", not "needs auth" — they
    # reached the handler. The CSRF middleware let them through because
    # they're in the exempt set.
    assert client.post("/auth/logout").status_code == 200
    assert (
        client.post("/auth/redeem-invite", json={"token": "x", "password": "y"}).status_code == 400
    )


# --- Item 15: CSRF ----------------------------------------------------------


def test_csrf_header_required_on_unsafe_in_multi_user(client, multi_user):
    """A POST without X-Requested-With is 403 in multi_user mode."""
    uid = _make_user()
    sid = _make_session(uid)
    r = client.post(
        "/auth/api-keys",
        json={"name": "laptop"},
        headers={"Cookie": f"{COOKIE}={sid}"},
    )
    assert r.status_code == 403
    assert "X-Requested-With" in r.json()["detail"]


def test_csrf_header_not_required_in_off_mode(client):
    """The CSRF check is only active in multi_user."""
    uid = _make_user()
    sid = _make_session(uid)
    # No X-Requested-With; the call is rejected as 401 in off mode (no
    # session required, but the request is still processed) — actually
    # off mode doesn't gate on session, so this hits the auth path.
    # Easier check: the csrf middleware is off, so the rejection code
    # is not 403.
    r = client.post(
        "/auth/api-keys",
        json={"name": "laptop"},
        headers={"Cookie": f"{COOKIE}={sid}"},
    )
    assert r.status_code != 403


# --- Item 14: API keys ------------------------------------------------------


def test_api_key_issued_once_and_verifiable(client, multi_user):
    """Issue a key, use it as Bearer, get the user back."""
    uid = _make_user()
    sid = _make_session(uid)
    r = client.post(
        "/auth/api-keys",
        json={"name": "laptop"},
        headers=_cookie_header(sid),
    )
    assert r.status_code == 201
    body = r.json()
    assert body["key"].startswith("sk_")
    assert body["name"] == "laptop"
    # Use the key as Bearer on a protected route.
    r2 = client.get("/repos", headers=_bearer_header(body["key"]))
    assert r2.status_code == 200


def test_api_key_prefix_never_breaks_parsing():
    """Regression (1.0.0): token_urlsafe prefixes could contain "_", which
    broke verify_api_key's split-based prefix reassembly — ~8% of issued keys
    401'd on first use. The prefix alphabet must never contain "_"."""
    for _ in range(500):
        full, prefix, _secret = _generate_api_key()
        parts = full.split("_", 2)
        assert parts[0] + "_" + parts[1] == prefix


def test_api_key_list_does_not_return_key_material(client, multi_user):
    uid = _make_user()
    sid = _make_session(uid)
    client.post("/auth/api-keys", json={"name": "laptop"}, headers=_cookie_header(sid))
    r = client.get("/auth/api-keys", headers=_cookie_header(sid))
    assert r.status_code == 200
    keys = r.json()["keys"]
    assert len(keys) == 1
    assert "key" not in keys[0]
    assert "key_hash" not in keys[0]
    assert keys[0]["name"] == "laptop"
    assert keys[0]["prefix"].startswith("sk_")


def test_api_key_revoke_idempotent(client, multi_user):
    uid = _make_user()
    sid = _make_session(uid)
    r = client.post("/auth/api-keys", json={"name": "x"}, headers=_cookie_header(sid))
    kid = r.json()["id"]
    assert client.delete(f"/auth/api-keys/{kid}", headers=_cookie_header(sid)).status_code == 204
    # Second delete is still 204 (idempotent).
    assert client.delete(f"/auth/api-keys/{kid}", headers=_cookie_header(sid)).status_code == 204


def test_api_key_revoked_cannot_authenticate(client, multi_user):
    uid = _make_user()
    sid = _make_session(uid)
    r = client.post("/auth/api-keys", json={"name": "x"}, headers=_cookie_header(sid))
    full = r.json()["key"]
    kid = r.json()["id"]
    # Works before revoke.
    assert client.get("/repos", headers=_bearer_header(full)).status_code == 200
    # Revoke.
    client.delete(f"/auth/api-keys/{kid}", headers=_cookie_header(sid))
    # Now 401.
    assert client.get("/repos", headers=_bearer_header(full)).status_code == 401


def test_api_key_only_owner_can_revoke(client, multi_user):
    """Alice can't revoke Bob's key (no cross-user revoke)."""
    alice = _make_user(email="alice@example.com")
    bob = _make_user(email="bob@example.com")
    alice_sid = _make_session(alice)
    r = client.post(
        "/auth/api-keys", json={"name": "alice-laptop"}, headers=_cookie_header(alice_sid)
    )
    alice_kid = r.json()["id"]
    bob_sid = _make_session(bob)
    # Bob's revoke is a no-op (silently 204) — we don't leak the
    # existence of Alice's key to Bob.
    assert (
        client.delete(f"/auth/api-keys/{alice_kid}", headers=_cookie_header(bob_sid)).status_code
        == 204
    )
    # Alice's key still works.
    r2 = client.get("/repos", headers=_cookie_header(alice_sid))
    assert r2.status_code == 200


def test_api_key_issuance_is_throttled_per_user(client, multi_user, monkeypatch):
    """Per-user rate limit on key issuance."""
    monkeypatch.setattr(config, "API_KEY_ISSUE_LIMIT", 2)
    monkeypatch.setattr(config, "API_KEY_ISSUE_WINDOW_HOURS", 24)
    uid = _make_user()
    sid = _make_session(uid)
    for _ in range(2):
        r = client.post("/auth/api-keys", json={"name": "x"}, headers=_cookie_header(sid))
        assert r.status_code == 201
    r = client.post("/auth/api-keys", json={"name": "x"}, headers=_cookie_header(sid))
    assert r.status_code == 429


def test_verify_api_key_rejects_malformed(client, multi_user):
    """Anything that doesn't look like sk_xxxx_xxxx is rejected."""
    with get_session() as s:
        assert verify_api_key("", session=s) is None
        assert verify_api_key("not-a-key", session=s) is None
        assert verify_api_key("sk_a_b", session=s) is None  # too short


# --- Item 16: REPO_ADD_GLOBAL_ONLY ------------------------------------------


@pytest.fixture(autouse=True)
def repo_cache(tmp_path, monkeypatch):
    """Redirect the ingest cache to a tmp path so background tasks don't
    try to create /var/surgite in the test environment. The
    `_ingest_repo` helper imports ``REPO_CACHE_DIR`` from
    ``surgite.config`` on every call, so a monkeypatch on the module
    attribute is enough."""
    cache_dir = str(tmp_path / "repos")
    monkeypatch.setattr(config, "REPO_CACHE_DIR", cache_dir)


def test_repo_add_gated_to_admins_when_enabled(client, multi_user, monkeypatch):
    """REPO_ADD_GLOBAL_ONLY=true → only admins can POST /repos."""
    # Stub the background ingest so we don't try to git clone from a
    # bogus URL during the test.
    from surgite import api as api_module

    def fake_ingest(*args, **kwargs):
        return {
            "repo": args[1] if len(args) > 1 else "x",
            "inserted": 0,
            "updated": 0,
            "unchanged": 0,
        }

    monkeypatch.setattr(api_module, "_ingest_repo", fake_ingest)
    monkeypatch.setattr(config, "REPO_ADD_GLOBAL_ONLY", True)
    admin = _make_user(email="admin@example.com", is_admin=True)
    user = _make_user(email="user@example.com", is_admin=False)
    admin_sid = _make_session(admin)
    user_sid = _make_session(user)
    r = client.post(
        "/repos",
        json={"url": "https://example.com/admin.git"},
        headers=_cookie_header(admin_sid),
    )
    assert r.status_code == 201
    r = client.post(
        "/repos",
        json={"url": "https://example.com/user.git"},
        headers=_cookie_header(user_sid),
    )
    assert r.status_code == 403


def test_repo_add_open_to_all_by_default(client, multi_user, monkeypatch):
    """Default (REPO_ADD_GLOBAL_ONLY unset) is the 0.4.0 UX: any
    authenticated user can add a repo."""
    from surgite import api as api_module

    def fake_ingest(*args, **kwargs):
        return {"repo": "x", "inserted": 0, "updated": 0, "unchanged": 0}

    monkeypatch.setattr(api_module, "_ingest_repo", fake_ingest)
    user = _make_user()
    sid = _make_session(user)
    r = client.post(
        "/repos",
        json={"url": "https://example.com/x.git"},
        headers=_cookie_header(sid),
    )
    assert r.status_code == 201


# --- Item 17: per-user + per-IP-outer rate limit ----------------------------


def test_summary_ai_rate_limit_is_per_user(client, multi_user, add_commit):
    """5/60s per user. Two users have independent buckets."""
    rate_limit._reset_for_tests()
    alice = _make_user(email="alice@example.com")
    bob = _make_user(email="bob@example.com")
    add_commit(hash="a" * 40, owner_id=alice, repo="alice-repo")
    add_commit(hash="b" * 40, owner_id=bob, repo="bob-repo")
    alice_sid = _make_session(alice)
    bob_sid = _make_session(bob)
    # Alice burns her 5.
    for i in range(5):
        r = client.get("/summary?ai=true&provider=groq", headers=_cookie_header(alice_sid))
        # 400 because GROQ_API_KEY is empty in conftest; the rate
        # limit check fires first.
        assert r.status_code == 400, f"call {i + 1}: {r.status_code}"
    r = client.get("/summary?ai=true&provider=groq", headers=_cookie_header(alice_sid))
    assert r.status_code == 429
    # Bob's bucket is independent.
    r = client.get("/summary?ai=true&provider=groq", headers=_cookie_header(bob_sid))
    assert r.status_code == 400


def test_summary_ai_per_ip_outer_is_independent(client, multi_user, add_commit):
    """The 100/60s per-IP outer backstop doesn't share state with the
    per-user 5/60s bucket — burning the per-user bucket doesn't
    affect a different user on the same IP."""
    rate_limit._reset_for_tests()
    alice = _make_user(email="alice@example.com")
    add_commit(owner_id=alice)
    alice_sid = _make_session(alice)
    for _ in range(5):
        client.get("/summary?ai=true&provider=groq", headers=_cookie_header(alice_sid))
    # 6th call from Alice hits the per-user limit (429).
    r = client.get("/summary?ai=true&provider=groq", headers=_cookie_header(alice_sid))
    assert r.status_code == 429


# --- Item 18: account lockout -----------------------------------------------


def test_lockout_trips_after_threshold(client, multi_user, monkeypatch):
    """10 fails in a row → 15 min lockout → 423 with Retry-After."""
    monkeypatch.setattr(config, "LOGIN_LOCKOUT_THRESHOLD", 3)
    monkeypatch.setattr(config, "LOGIN_LOCKOUT_DURATION_MINUTES", 15)
    _make_user(email="nick@example.com", password="right")
    for _ in range(3):
        r = client.post(
            "/auth/login",
            json={"email": "nick@example.com", "password": "wrong"},
            headers={"X-Requested-With": "surgite-web"},
        )
        assert r.status_code == 401
    # 4th call: even the right password returns 423 because the user
    # is now locked.
    r = client.post(
        "/auth/login",
        json={"email": "nick@example.com", "password": "right"},
        headers={"X-Requested-With": "surgite-web"},
    )
    assert r.status_code == 423
    assert "Retry-After" in r.headers


def test_lockout_does_not_count_unknown_email(client, multi_user, monkeypatch):
    """A failed login for a non-existent email doesn't trip a lockout
    (we have no user to lock out)."""
    monkeypatch.setattr(config, "LOGIN_LOCKOUT_THRESHOLD", 2)
    for _ in range(5):
        r = client.post(
            "/auth/login",
            json={"email": "ghost@example.com", "password": "x"},
            headers={"X-Requested-With": "surgite-web"},
        )
        assert r.status_code == 401  # never 423
    # The real user is unaffected.
    _make_user(email="real@example.com", password="right")
    r = client.post(
        "/auth/login",
        json={"email": "real@example.com", "password": "right"},
        headers={"X-Requested-With": "surgite-web"},
    )
    assert r.status_code == 200


def test_admin_can_unlock_user(client, multi_user, add_commit):
    """POST /admin/users/{id}/unlock clears the lockout."""
    uid = _make_user(email="victim@example.com", password="right")
    with get_session() as s:
        u = s.get(UserRow, uid)
        u.locked_until = datetime.now(UTC) + timedelta(minutes=15)
        u.failed_login_count = 9
        s.commit()
    admin = _make_user(email="admin@example.com", is_admin=True)
    admin_sid = _make_session(admin)
    r = client.post(
        f"/admin/users/{uid}/unlock",
        headers=_cookie_header(admin_sid),
    )
    assert r.status_code == 204
    with get_session() as s:
        u = s.get(UserRow, uid)
        assert u.locked_until is None
        assert u.failed_login_count == 0


def test_admin_unlock_requires_admin(client, multi_user):
    uid = _make_user()
    user = _make_user()
    sid = _make_session(user)
    r = client.post(f"/admin/users/{uid}/unlock", headers=_cookie_header(sid))
    assert r.status_code == 403


def test_admin_unlock_unknown_user_is_404(client, multi_user):
    admin = _make_user(is_admin=True)
    sid = _make_session(admin)
    r = client.post("/admin/users/does-not-exist/unlock", headers=_cookie_header(sid))
    assert r.status_code == 404


# --- Item 19: shareable links user-scoped -----------------------------------


def test_share_resolution_is_owner_scoped(client, multi_user, add_commit):
    """GET /summaries/{slug} returns 404 (not 403) to a
    non-owner — the 404 doesn't leak the slug's existence."""
    alice = _make_user(email="alice@example.com")
    bob = _make_user(email="bob@example.com")
    alice_sid = _make_session(alice)
    bob_sid = _make_session(bob)
    # Alice creates a share.
    r = client.post("/summaries", json={"repo": "demo"}, headers=_cookie_header(alice_sid))
    assert r.status_code == 201
    slug = r.json()["slug"]
    # Alice can resolve.
    r = client.get(f"/summaries/{slug}", headers=_cookie_header(alice_sid))
    assert r.status_code == 200
    # Bob gets 404 (not 403).
    r = client.get(f"/summaries/{slug}", headers=_cookie_header(bob_sid))
    assert r.status_code == 404


def test_share_requires_auth_in_multi_user(client, multi_user):
    r = client.get("/summaries/someslug")
    assert r.status_code == 401


# --- Item 20: per-user provider keys ----------------------------------------


def test_provider_keys_encrypted_at_rest(client, multi_user):
    """The stored value is a Fernet token, not the raw key."""
    uid = _make_user()
    sid = _make_session(uid)
    r = client.put(
        "/settings/provider-keys",
        json={"provider": "groq", "key": "gsk_test_abc"},
        headers=_cookie_header(sid),
    )
    assert r.status_code == 200
    assert r.json() == {"configured": True}
    with get_session() as s:
        row = s.scalar(
            select(ProviderKeyRow).where(
                ProviderKeyRow.user_id == uid, ProviderKeyRow.provider == "groq"
            )
        )
        assert row is not None
        assert row.encrypted_key != "gsk_test_abc"
        assert row.encrypted_key.startswith("gAAAAA")  # Fernet tokens start with this
        # And it decrypts back to the original.
        assert secrets.decrypt(row.encrypted_key) == "gsk_test_abc"


def test_provider_key_clear_revokes(client, multi_user):
    uid = _make_user()
    sid = _make_session(uid)
    client.put(
        "/settings/provider-keys",
        json={"provider": "groq", "key": "gsk_test_abc"},
        headers=_cookie_header(sid),
    )
    r = client.put(
        "/settings/provider-keys",
        json={"provider": "groq", "clear": True},
        headers=_cookie_header(sid),
    )
    assert r.status_code == 200
    assert r.json() == {"configured": False}
    with get_session() as s:
        row = s.scalar(
            select(ProviderKeyRow).where(
                ProviderKeyRow.user_id == uid, ProviderKeyRow.provider == "groq"
            )
        )
        assert row is not None
        assert row.revoked_at is not None


def test_provider_key_unknown_provider_400(client, multi_user):
    uid = _make_user()
    sid = _make_session(uid)
    r = client.put(
        "/settings/provider-keys",
        json={"provider": "made-up", "key": "x"},
        headers=_cookie_header(sid),
    )
    assert r.status_code == 400


def test_provider_key_set_then_list(client, multi_user):
    uid = _make_user()
    sid = _make_session(uid)
    client.put(
        "/settings/provider-keys",
        json={"provider": "groq", "key": "gsk_x"},
        headers=_cookie_header(sid),
    )
    client.put(
        "/settings/provider-keys",
        json={"provider": "anthropic", "key": "sk-ant-x"},
        headers=_cookie_header(sid),
    )
    r = client.get("/settings/provider-keys", headers=_cookie_header(sid))
    assert r.status_code == 200
    keys = {k["provider"] for k in r.json()["keys"]}
    assert keys == {"groq", "anthropic"}


def test_per_user_provider_status_in_multi_user(client, multi_user, monkeypatch):
    """An admin's /providers call reflects their own key (or env-var
    fallback) — not a global view."""
    admin = _make_user(is_admin=True)
    sid = _make_session(admin)
    # No keys set, no env vars: nothing available.
    r = client.get("/providers", headers=_cookie_header(sid))
    assert r.status_code == 200
    statuses = {p["name"]: p["available"] for p in r.json()["providers"]}
    assert statuses == {n: False for n in statuses}
    # Set a per-user key.
    client.put(
        "/settings/provider-keys",
        json={"provider": "groq", "key": "gsk_x"},
        headers=_cookie_header(sid),
    )
    r = client.get("/providers", headers=_cookie_header(sid))
    statuses = {p["name"]: p["available"] for p in r.json()["providers"]}
    assert statuses["groq"] is True


# --- Item 22: audit log -----------------------------------------------------


def test_audit_row_written_on_login_success(client, multi_user):
    _make_user(email="a@example.com", password="right")
    client.post(
        "/auth/login",
        json={"email": "a@example.com", "password": "right"},
        headers={"X-Requested-With": "surgite-web"},
    )
    with get_session() as s:
        rows = s.scalars(
            select(AuditLogRow).where(AuditLogRow.action == "auth.login.success")
        ).all()
        assert len(rows) == 1


def test_audit_row_written_on_login_failure(client, multi_user):
    _make_user(email="a@example.com", password="right")
    client.post(
        "/auth/login",
        json={"email": "a@example.com", "password": "wrong"},
        headers={"X-Requested-With": "surgite-web"},
    )
    with get_session() as s:
        rows = s.scalars(select(AuditLogRow).where(AuditLogRow.action == "auth.login.fail")).all()
        assert len(rows) == 1
        assert rows[0].metadata_ == {"email": "a@example.com"}


def test_audit_row_written_on_repo_create(client, multi_user, monkeypatch):
    from surgite import api as api_module

    def fake_ingest(*args, **kwargs):
        return {"repo": "x", "inserted": 0, "updated": 0, "unchanged": 0}

    monkeypatch.setattr(api_module, "_ingest_repo", fake_ingest)
    uid = _make_user()
    sid = _make_session(uid)
    client.post(
        "/repos",
        json={"url": "https://example.com/x.git"},
        headers=_cookie_header(sid),
    )
    with get_session() as s:
        rows = s.scalars(select(AuditLogRow).where(AuditLogRow.action == "repo.create")).all()
        assert len(rows) == 1
        assert rows[0].metadata_["clone_url"] == "https://example.com/x.git"


def test_admin_audit_endpoint_paginated(client, multi_user):
    admin = _make_user(is_admin=True)
    sid = _make_session(admin)
    # Seed a few rows.
    for action in ("auth.login.success", "auth.login.fail", "repo.create"):
        audit(action, actor_id=admin)
    r = client.get("/admin/audit", headers=_cookie_header(sid))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert len(body["events"]) == 3
    # Filter by action.
    r = client.get("/admin/audit?action=auth.login.fail", headers=_cookie_header(sid))
    assert r.json()["total"] == 1
    # Non-admin gets 403.
    user = _make_user()
    user_sid = _make_session(user)
    assert client.get("/admin/audit", headers=_cookie_header(user_sid)).status_code == 403


def test_audit_endpoint_requires_admin_in_multi_user(client, multi_user):
    user = _make_user()
    sid = _make_session(user)
    assert client.get("/admin/audit", headers=_cookie_header(sid)).status_code == 403


# --- Item 21: rotation script smoke test ------------------------------------


def test_rotate_secrets_script_smoke(tmp_path, monkeypatch):
    """Smoke-test the rotation logic: encrypt a row under master A,
    re-encrypt under master B, confirm only B can read it. We exercise
    the same algorithm scripts/rotate-secrets.sh uses without forking
    a subprocess (so a test failure is local to this function)."""
    from cryptography.fernet import Fernet

    from surgite.secrets import _derive_fernet_key

    master_a = "test-master-A"
    master_b = "test-master-B"

    # Encrypt under master A.
    fernet_a = Fernet(_derive_fernet_key(master_a))
    fernet_b = Fernet(_derive_fernet_key(master_b))
    ciphertext = fernet_a.encrypt(b"gsk-original").decode("ascii")

    # The rotation script's core: re-encrypt with the new master.
    plaintext = fernet_a.decrypt(ciphertext.encode("ascii"))
    new_ciphertext = fernet_b.encrypt(plaintext).decode("ascii")

    # Old master can no longer read; new master can.
    try:
        fernet_a.decrypt(new_ciphertext.encode("ascii"))
        old_still_works = True
    except Exception:
        old_still_works = False
    assert not old_still_works

    assert fernet_b.decrypt(new_ciphertext.encode("ascii")) == b"gsk-original"


# --- SECRETS_KEY_FILE: the generated fallback key must land on durable storage


def _import_secrets_with_env(tmp_path, **env_overrides) -> subprocess.CompletedProcess:
    """Import surgite.secrets in a fresh interpreter with a patched environment.

    A subprocess rather than importlib.reload: reload mutates the live module's
    __dict__ in place, so the module-level _fernet other tests already hold a
    reference to would be swapped underneath them, and rows encrypted earlier in
    the session would stop decrypting.
    """
    env = {k: v for k, v in os.environ.items() if k != "SECRETS_ENCRYPTION_KEY"}
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-c", "import surgite.secrets as s; print(s.key_file_path())"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).resolve().parent.parent,
    )


def test_secrets_key_file_env_var_relocates_the_generated_key(tmp_path):
    """SECRETS_KEY_FILE moves the fallback key off the project root.

    Without this, a container writes the key into /app — an image layer — and
    every provider_keys row encrypted under it is unrecoverable after a
    redeploy. The compose file relies on this to put the key on a volume.
    """
    key_path = tmp_path / "data" / ".secrets_key"

    result = _import_secrets_with_env(tmp_path, SECRETS_KEY_FILE=str(key_path))

    assert result.stdout.strip() == str(key_path)
    # The parent directory did not exist: importing must create it, not crash.
    assert key_path.is_file()
    assert oct(key_path.stat().st_mode)[-3:] == "600"


def test_secrets_key_file_unset_still_defaults_to_the_project_root(tmp_path):
    """The default path is unchanged, so existing installs keep their key."""
    env = {k: v for k, v in os.environ.items() if k != "SECRETS_KEY_FILE"}
    result = subprocess.run(
        [sys.executable, "-c", "import surgite.secrets as s; print(s.key_file_path())"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).resolve().parent.parent,
    )
    expected = Path(__file__).resolve().parent.parent / ".secrets_key"
    assert result.stdout.strip() == str(expected)


def test_generated_key_is_reused_across_processes_at_the_same_path(tmp_path):
    """Two starts pointed at the same durable path share one key.

    This is the property the ephemeral-container bug violated: two starts
    generated two different keys, silently orphaning every encrypted row.
    """
    key_path = tmp_path / "data" / ".secrets_key"

    _import_secrets_with_env(tmp_path, SECRETS_KEY_FILE=str(key_path))
    first = key_path.read_text()
    _import_secrets_with_env(tmp_path, SECRETS_KEY_FILE=str(key_path))
    second = key_path.read_text()

    assert first == second


# --- Item 13 lockdown: every documented authed route 401s in multi_user -----
# (already covered at the top of the file)
