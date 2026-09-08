"""Auth foundation tests.

Covers the password/session primitives directly and the AUTH_MODE behaviour
through the API: off/single_user stay anonymous-equivalent, multi_user gates
on a session cookie and isolates data per owner.

The session cookie is `__Host-`-prefixed and Secure in the test config (DEBUG
is unset), so the TestClient's cookie jar won't store or replay it over plain
HTTP. Authenticated requests therefore send the cookie via an explicit Cookie
header, and the login/redeem responses are checked through their Set-Cookie
header rather than the jar.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from surgite import config
from surgite.auth import (
    create_invite,
    create_session,
    create_user,
    hash_password,
    purge_expired_sessions,
    revoke_session,
    verify_password,
)
from surgite.db import SessionRow, UserRow, get_session

COOKIE = config.SESSION_COOKIE_NAME


@pytest.fixture
def multi_user(monkeypatch):
    monkeypatch.setattr(config, "AUTH_MODE", "multi_user")


def _cookie_header(sid: str) -> dict:
    """Headers that carry a session cookie for an authenticated request.

    In multi_user mode the SPA also sends ``X-Requested-With: surgite-web``
    on every state-changing request (CSRF defence in depth).
    The test client mirrors that here so the auth+CSRF combination is
    exercised end-to-end."""
    return {
        "Cookie": f"{COOKIE}={sid}",
        "X-Requested-With": "surgite-web",
    }


def _make_user(email="a@example.com", password="pw-correct-horse", is_admin=False):
    with get_session() as s:
        return create_user(s, email=email, password=password, is_admin=is_admin).id


def _make_session(user_id: str) -> str:
    return create_session(user_id, session=None).id


# --- password hashing -------------------------------------------------------


def test_password_roundtrip():
    h = hash_password("s3cret-passphrase")
    assert h != "s3cret-passphrase"  # not plaintext
    assert verify_password("s3cret-passphrase", h)


def test_password_wrong_is_false():
    h = hash_password("right")
    assert not verify_password("wrong", h)


def test_verify_malformed_hash_is_false_not_raise():
    assert verify_password("anything", "not-a-real-argon2-hash") is False


# --- sessions ---------------------------------------------------------------


def test_create_and_revoke_session():
    uid = _make_user()
    sid = _make_session(uid)
    with get_session() as s:
        assert s.get(SessionRow, sid) is not None
    revoke_session(sid)
    with get_session() as s:
        assert s.get(SessionRow, sid) is None


def test_purge_expired_sessions():
    uid = _make_user()
    live = _make_session(uid)
    with get_session() as s:
        expired = SessionRow(
            id="expired-sid",
            user_id=uid,
            created_at=datetime.now(UTC) - timedelta(days=30),
            expires_at=datetime.now(UTC) - timedelta(days=1),
            last_seen_at=datetime.now(UTC) - timedelta(days=2),
        )
        s.add(expired)
        s.commit()
    assert purge_expired_sessions() == 1
    with get_session() as s:
        assert s.get(SessionRow, live) is not None
        assert s.get(SessionRow, "expired-sid") is None


def test_session_ip_is_truncated():
    uid = _make_user()
    sess = create_session(uid, ip="192.168.0.55", user_agent="x" * 500)
    assert sess.ip == "192.168.0.0/24"
    assert len(sess.user_agent or "") <= 256


# --- AUTH_MODE=off (default) keeps 0.4.0 behaviour --------------------------


def test_off_mode_no_auth_required(client, add_commit):
    add_commit()
    assert client.get("/repos").status_code == 200
    assert client.get("/commits").status_code == 200
    # /providers is open in off mode
    assert client.get("/providers").status_code == 200


def test_off_mode_auth_routes_404(client):
    # /login and /signup (GET) are SPA shell routes — they serve the build's
    # 200.html in prod, 404 in dev. They aren't auth handlers, so AUTH_MODE
    # doesn't gate them; the page itself decides what to render.
    assert client.post("/auth/login", json={"email": "a@b.c", "password": "x"}).status_code == 404
    assert client.post("/auth/logout").status_code == 404
    assert client.post("/signup", json={"token": "x", "password": "x"}).status_code == 404


# --- AUTH_MODE=multi_user ---------------------------------------------------


def test_multi_user_requires_session(client, multi_user):
    assert client.get("/repos").status_code == 401
    assert client.get("/commits").status_code == 401
    assert client.get("/summary").status_code == 401


def test_multi_user_login_and_authenticated_request(client, multi_user):
    _make_user(email="nick@example.com", password="correct-horse-battery")
    r = client.post(
        "/auth/login", json={"email": "nick@example.com", "password": "correct-horse-battery"}
    )
    assert r.status_code == 200
    assert r.json()["email"] == "nick@example.com"
    set_cookie = r.headers.get("set-cookie", "")
    assert COOKIE in set_cookie
    assert "HttpOnly" in set_cookie
    assert "samesite=lax" in set_cookie.lower()
    assert "Secure" in set_cookie  # DEBUG unset -> production cookie
    assert "Path=/" in set_cookie

    # Extract the session id and use it on a protected route.
    sid = set_cookie.split(f"{COOKIE}=", 1)[1].split(";", 1)[0]
    assert client.get("/repos", headers=_cookie_header(sid)).status_code == 200


def test_multi_user_login_bad_password_401(client, multi_user):
    _make_user(email="nick@example.com", password="the-right-one")
    r = client.post("/auth/login", json={"email": "nick@example.com", "password": "wrong"})
    assert r.status_code == 401


def test_multi_user_login_unknown_user_401(client, multi_user):
    r = client.post("/auth/login", json={"email": "ghost@example.com", "password": "x"})
    assert r.status_code == 401


def test_expired_session_rejected(client, multi_user):
    uid = _make_user()
    with get_session() as s:
        s.add(
            SessionRow(
                id="stale-sid",
                user_id=uid,
                created_at=datetime.now(UTC) - timedelta(days=30),
                expires_at=datetime.now(UTC) - timedelta(days=1),
                last_seen_at=datetime.now(UTC) - timedelta(days=2),
            )
        )
        s.commit()
    assert client.get("/repos", headers=_cookie_header("stale-sid")).status_code == 401


def test_logout_revokes_session(client, multi_user):
    uid = _make_user()
    sid = _make_session(uid)
    assert client.get("/repos", headers=_cookie_header(sid)).status_code == 200
    assert client.post("/auth/logout", headers=_cookie_header(sid)).status_code == 200
    assert client.get("/repos", headers=_cookie_header(sid)).status_code == 401


# --- invite redemption ------------------------------------------------------


def test_redeem_pinned_invite_creates_user_and_logs_in(client, multi_user):
    admin = _make_user(email="admin@example.com", is_admin=True)
    with get_session() as s:
        token = create_invite(s, email="newbie@example.com", role="user", created_by=admin).token
    r = client.post("/auth/redeem-invite", json={"token": token, "password": "brand-new-pass"})
    assert r.status_code == 201
    assert r.json()["email"] == "newbie@example.com"
    assert r.json()["is_admin"] is False
    sid = r.headers["set-cookie"].split(f"{COOKIE}=", 1)[1].split(";", 1)[0]
    assert (
        client.get("/auth/me", headers=_cookie_header(sid)).json()["email"] == "newbie@example.com"
    )


def test_redeem_admin_invite_grants_admin(client, multi_user):
    with get_session() as s:
        token = create_invite(s, email="boss@example.com", role="admin").token
    r = client.post("/auth/redeem-invite", json={"token": token, "password": "boss-pass-1234"})
    assert r.status_code == 201
    assert r.json()["is_admin"] is True


def test_redeem_used_invite_rejected(client, multi_user):
    with get_session() as s:
        token = create_invite(s, email="once@example.com").token
    assert (
        client.post(
            "/auth/redeem-invite", json={"token": token, "password": "first-pass-123"}
        ).status_code
        == 201
    )
    # Second redemption of the same token fails.
    r = client.post("/auth/redeem-invite", json={"token": token, "password": "second-pass-12"})
    assert r.status_code == 400


def test_redeem_unknown_invite_rejected(client, multi_user):
    r = client.post("/auth/redeem-invite", json={"token": "nope", "password": "whatever-123"})
    assert r.status_code == 400


# --- /signup alias ----------------------------------------------------------
# /signup and /auth/redeem-invite are aliases of the same handler; one
# parametrized test exercises both paths to keep the diff small.


@pytest.mark.parametrize("path", ["/auth/redeem-invite", "/signup"])
def test_signup_alias_creates_user_and_logs_in(client, multi_user, path):
    admin = _make_user(email="admin@example.com", is_admin=True)
    with get_session() as s:
        token = create_invite(s, email="newbie@example.com", role="user", created_by=admin).token
    r = client.post(path, json={"token": token, "password": "brand-new-pass"})
    assert r.status_code == 201
    assert r.json()["email"] == "newbie@example.com"
    sid = r.headers["set-cookie"].split(f"{COOKIE}=", 1)[1].split(";", 1)[0]
    assert (
        client.get("/auth/me", headers=_cookie_header(sid)).json()["email"] == "newbie@example.com"
    )


@pytest.mark.parametrize("path", ["/auth/redeem-invite", "/signup"])
def test_signup_alias_rejects_bad_token(client, multi_user, path):
    r = client.post(path, json={"token": "nope", "password": "whatever-123"})
    assert r.status_code == 400


# --- owner isolation --------------------------------------------------------


def test_repos_isolated_between_owners(client, multi_user, add_repo):
    alice = _make_user(email="alice@example.com")
    bob = _make_user(email="bob@example.com")
    add_repo(name="alice-repo", clone_url="https://example.com/alice.git", owner_id=alice)
    add_repo(name="bob-repo", clone_url="https://example.com/bob.git", owner_id=bob)

    alice_sid = _make_session(alice)
    bob_sid = _make_session(bob)

    alice_repos = client.get("/repos", headers=_cookie_header(alice_sid)).json()["repos"]
    bob_repos = client.get("/repos", headers=_cookie_header(bob_sid)).json()["repos"]
    assert [r["name"] for r in alice_repos] == ["alice-repo"]
    assert [r["name"] for r in bob_repos] == ["bob-repo"]


def test_commits_isolated_between_owners(client, multi_user, add_commit):
    alice = _make_user(email="alice@example.com")
    bob = _make_user(email="bob@example.com")
    add_commit(hash="a" * 40, repo="alice-repo", owner_id=alice)
    add_commit(hash="b" * 40, repo="bob-repo", owner_id=bob)

    alice_sid = _make_session(alice)
    out = client.get("/commits", headers=_cookie_header(alice_sid)).json()
    assert out["total"] == 1
    assert out["commits"][0]["repo"] == "alice-repo"


def test_cannot_delete_another_users_repo(client, multi_user, add_repo):
    alice = _make_user(email="alice@example.com")
    bob = _make_user(email="bob@example.com")
    alice_repo = add_repo(name="alice-repo", clone_url="https://example.com/a.git", owner_id=alice)
    bob_sid = _make_session(bob)
    # Bob can't see or delete Alice's repo -> 404 (not 403, no existence leak).
    assert client.delete(f"/repos/{alice_repo}", headers=_cookie_header(bob_sid)).status_code == 404


def test_cannot_ingest_another_users_repo(client, multi_user, add_repo):
    alice = _make_user(email="alice@example.com")
    bob = _make_user(email="bob@example.com")
    alice_repo = add_repo(name="alice-repo", clone_url="https://example.com/a.git", owner_id=alice)
    bob_sid = _make_session(bob)
    assert (
        client.post(f"/repos/{alice_repo}/ingest", headers=_cookie_header(bob_sid)).status_code
        == 404
    )


# --- /providers admin gating ------------------------------------------------


def test_providers_admin_only_in_multi_user(client, multi_user):
    admin = _make_user(email="admin@example.com", is_admin=True)
    regular = _make_user(email="user@example.com", is_admin=False)
    assert client.get("/providers", headers=_cookie_header(_make_session(admin))).status_code == 200
    assert (
        client.get("/providers", headers=_cookie_header(_make_session(regular))).status_code == 403
    )


# --- bootstrap invite (startup) ---------------------------------------------


def test_bootstrap_invite_minted_when_no_admin(multi_user):
    from surgite.auth import ensure_bootstrap_invite
    from surgite.db import InviteRow

    with get_session() as s:
        token = ensure_bootstrap_invite(s)
        assert token is not None
        invite = s.scalar(select(InviteRow).where(InviteRow.token == token))
        assert invite is not None and invite.role == "admin"
    # Idempotent: a second call returns the same still-unused token.
    with get_session() as s:
        assert ensure_bootstrap_invite(s) == token


def test_bootstrap_invite_skipped_when_admin_exists(multi_user):
    _make_user(email="admin@example.com", is_admin=True)
    from surgite.auth import ensure_bootstrap_invite

    with get_session() as s:
        assert ensure_bootstrap_invite(s) is None


def test_off_mode_no_bootstrap_invite():
    # AUTH_MODE defaults to off here; ensure_bootstrap_invite is a no-op.
    from surgite.auth import ensure_bootstrap_invite

    with get_session() as s:
        assert ensure_bootstrap_invite(s) is None


# --- Password change --------------------------------------------------------


def test_password_change_happy_path(client, multi_user):
    uid = _make_user(email="chg@example.com", password="old-pass-1234")
    sid = _make_session(uid)
    r = client.put(
        "/auth/password",
        headers=_cookie_header(sid),
        json={"current_password": "old-pass-1234", "new_password": "new-pass-5678"},
    )
    assert r.status_code == 204
    # New password works on a fresh login.
    r = client.post("/auth/login", json={"email": "chg@example.com", "password": "new-pass-5678"})
    assert r.status_code == 200


def test_password_change_wrong_current_returns_401(client, multi_user):
    uid = _make_user(email="chg@example.com", password="right-old")
    sid = _make_session(uid)
    r = client.put(
        "/auth/password",
        headers=_cookie_header(sid),
        json={"current_password": "WRONG", "new_password": "should-not-stick"},
    )
    assert r.status_code == 401
    # Old password still works.
    r = client.post("/auth/login", json={"email": "chg@example.com", "password": "right-old"})
    assert r.status_code == 200


def test_password_change_revokes_other_sessions(client, multi_user):
    uid = _make_user(email="chg@example.com", password="old-pass-1234")
    # The "other" session — created out-of-band, so the PUT request can
    # identify the calling session by its cookie and revoke the rest.
    other = _make_session(uid)
    sid = _make_session(uid)
    r = client.put(
        "/auth/password",
        headers=_cookie_header(sid),
        json={"current_password": "old-pass-1234", "new_password": "new-pass-5678"},
    )
    assert r.status_code == 204
    # The "other" session is gone.
    with get_session() as s:
        assert s.get(SessionRow, other) is None


def test_password_change_keeps_current_session(client, multi_user):
    uid = _make_user(email="chg@example.com", password="old-pass-1234")
    sid = _make_session(uid)
    r = client.put(
        "/auth/password",
        headers=_cookie_header(sid),
        json={"current_password": "old-pass-1234", "new_password": "new-pass-5678"},
    )
    assert r.status_code == 204
    # The same sid is still valid on a protected route.
    assert client.get("/auth/me", headers=_cookie_header(sid)).status_code == 200


def test_password_change_off_mode_returns_404(client):
    # AUTH_MODE defaults to off here.
    r = client.put("/auth/password", json={"current_password": "x", "new_password": "y"})
    assert r.status_code == 404


def test_password_change_single_user_mode_returns_404(client, monkeypatch):
    monkeypatch.setattr(config, "AUTH_MODE", "single_user")
    r = client.put("/auth/password", json={"current_password": "x", "new_password": "y"})
    assert r.status_code == 404


# --- Admin-mediated password reset ------------------------------------------


def test_admin_reset_password_mints_token(client, multi_user):
    admin = _make_user(email="admin@example.com", is_admin=True)
    target = _make_user(email="victim@example.com", password="old-pass-1234")
    r = client.post(
        f"/admin/users/{target}/reset-password",
        headers=_cookie_header(_make_session(admin)),
    )
    assert r.status_code == 201
    body = r.json()
    assert body["reset_token"].startswith("pr_")
    assert "expires_at" in body


def test_admin_reset_password_requires_admin(client, multi_user):
    target = _make_user(email="victim@example.com", password="old-pass-1234")
    attacker = _make_user(email="attacker@example.com")
    r = client.post(
        f"/admin/users/{target}/reset-password",
        headers=_cookie_header(_make_session(attacker)),
    )
    assert r.status_code == 403


def test_admin_reset_password_unknown_user_returns_404(client, multi_user):
    admin = _make_user(email="admin@example.com", is_admin=True)
    r = client.post(
        "/admin/users/does-not-exist/reset-password",
        headers=_cookie_header(_make_session(admin)),
    )
    assert r.status_code == 404


# --- Reset token redemption -------------------------------------------------


def test_reset_token_redeem_sets_new_password(client, multi_user):
    admin = _make_user(email="admin@example.com", is_admin=True)
    target = _make_user(email="victim@example.com", password="old-pass-1234")
    r = client.post(
        f"/admin/users/{target}/reset-password",
        headers=_cookie_header(_make_session(admin)),
    )
    token = r.json()["reset_token"]
    r = client.post(
        "/auth/password-reset/confirm",
        json={"token": token, "new_password": "freshly-chosen-pw"},
    )
    assert r.status_code == 204
    # New password works on a fresh login.
    r = client.post(
        "/auth/login", json={"email": "victim@example.com", "password": "freshly-chosen-pw"}
    )
    assert r.status_code == 200
    # Old password is gone.
    r = client.post(
        "/auth/login", json={"email": "victim@example.com", "password": "old-pass-1234"}
    )
    assert r.status_code == 401


def test_reset_token_redeem_revokes_all_sessions(client, multi_user):
    admin = _make_user(email="admin@example.com", is_admin=True)
    target = _make_user(email="victim@example.com", password="old-pass-1234")
    # Two existing sessions before the reset.
    keep = _make_session(target)
    other = _make_session(target)
    r = client.post(
        f"/admin/users/{target}/reset-password",
        headers=_cookie_header(_make_session(admin)),
    )
    token = r.json()["reset_token"]
    r = client.post(
        "/auth/password-reset/confirm",
        json={"token": token, "new_password": "freshly-chosen-pw"},
    )
    assert r.status_code == 204
    with get_session() as s:
        # All sessions for the target user are gone, even the one we
        # "kept" — reset is a full session wipe, not a partial one.
        assert s.get(SessionRow, keep) is None
        assert s.get(SessionRow, other) is None


def test_reset_token_redeem_clears_lockout(client, multi_user):
    from surgite.auth import record_login_failure

    admin = _make_user(email="admin@example.com", is_admin=True)
    target = _make_user(email="victim@example.com", password="old-pass-1234")
    # Force a lockout.
    with get_session() as s:
        user = s.get(UserRow, target)
        for _ in range(config.LOGIN_LOCKOUT_THRESHOLD):
            record_login_failure(user, session=s)
        assert user.locked_until is not None
    # Mint and redeem a reset token.
    r = client.post(
        f"/admin/users/{target}/reset-password",
        headers=_cookie_header(_make_session(admin)),
    )
    token = r.json()["reset_token"]
    r = client.post(
        "/auth/password-reset/confirm",
        json={"token": token, "new_password": "freshly-chosen-pw"},
    )
    assert r.status_code == 204
    # Login works with the new password (no more lockout).
    r = client.post(
        "/auth/login",
        json={"email": "victim@example.com", "password": "freshly-chosen-pw"},
    )
    assert r.status_code == 200


def test_reset_token_redeem_expired_returns_400(client, multi_user):
    from datetime import timedelta

    from surgite.auth import mint_password_reset
    from surgite.db import PasswordResetRow

    target = _make_user(email="victim@example.com", password="old-pass-1234")
    token, _ = mint_password_reset(target)
    # Force the row's expiry into the past.
    with get_session() as s:
        rid = token.split("_", 2)[1]
        row = s.get(PasswordResetRow, rid)
        assert row is not None
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        s.commit()
    r = client.post(
        "/auth/password-reset/confirm",
        json={"token": token, "new_password": "should-not-stick"},
    )
    assert r.status_code == 400


def test_reset_token_redeem_off_mode_returns_404(client):
    # AUTH_MODE defaults to off here.
    r = client.post(
        "/auth/password-reset/confirm",
        json={"token": "pr_a_a", "new_password": "x"},
    )
    assert r.status_code == 404


# --- Admin: users -----------------------------------------------------------


def test_admin_list_users_paginated(client, multi_user):
    admin = _make_user(email="admin@example.com", is_admin=True)
    _make_user(email="alice@example.com")
    _make_user(email="bob@example.com")
    r = client.get("/admin/users?limit=2", headers=_cookie_header(_make_session(admin)))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert len(body["users"]) == 2


def test_admin_list_users_filter_by_email(client, multi_user):
    admin = _make_user(email="admin@example.com", is_admin=True)
    _make_user(email="alice@example.com")
    _make_user(email="bob@example.com")
    r = client.get("/admin/users?q=ALI", headers=_cookie_header(_make_session(admin)))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["users"][0]["email"] == "alice@example.com"


def test_admin_list_users_non_admin_403(client, multi_user):
    regular = _make_user(email="user@example.com")
    assert (
        client.get("/admin/users", headers=_cookie_header(_make_session(regular))).status_code
        == 403
    )


def test_admin_deactivate_user(client, multi_user):
    from surgite.db import AuditLogRow

    admin = _make_user(email="admin@example.com", is_admin=True)
    target = _make_user(email="victim@example.com")
    r = client.post(
        f"/admin/users/{target}/deactivate",
        headers=_cookie_header(_make_session(admin)),
    )
    assert r.status_code == 204
    with get_session() as s:
        assert s.get(UserRow, target).is_active is False
        assert (
            s.scalar(
                select(AuditLogRow).where(
                    AuditLogRow.action == "admin.user.deactivate",
                    AuditLogRow.target_id == target,
                )
            )
            is not None
        )


def test_admin_deactivate_self_400(client, multi_user):
    admin = _make_user(email="admin@example.com", is_admin=True)
    r = client.post(
        f"/admin/users/{admin}/deactivate",
        headers=_cookie_header(_make_session(admin)),
    )
    assert r.status_code == 400


def test_admin_deactivate_unknown_404(client, multi_user):
    admin = _make_user(email="admin@example.com", is_admin=True)
    r = client.post(
        "/admin/users/does-not-exist/deactivate",
        headers=_cookie_header(_make_session(admin)),
    )
    assert r.status_code == 404


def test_admin_activate_user(client, multi_user):
    from surgite.db import AuditLogRow

    admin = _make_user(email="admin@example.com", is_admin=True)
    target = _make_user(email="victim@example.com")
    # Pre-deactivate so the activate handler has work to do.
    with get_session() as s:
        s.get(UserRow, target).is_active = False
        s.commit()
    r = client.post(
        f"/admin/users/{target}/activate",
        headers=_cookie_header(_make_session(admin)),
    )
    assert r.status_code == 204
    with get_session() as s:
        assert s.get(UserRow, target).is_active is True
        assert (
            s.scalar(
                select(AuditLogRow).where(
                    AuditLogRow.action == "admin.user.activate",
                    AuditLogRow.target_id == target,
                )
            )
            is not None
        )


def test_admin_activate_unknown_404(client, multi_user):
    admin = _make_user(email="admin@example.com", is_admin=True)
    r = client.post(
        "/admin/users/does-not-exist/activate",
        headers=_cookie_header(_make_session(admin)),
    )
    assert r.status_code == 404


# --- /summaries/mine --------------------------------------------------------


def test_summaries_mine_returns_only_caller_shares(client, multi_user):
    from surgite.auth import create_session

    alice = _make_user(email="alice@example.com", password="alice-pass-1234")
    bob = _make_user(email="bob@example.com", password="bob-pass-1234567")
    alice_sid = create_session(alice).id
    bob_sid = create_session(bob).id
    for _ in range(2):
        client.post(
            "/summaries",
            json={"repo": "alice-repo", "since": "2026-05-01"},
            headers=_cookie_header(alice_sid),
        )
    client.post(
        "/summaries",
        json={"repo": "bob-repo", "since": "2026-05-01"},
        headers=_cookie_header(bob_sid),
    )

    alice_out = client.get("/summaries/mine", headers=_cookie_header(alice_sid)).json()
    bob_out = client.get("/summaries/mine", headers=_cookie_header(bob_sid)).json()
    assert alice_out["total"] == 2
    assert bob_out["total"] == 1
    assert all(s["params"]["repo"] == "alice-repo" for s in alice_out["summaries"])
    assert bob_out["summaries"][0]["params"]["repo"] == "bob-repo"


def test_summaries_mine_pagination(client, multi_user):
    from surgite.auth import create_session

    uid = _make_user(email="pag@example.com", password="paginate-pass-1234")
    sid = create_session(uid).id
    for i in range(5):
        client.post(
            "/summaries",
            json={"repo": f"r{i}", "since": "2026-05-01"},
            headers=_cookie_header(sid),
        )

    page1 = client.get("/summaries/mine?limit=2&offset=0", headers=_cookie_header(sid)).json()
    page2 = client.get("/summaries/mine?limit=2&offset=2", headers=_cookie_header(sid)).json()
    assert page1["total"] == 5
    assert page2["total"] == 5
    assert len(page1["summaries"]) == 2
    assert len(page2["summaries"]) == 2
    slugs1 = {s["slug"] for s in page1["summaries"]}
    slugs2 = {s["slug"] for s in page2["summaries"]}
    assert slugs1.isdisjoint(slugs2)


def test_summaries_mine_excludes_expired(client, multi_user):
    from datetime import UTC, datetime, timedelta

    from surgite.auth import create_session
    from surgite.db import SharedSummaryRow, get_session

    uid = _make_user(email="exp@example.com", password="expire-pass-12345")
    sid = create_session(uid).id
    live_slug = client.post("/summaries", json={"repo": "r"}, headers=_cookie_header(sid)).json()[
        "slug"
    ]
    with get_session() as s:
        s.add(
            SharedSummaryRow(
                slug="expired-1",
                owner_id=uid,
                params={"repo": "stale"},
                created_at=datetime.now(UTC) - timedelta(days=30),
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        )
        s.commit()
    out = client.get("/summaries/mine", headers=_cookie_header(sid)).json()
    slugs = {row["slug"] for row in out["summaries"]}
    assert slugs == {live_slug}


def test_summaries_mine_requires_auth_in_multi_user(client, multi_user):
    assert client.get("/summaries/mine").status_code == 401


# --- Self-serve, email-delivered password reset (0.6.0) ---------------------
# SMTP_HOST is unset in tests, so the app uses LoggingMailer: the reset email
# (link + token) is written to the `surgite.mail` logger. We read it back from
# caplog and prove the token redeems end-to-end.


def _reset_email_body(caplog) -> str:
    msgs = [r.getMessage() for r in caplog.records if r.name == "surgite.mail"]
    assert msgs, "no email was logged"
    return "\n".join(msgs)


def test_self_serve_reset_emails_a_working_link(client, multi_user, caplog):
    import logging
    import re

    _make_user(email="victim@example.com", password="old-pass-1234")
    with caplog.at_level(logging.INFO, logger="surgite.mail"):
        r = client.post("/auth/password-reset", json={"email": "victim@example.com"})
    assert r.status_code == 204
    body = _reset_email_body(caplog)
    m = re.search(r"/password-reset\?token=(pr_[A-Za-z0-9_-]+)", body)
    assert m, f"no reset link in email:\n{body}"
    token = m.group(1)
    # The emailed token redeems and sets the new password.
    r = client.post(
        "/auth/password-reset/confirm",
        json={"token": token, "new_password": "freshly-chosen-pw"},
    )
    assert r.status_code == 204
    assert (
        client.post(
            "/auth/login", json={"email": "victim@example.com", "password": "freshly-chosen-pw"}
        ).status_code
        == 200
    )


def test_self_serve_reset_unknown_email_is_204_and_silent(client, multi_user, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="surgite.mail"):
        r = client.post("/auth/password-reset", json={"email": "nobody@example.com"})
    assert r.status_code == 204  # no enumeration: same response as a real account
    assert not [rec for rec in caplog.records if rec.name == "surgite.mail"]


def test_self_serve_reset_404s_outside_multi_user(client):
    # AUTH_MODE defaults to off; the route should not exist.
    assert client.post("/auth/password-reset", json={"email": "x@example.com"}).status_code == 404
