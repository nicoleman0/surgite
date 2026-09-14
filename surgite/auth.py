"""Passwords, sessions, API keys, and FastAPI auth dependencies."""

import ipaddress
import logging
import re
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from surgite import config
from surgite.db import (
    ApiKeyRow,
    AuditLogRow,
    InviteRow,
    OrgMemberRow,
    OrgRow,
    PasswordResetRow,
    SessionRow,
    UserRow,
    get_db,
    get_session,
    session_scope,
)

log = logging.getLogger(__name__)

_ph = PasswordHasher()
_SESSION_ID_BYTES = 32


# --- Passwords --------------------------------------------------------------


def hash_password(plain: str) -> str:
    return _ph.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Return whether a password matches, including for malformed hashes."""
    try:
        return _ph.verify(hashed, plain)
    except VerifyMismatchError, InvalidHashError:
        return False


def normalize_email(email: str) -> str:
    return email.strip().lower()


# --- API keys (Bearer) ------------------------------------------------------


_API_KEY_SECRET_LEN = 32
_API_KEY_PREFIX_BYTES = 4  # 4 bytes -> 8 hex chars after "sk_"
_API_KEY_SECRET_BYTES = 24  # 24 bytes -> 32 chars urlsafe


def _generate_api_key() -> tuple[str, str, str]:
    """Return a full API key, its lookup prefix, and its secret."""
    # Hex keeps the prefix free of the key format's underscore separator.
    prefix = "sk_" + secrets.token_hex(_API_KEY_PREFIX_BYTES)
    secret = secrets.token_urlsafe(_API_KEY_SECRET_BYTES)[:_API_KEY_SECRET_LEN]
    full = f"{prefix}_{secret}"
    return full, prefix, secret


def issue_api_key(
    user_id: str,
    *,
    name: str,
    expires_at: datetime | None = None,
) -> tuple[str, str]:
    """Create an API key and return its one-time plaintext value and ID."""
    full, prefix, _secret = _generate_api_key()
    kid = secrets.token_urlsafe(8)
    now = datetime.now(UTC)
    with session_scope() as s:
        row = ApiKeyRow(
            id=kid,
            user_id=user_id,
            org_id=personal_org_id(s, user_id),
            name=name,
            prefix=prefix,
            key_hash=hash_password(full),
            expires_at=expires_at,
            created_at=now,
        )
        s.add(row)
        s.commit()
    return full, kid


def verify_api_key(full_key: str, *, session: Session) -> UserRow | None:
    """Resolve an active Bearer key and update its last-used time."""
    if not full_key or not full_key.startswith("sk_"):
        return None
    parts = full_key.split("_", 2)
    if len(parts) != 3:
        return None
    prefix = parts[0] + "_" + parts[1]
    row = session.scalar(select(ApiKeyRow).where(ApiKeyRow.prefix == prefix))
    if row is None or row.revoked_at is not None:
        return None
    if row.expires_at is not None and _as_utc(row.expires_at) <= datetime.now(UTC):
        return None
    if not verify_password(full_key, row.key_hash):
        return None
    row.last_used_at = datetime.now(UTC)
    session.commit()
    user = session.get(UserRow, row.user_id)
    return user if user is not None and user.is_active else None


def revoke_api_key(key_id: str, *, user_id: str | None = None) -> bool:
    """Revoke a key, optionally restricting it to one owner."""
    with session_scope() as s:
        q = select(ApiKeyRow).where(ApiKeyRow.id == key_id)
        if user_id is not None:
            q = q.where(ApiKeyRow.user_id == user_id)
        row = s.scalar(q)
        if row is None or row.revoked_at is not None:
            return False
        row.revoked_at = datetime.now(UTC)
        s.commit()
        return True


# --- Login lockout ----------------------------------------------------------


def is_locked(user: UserRow) -> bool:
    """Return whether the user's login lockout is active."""
    if user.locked_until is None:
        return False
    return _as_utc(user.locked_until) > datetime.now(UTC)


def record_login_failure(user: UserRow, *, session: Session) -> None:
    """Record a failure and lock the user after the configured threshold."""
    user.failed_login_count += 1
    if user.failed_login_count >= config.LOGIN_LOCKOUT_THRESHOLD:
        user.locked_until = datetime.now(UTC) + timedelta(
            minutes=config.LOGIN_LOCKOUT_DURATION_MINUTES
        )
        user.failed_login_count = 0
    session.commit()


def record_login_success(user: UserRow, *, session: Session) -> None:
    """Clear failed-login state after a successful login."""
    user.failed_login_count = 0
    user.locked_until = None
    session.commit()


def unlock_user(user: UserRow, *, session: Session) -> None:
    """Clear the user's lockout and failure counter."""
    user.failed_login_count = 0
    user.locked_until = None
    session.commit()


# --- Users ------------------------------------------------------------------


def slugify_org(local_part: str) -> str:
    """Convert an email local part to a 3–32 character org slug."""
    s = re.sub(r"[^a-z0-9]+", "-", local_part.lower()).strip("-")[:32].strip("-")
    if len(s) < 3:
        s = f"{s}-org" if s else "org"
    return s


def _ensure_personal_org(session: Session, user: UserRow) -> OrgRow:
    if user.personal_org_id is not None:
        existing = session.get(OrgRow, user.personal_org_id)
        if existing is not None:
            return existing
    base = slugify_org(normalize_email(user.email).split("@")[0])
    slug, n = base, 1
    while session.scalar(select(OrgRow.slug).where(OrgRow.slug == slug)) is not None:
        n += 1
        slug = f"{base}-{n}"
    org = OrgRow(name=user.display_name or base, slug=slug)
    session.add(org)
    session.flush()
    session.add(OrgMemberRow(org_id=org.id, user_id=user.id, role="owner"))
    user.personal_org_id = org.id
    return org


def create_personal_org(session: Session, user: UserRow) -> OrgRow:
    """Return the user's personal org, creating it when needed."""
    org = _ensure_personal_org(session, user)
    session.commit()
    session.refresh(user)
    return org


def personal_org_id(session: Session, user_id: str) -> str | None:
    """Look up a user's personal org ID."""
    return session.scalar(select(UserRow.personal_org_id).where(UserRow.id == user_id))


def ensure_bootstrap_user(session: Session) -> UserRow:
    """Return or create the owner used by off and single-user modes."""
    email = normalize_email(config.BOOTSTRAP_OWNER_EMAIL)
    user = session.scalar(select(UserRow).where(UserRow.email == email))
    if user is None:
        user = UserRow(email=email, display_name="owner", is_admin=True, is_active=True)
        session.add(user)
        session.commit()
        session.refresh(user)
    create_personal_org(session, user)
    return user


def create_user(
    session: Session,
    *,
    email: str,
    password: str | None,
    display_name: str = "",
    is_admin: bool = False,
) -> UserRow:
    user = UserRow(
        email=normalize_email(email),
        password_hash=hash_password(password) if password else None,
        display_name=display_name or normalize_email(email).split("@")[0],
        is_admin=is_admin,
        is_active=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    create_personal_org(session, user)
    return user


def bootstrap_admin(email: str, password: str) -> str:
    """Create or claim the sole bootstrap administrator from the host shell."""
    if config.AUTH_MODE != "multi_user":
        raise ValueError("administrator bootstrap requires AUTH_MODE=multi_user")
    email = normalize_email(email)
    if not email:
        raise ValueError("administrator email is required")
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    password_hash = hash_password(password)

    with get_session() as session:
        admins = session.scalars(
            select(UserRow).where(UserRow.is_admin.is_(True), UserRow.is_active.is_(True))
        ).all()
        if any(admin.password_hash is not None for admin in admins):
            raise ValueError("an administrator is already configured")
        if len(admins) > 1:
            raise ValueError("multiple passwordless administrators exist; refusing to guess")

        target = session.scalar(select(UserRow).where(UserRow.email == email))
        if admins:
            user = admins[0]
            if target is not None and target.id != user.id:
                raise ValueError(f"an account with email {email!r} already exists")
            user.email = email
            user.password_hash = password_hash
            mode = "claimed"
        else:
            if target is not None:
                if not target.is_active:
                    raise ValueError(f"the account for {email!r} is inactive")
                raise ValueError(f"an account with email {email!r} already exists")
            user = UserRow(
                email=email,
                password_hash=password_hash,
                display_name=email.split("@")[0],
                is_admin=True,
                is_active=True,
            )
            session.add(user)
            session.flush()
            mode = "created"
        _ensure_personal_org(session, user)

        now = datetime.now(UTC)
        legacy_invites = session.scalars(
            select(InviteRow).where(
                InviteRow.created_by.is_(None),
                InviteRow.role == "admin",
                InviteRow.used_at.is_(None),
            )
        ).all()
        for invite in legacy_invites:
            invite.used_at = now
            invite.used_by = user.id
        session.add(
            AuditLogRow(
                actor_id=user.id,
                org_id=user.personal_org_id,
                action="admin.bootstrap",
                target_type="user",
                target_id=user.id,
                metadata_={"mode": mode},
            )
        )
        session.commit()
        user_id = user.id

    logging.getLogger("audit").info(
        "audit admin.bootstrap actor=%s target=user:%s", user_id, user_id
    )
    return user_id


def create_invite(
    session: Session,
    *,
    email: str | None = None,
    role: str = "user",
    created_by: str | None = None,
    ttl_days: int = 14,
) -> InviteRow:
    invite = InviteRow(
        token=secrets.token_urlsafe(_SESSION_ID_BYTES),
        email=normalize_email(email) if email else None,
        role=role,
        created_by=created_by,
        org_id=personal_org_id(session, created_by) if created_by else None,
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=ttl_days),
    )
    session.add(invite)
    session.commit()
    session.refresh(invite)
    return invite


def has_active_admin(session: Session) -> bool:
    """Return whether the deployment has an active administrator."""
    return (
        session.scalar(
            select(UserRow.id)
            .where(UserRow.is_admin.is_(True), UserRow.is_active.is_(True))
            .limit(1)
        )
        is not None
    )


# --- Sessions ---------------------------------------------------------------


def _as_utc(dt: datetime) -> datetime:
    """Treat SQLite's naive datetimes as UTC."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _truncate_ip(ip: str | None) -> str | None:
    """Coarsen an IP address before storing it for audit."""
    if not ip:
        return None
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    prefix = 24 if addr.version == 4 else 64
    return str(ipaddress.ip_network(f"{ip}/{prefix}", strict=False))


def create_session(
    user_id: str,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
    session: Session | None = None,
) -> SessionRow:
    now = datetime.now(UTC)
    row = SessionRow(
        id=secrets.token_urlsafe(_SESSION_ID_BYTES),
        user_id=user_id,
        created_at=now,
        expires_at=now + timedelta(days=config.SESSION_TTL_DAYS),
        last_seen_at=now,
        ip=_truncate_ip(ip),
        user_agent=(user_agent or "")[:256] or None,
    )
    with session_scope(session) as s:
        row.org_id = personal_org_id(s, user_id)
        s.add(row)
        s.commit()
        s.refresh(row)
        s.expunge(row)
    return row


def revoke_session(session_id: str, *, session: Session | None = None) -> None:
    with session_scope(session) as s:
        row = s.get(SessionRow, session_id)
        if row is not None:
            s.delete(row)
            s.commit()


def purge_expired_sessions() -> int:
    """Delete expired sessions and return the number removed."""
    now = datetime.now(UTC)
    with session_scope() as s:
        rows = s.scalars(select(SessionRow).where(SessionRow.expires_at <= now)).all()
        for row in rows:
            s.delete(row)
        s.commit()
        return len(rows)


def revoke_all_sessions(user_id: str, *, keep: str | None = None) -> int:
    """Revoke a user's sessions, optionally preserving one."""
    with session_scope() as s:
        q = select(SessionRow).where(SessionRow.user_id == user_id)
        if keep is not None:
            q = q.where(SessionRow.id != keep)
        rows = s.scalars(q).all()
        for row in rows:
            s.delete(row)
        s.commit()
        return len(rows)


# --- Password change --------------------------------------------------------


def change_password(user_id: str, *, new_password: str) -> None:
    """Replace a user's password hash."""
    with session_scope() as s:
        user = s.get(UserRow, user_id)
        if user is None:
            return
        user.password_hash = hash_password(new_password)
        s.commit()


# --- Password reset tokens --------------------------------------------------
_PASSWORD_RESET_TTL_MINUTES = 15
_PASSWORD_RESET_ID_BYTES = 4  # token_hex(4) -> 8 hex chars, separator-free
_PASSWORD_RESET_ID_LEN = _PASSWORD_RESET_ID_BYTES * 2
_PASSWORD_RESET_SECRET_BYTES = 32


def _generate_reset_token() -> tuple[str, str]:
    """Return a reset token and its separator-free lookup ID."""
    rid = secrets.token_hex(_PASSWORD_RESET_ID_BYTES)
    secret = secrets.token_urlsafe(_PASSWORD_RESET_SECRET_BYTES)
    return f"pr_{rid}_{secret}", rid


def mint_password_reset(user_id: str) -> tuple[str, datetime]:
    """Persist a reset token and return its one-time plaintext value."""
    full, rid = _generate_reset_token()
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=_PASSWORD_RESET_TTL_MINUTES)
    with session_scope() as s:
        s.add(
            PasswordResetRow(
                id=rid,
                user_id=user_id,
                org_id=personal_org_id(s, user_id),
                token_hash=hash_password(full),
                expires_at=expires_at,
                created_at=now,
            )
        )
        s.commit()
    return full, expires_at


def redeem_password_reset(token: str, *, new_password: str) -> str | None:
    """Redeem a reset token, returning the user ID on success."""
    if not token or not token.startswith("pr_"):
        return None
    rid = token[3 : 3 + _PASSWORD_RESET_ID_LEN]
    if len(rid) != _PASSWORD_RESET_ID_LEN:
        return None
    now = datetime.now(UTC)
    with session_scope() as s:
        row = s.get(PasswordResetRow, rid)
        if row is None or row.used_at is not None or _as_utc(row.expires_at) <= now:
            return None
        if not verify_password(token, row.token_hash):
            return None
        user = s.get(UserRow, row.user_id)
        if user is None or not user.is_active:
            return None
        user.password_hash = hash_password(new_password)
        user.failed_login_count = 0
        user.locked_until = None
        row.used_at = now
        s.commit()
        user_id = user.id
    revoke_all_sessions(user_id)
    return user_id


# --- Cookies ----------------------------------------------------------------


def set_session_cookie(response: Response, session_id: str) -> None:
    """Set the session cookie, allowing insecure cookies only in debug mode."""
    response.set_cookie(
        key=config.SESSION_COOKIE_NAME,
        value=session_id,
        max_age=config.SESSION_TTL_DAYS * 86400,
        httponly=True,
        secure=not config.DEBUG,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(config.SESSION_COOKIE_NAME, path="/")


# --- The dependency ---------------------------------------------------------


def get_current_user(
    request: Request,
    response: Response,
    session: Session = Depends(get_db),
) -> UserRow:
    """Resolve the bootstrap user, Bearer key, or session for this request."""
    mode = config.AUTH_MODE
    if mode in ("off", "single_user"):
        return ensure_bootstrap_user(session)

    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
        user = verify_api_key(token, session=session)
        if user is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        return user

    sid = request.cookies.get(config.SESSION_COOKIE_NAME)
    if not sid:
        raise HTTPException(status_code=401, detail="Not authenticated")

    now = datetime.now(UTC)
    sess = session.get(SessionRow, sid)
    if sess is None or _as_utc(sess.expires_at) <= now:
        raise HTTPException(status_code=401, detail="Session expired")

    user = session.get(UserRow, sess.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Not authenticated")

    sess.last_seen_at = now
    if _as_utc(sess.expires_at) - now < timedelta(days=config.SESSION_REFRESH_THRESHOLD_DAYS):
        sess.expires_at = now + timedelta(days=config.SESSION_TTL_DAYS)
        set_session_cookie(response, sess.id)
    session.commit()
    return user


def get_optional_user(
    request: Request,
    session: Session = Depends(get_db),
) -> UserRow | None:
    """Resolve an optional session without raising for anonymous requests."""
    if config.AUTH_MODE in ("off", "single_user"):
        return ensure_bootstrap_user(session)
    sid = request.cookies.get(config.SESSION_COOKIE_NAME)
    if not sid:
        return None
    sess = session.get(SessionRow, sid)
    if sess is None or _as_utc(sess.expires_at) <= datetime.now(UTC):
        return None
    user = session.get(UserRow, sess.user_id)
    return user if user is not None and user.is_active else None
