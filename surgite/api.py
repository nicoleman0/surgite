import asyncio
import json
import logging
import os
import secrets
import threading
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from surgite import config, mail, summarizer
from surgite.audit import audit
from surgite.auth import (
    change_password,
    clear_session_cookie,
    create_invite,
    create_session,
    create_user,
    ensure_bootstrap_invite,
    get_current_user,
    get_optional_user,
    is_locked,
    issue_api_key,
    mint_password_reset,
    normalize_email,
    personal_org_id,
    purge_expired_sessions,
    record_login_failure,
    record_login_success,
    redeem_password_reset,
    revoke_all_sessions,
    revoke_api_key,
    revoke_session,
    set_session_cookie,
    unlock_user,
    verify_password,
)
from surgite.config import SHARE_TTL_DAYS
from surgite.db import (
    ApiKeyRow,
    AuditLogRow,
    CommitRow,
    InviteRow,
    PromptSettingsRow,
    ProviderKeyRow,
    RepoRow,
    SharedSummaryRow,
    UserRow,
    get_db,
    session_scope,
)
from surgite.formatter import format_log
from surgite.git import get_raw_log, ls_remote, parse_log
from surgite.logging_config import configure_logging
from surgite.models import Commit
from surgite.rate_limit import (
    check_ip_outer_rate_limit,
    check_summary_user_limit,
)
from surgite.schemas import (
    ApiKeyCreate,
    ErrorResponse,
    IngestAccepted,
    InviteCreateRequest,
    LoginRequest,
    PasswordChange,
    PasswordResetConfirm,
    PasswordResetRequest,
    PromptSettingsUpdate,
    ProviderKeysUpdate,
    RedeemInviteRequest,
    RepoCreate,
    RepoListResponse,
    RepoResponse,
    ShareCreate,
)
from surgite.secrets import encrypt
from surgite.summarizer import ProviderError

configure_logging()
log = logging.getLogger(__name__)

# Cap on commits sent to the LLM in /summary?ai=true to bound token cost.
AI_SUMMARY_MAX_COMMITS = 500
INGEST_ERROR = "Could not sync repository; check its URL and credentials."

_active_ingests: set[int] = set()
_active_ingests_lock = threading.Lock()


def _claim_ingest(repo_id: int) -> bool:
    """Atomically reserve a repo for ingestion in this process."""
    with _active_ingests_lock:
        if repo_id in _active_ingests:
            return False
        _active_ingests.add(repo_id)
        return True


def _release_ingest(repo_id: int) -> None:
    with _active_ingests_lock:
        _active_ingests.discard(repo_id)


def _ingest_all_repos() -> list[dict]:
    """Ingest every registered repo. The background scheduler calls this on
    a timer (see `_scheduler_loop`); the /summary endpoint itself stays a
    pure read against the DB. The per-repo ingest (`_ingest_repo`) is still
    wired up as a FastAPI BackgroundTask on `POST /repos` so newly added
    repos show up immediately rather than waiting up to INGEST_INTERVAL
    seconds. Returns list of per-repo result/error dicts."""
    results: list[dict] = []
    with session_scope() as s:
        repo_data = [(r.id, r.name) for r in s.scalars(select(RepoRow)).all()]

    for repo_id, repo_name in repo_data:
        if not _claim_ingest(repo_id):
            continue
        results.append(_run_ingest(repo_id, fallback_name=repo_name))

    return results


def _ingest_repo(
    repo_id: int,
    repo_name: str,
    clone_url: str,
    owner_id: str,
    since: date | None = None,
    until: date | None = None,
    session: Session | None = None,
) -> dict:
    """Ingest commits for a single repo. Commits inherit the repo's owner.
    Returns a result dict."""
    from surgite.config import REPO_CACHE_DIR
    from surgite.git import ensure_repo

    actual_path = ensure_repo(repo_name, clone_url, REPO_CACHE_DIR)

    since_str = (since or date.today() - timedelta(days=7)).isoformat()
    until_str = (until or date.today()).isoformat()

    raw = get_raw_log(actual_path, since_str, until_str)
    commits = parse_log(raw)
    inserted = updated = unchanged = 0

    with session_scope(session) as s:
        existing: dict[str, CommitRow] = {}
        if commits:
            existing = {
                row.hash: row
                for row in s.scalars(
                    select(CommitRow).where(CommitRow.hash.in_([c.hash for c in commits]))
                )
            }
        now = datetime.now(UTC)
        for c in commits:
            row = existing.get(c.hash)
            if row is None:
                s.add(
                    CommitRow(
                        hash=c.hash,
                        short_hash=c.hash[:7],
                        date=c.date,
                        author=c.author,
                        message=c.message,
                        repo=repo_name,
                        repo_id=repo_id,
                        owner_id=owner_id,
                        ingested_at=now,
                    )
                )
                inserted += 1
            elif row.repo_id != repo_id:
                row.repo_id = repo_id
                row.repo = repo_name
                row.ingested_at = now
                updated += 1
            else:
                unchanged += 1

        s.commit()

    return {"repo": repo_name, "inserted": inserted, "updated": updated, "unchanged": unchanged}


def _run_ingest(repo_id: int, fallback_name: str | None = None) -> dict:
    """Run one previously-reserved ingest and persist its completed outcome."""
    repo_name = fallback_name or str(repo_id)
    try:
        with session_scope() as s:
            repo = s.get(RepoRow, repo_id)
            if repo is None:
                return {"repo": repo_name, "skipped": "deleted"}
            repo_name = repo.name
            clone_url = repo.clone_url
            owner_id = repo.owner_id

        result = _ingest_repo(repo_id, repo_name, clone_url, owner_id)
        completed_at = datetime.now(UTC)
        with session_scope() as s:
            repo = s.get(RepoRow, repo_id)
            if repo is not None:
                repo.last_ingest_attempt_at = completed_at
                repo.last_ingested_at = completed_at
                repo.last_ingest_error = None
                s.commit()
        return result
    except Exception as exc:
        completed_at = datetime.now(UTC)
        with session_scope() as s:
            repo = s.get(RepoRow, repo_id)
            if repo is not None:
                repo.last_ingest_attempt_at = completed_at
                repo.last_ingest_error = INGEST_ERROR
                s.commit()
        log.warning("Ingest failed for repo %s: %s", repo_name, exc, extra={"repo": repo_name})
        return {"repo": repo_name, "error": INGEST_ERROR}
    finally:
        _release_ingest(repo_id)


def _delete_expired_summaries() -> int:
    """Sweep shared-summary slugs past their expiry. Read-path also rejects
    expired slugs, so this is just housekeeping to keep the table small.
    Returns the number of rows deleted."""
    now = datetime.now(UTC)
    with session_scope() as s:
        rows = s.scalars(select(SharedSummaryRow).where(SharedSummaryRow.expires_at <= now)).all()
        for row in rows:
            s.delete(row)
        s.commit()
        return len(rows)


def _scheduler_tick() -> None:
    """One pass of the background work: ingest every repo, prune expired share
    slugs, and purge expired sessions. Runs in a thread (sync DB + git) off
    the event loop."""
    _ingest_all_repos()
    _delete_expired_summaries()
    purge_expired_sessions()


async def _scheduler_loop(interval: int) -> None:
    """Background task: every `interval` seconds, ingest all registered repos
    and prune expired share slugs.

    Runs `_scheduler_tick` in a thread (it's sync, talks to git + DB) so
    the event loop stays responsive. Any exception is logged and the loop
    continues — a transient git failure must not stop the scheduler. Stops
    cleanly when the task is cancelled at app shutdown."""
    log.info("Background ingest scheduler started (interval=%ds)", interval)
    loop = asyncio.get_running_loop()
    try:
        while True:
            try:
                await loop.run_in_executor(None, _scheduler_tick)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Defence in depth: _ingest_all_repos already swallows per-repo
                # failures, so anything reaching here is unexpected.
                log.exception("Background ingest loop failed: %s", exc)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        log.info("Background ingest scheduler stopped")
        raise


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Start the background ingest scheduler on app startup, cancel it on
    shutdown. Disabled (interval<=0) for tests and one-off CLI runs where
    a background task would never be observed.

    INGEST_INTERVAL is read fresh from the environment on every startup so
    tests can flip it without reloading the config module."""
    if config.AUTH_MODE == "multi_user":
        with session_scope() as s:
            token = ensure_bootstrap_invite(s)
        if token:
            log.warning(
                "No admin account exists. Bootstrap an admin by redeeming this "
                "invite for %s:  surgite --redeem-invite %s",
                config.BOOTSTRAP_OWNER_EMAIL,
                token,
            )

    interval = int(os.environ.get("INGEST_INTERVAL", "300"))
    task: asyncio.Task | None = None
    if interval > 0:
        task = asyncio.create_task(_scheduler_loop(interval))
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


# Document the stable error envelope once, for every route. OpenAPI's
# `default` response means "any status not otherwise listed" — accurate
# here because every error path (HTTPException, the CSRF and SQLAlchemy
# handlers below) serialises to the same {"detail": "..."} shape. This
# puts ErrorResponse in components/schemas and declares the contract the
# 0.6.0 api-stability policy promises, without per-route boilerplate.
# Routes that want to call out a specific status + header (login's 423,
# api-key issuance's 429) add it on their own decorator.
# ponytail: one app-level default beats responses= on all 36 routes.
_ERROR_RESPONSES: dict = {
    "default": {"model": ErrorResponse, "description": 'Error: `{"detail": "..."}`.'}
}

app = FastAPI(title="surgite", lifespan=_lifespan, responses=_ERROR_RESPONSES)

# Allow the Vite dev server (separate origin) to call the API during development.
# In production the frontend is served same-origin from the static mount below, so
# these origins simply go unused.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# CSRF defence in depth. The session cookie is ``SameSite=Lax`` so the
# browser won't send it on cross-site POSTs; this
# adds a header check on top so a same-site XHR can't get away with a
# missing intent signal either.
#
# Rules:
#  - The check is only active in multi_user mode (no session cookie in
#    off/single_user; the test suite would burn cycles on noise).
#  - Safe methods (GET/HEAD/OPTIONS) and a small allowlist (login,
#    logout, redeem-invite, health) are exempt — login and redeem
#    are pre-session endpoints where the cookie doesn't exist yet,
#    and the SameSite=Lax cookie already provides the cross-site
#    protection.
#  - The SPA sends ``X-Requested-With: surgite-web`` on every state-
#    changing request. The CLI uses Bearer auth and never hits a
#    state-changing route, so the header requirement is invisible to
#    it.
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_CSRF_EXEMPT_PATHS = {
    "/auth/login",
    "/auth/redeem-invite",
    "/auth/logout",
    "/auth/password-reset",
    "/auth/password-reset/confirm",
    "/signup",
    "/login",
}
_CSRF_HEADER = "x-requested-with"
_CSRF_HEADER_VALUE = "surgite-web"


@app.middleware("http")
async def _csrf_middleware(request: Request, call_next):
    if (
        config.AUTH_MODE == "multi_user"
        and request.method in _UNSAFE_METHODS
        and request.url.path not in _CSRF_EXEMPT_PATHS
        and not request.url.path.startswith("/static/")
    ):
        if request.headers.get(_CSRF_HEADER) != _CSRF_HEADER_VALUE:
            return JSONResponse(
                status_code=403,
                content={"detail": "Missing or invalid X-Requested-With header"},
            )
    return await call_next(request)


@app.exception_handler(SQLAlchemyError)
async def _sqlalchemy_error_handler(request: Request, exc: SQLAlchemyError):
    return JSONResponse(status_code=503, content={"detail": "Database unavailable"})


def _escape_like(s: str) -> str:
    """Escape SQL LIKE wildcards so user input is treated literally."""
    return s.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


def _repo_to_dict(row: RepoRow) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "clone_url": row.clone_url,
        "added_at": row.added_at.isoformat() if row.added_at else None,
        "last_ingested_at": row.last_ingested_at.isoformat() if row.last_ingested_at else None,
        "last_ingest_attempt_at": (
            row.last_ingest_attempt_at.isoformat() if row.last_ingest_attempt_at else None
        ),
        "last_ingest_error": row.last_ingest_error,
    }


def _owned_repo(session: Session, repo_id: int, owner_id: str) -> RepoRow | None:
    """Fetch a repo by id only if `owner_id` owns it. Returns None otherwise,
    so callers turn cross-owner access into a 404."""
    repo = session.get(RepoRow, repo_id)
    return repo if repo is not None and repo.owner_id == owner_id else None


def _row_to_dict(row: CommitRow) -> dict:
    """Convert a CommitRow to a dictionary."""
    return {
        "hash": row.hash,
        "short_hash": row.short_hash,
        "date": row.date.isoformat(),
        "author": row.author,
        "message": row.message,
        "repo": row.repo,
        "ingested_at": row.ingested_at.isoformat() if row.ingested_at else None,
    }


def _query_commits(
    since: date | None,
    until: date | None,
    author: str | None,
    repo: str | None,
    limit: int | None,
    offset: int,
    owner_id: str,
    session: Session | None = None,
) -> tuple[int, list[dict]]:
    """Run the filtered commits query, scoped to `owner_id`. limit=None returns
    all matching rows."""
    with session_scope(session) as s:
        q = select(CommitRow).where(CommitRow.owner_id == owner_id)
        if since:
            q = q.where(CommitRow.date >= since)
        if until:
            q = q.where(CommitRow.date <= until)
        if author:
            q = q.where(CommitRow.author.ilike(f"%{_escape_like(author)}%", escape="\\"))
        if repo:
            repo_row = s.scalar(
                select(RepoRow).where(RepoRow.name == repo, RepoRow.owner_id == owner_id)
            )
            if repo_row is None:
                return 0, []
            q = q.where(CommitRow.repo_id == repo_row.id)

        total = s.scalar(select(func.count()).select_from(q.subquery())) or 0
        q = q.order_by(CommitRow.date.desc()).offset(offset)
        if limit is not None:
            q = q.limit(limit)
        rows = s.scalars(q).all()
        return total, [_row_to_dict(r) for r in rows]


@app.get("/health", summary="Liveness + DB readiness", tags=["health"], operation_id="health")
def health(session: Session = Depends(get_db)):
    """Liveness + DB readiness, for monitoring and the container healthcheck.
    A failed DB connection raises SQLAlchemyError, mapped to 503 above."""
    session.execute(select(1))
    return {"status": "ok"}


async def _check_providers() -> dict[str, str]:
    """Per-provider reachability: missing_key (no key, not a failure), ok (key
    set + host answered), or unreachable (key set but the network call failed).
    Reachability only — we don't spend a token validating the key."""
    out: dict[str, str] = {}
    async with httpx.AsyncClient(timeout=5) as client:
        for name, provider in summarizer.PROVIDERS.items():
            if provider.api_key is None:
                out[name] = "missing_key"
                continue
            try:
                await client.get(provider.base_url)
                out[name] = "ok"
            except httpx.HTTPError:
                out[name] = "unreachable"
    return out


@app.get(
    "/health/deep",
    summary="Deep health check",
    tags=["health"],
    operation_id="health_deep",
    responses={
        200: {"description": "All checked components healthy."},
        503: {
            "description": (
                "At least one checked component is degraded. Body is "
                '`{"status": "ok"|"degraded", "components": {"db", "git", '
                '"providers"}}`; `no_repos` / `missing_key` are informational, '
                "not failures."
            )
        },
    },
)
async def health_deep(
    session: Session = Depends(get_db),
    user: UserRow | None = Depends(get_optional_user),
):
    """Deep health for a real uptime check: DB connectivity, a remote-reachable
    probe against one registered repo, and provider-key reachability. Returns
    503 if any *checked* component is down (no_repos / missing_key are not
    failures), else 200.

    The git probe is scoped to a repo the *caller* owns and the response never
    names a specific repo, so an exposed multi_user deployment can't be used to
    enumerate other users' repos via /health/deep. An anonymous
    caller in multi_user mode gets `git: no_repos` — the probe is skipped
    rather than run against an arbitrary user's repo."""
    components: dict[str, object] = {}
    healthy = True

    try:
        session.execute(select(1))
        components["db"] = "ok"
    except SQLAlchemyError:
        components["db"] = "error"
        healthy = False

    repo = None
    if user is not None:
        repo = session.scalar(select(RepoRow).where(RepoRow.owner_id == user.id).limit(1))
    if repo is None:
        components["git"] = "no_repos"
    else:
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, ls_remote, repo.clone_url)
            components["git"] = "ok"
        except Exception as exc:
            log.warning("Deep health git probe failed: %s", exc, extra={"repo": repo.name})
            components["git"] = "error"
            healthy = False

    providers = await _check_providers()
    components["providers"] = providers
    if any(state == "unreachable" for state in providers.values()):
        healthy = False

    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ok" if healthy else "degraded", "components": components},
    )


@app.get(
    "/providers",
    summary="List summary providers",
    tags=["providers"],
    operation_id="list_providers",
)
def providers(current_user: UserRow = Depends(get_current_user)):
    """List summary providers, their default model, and whether each is
    configured. A client (UI/CLI) can use this to let the user pick one.

    In multi_user mode this is admin-only: provider-key presence is an
    information-disclosure surface on an exposed deployment. The status
    shown is the *calling admin's* per-user view (their own
    ``provider_keys`` rows + the env-var fallback), so the admin sees
    what they personally can use. In off/single_user mode it's the
    env-var view and is open as before."""
    if config.AUTH_MODE == "multi_user" and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    if config.AUTH_MODE == "multi_user":
        return {
            "default": summarizer.default_provider(),
            "providers": summarizer.provider_status_for(current_user.id),
        }
    return {"default": summarizer.default_provider(), "providers": summarizer.provider_status()}


# --- Auth (multi_user only) -------------------------------------------------
# The login/logout/redeem flow only exists in multi_user mode. In off and
# single_user mode there's no login concept, so these routes 404 — the auth
# machinery is still exercised by single_user (sessions, hashing) but the user
# never reaches these handlers.


def _require_multi_user() -> None:
    if config.AUTH_MODE != "multi_user":
        raise HTTPException(status_code=404, detail="Not found")


def _user_to_dict(user: UserRow) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "is_admin": user.is_admin,
    }


def _user_admin_to_dict(user: UserRow) -> dict:
    """Full user dict for the admin /admin/users view — adds is_active, the
    login/lockout counters, and timestamps that the self-view deliberately
    hides."""
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "is_active": user.is_active,
        "is_admin": user.is_admin,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "failed_login_count": user.failed_login_count,
        "locked_until": user.locked_until.isoformat() if user.locked_until else None,
    }


@app.post(
    "/auth/login",
    summary="Log in",
    tags=["auth"],
    operation_id="auth_login",
    responses={
        423: {
            "model": ErrorResponse,
            "description": "Account temporarily locked.",
            "headers": {
                "Retry-After": {
                    "schema": {"type": "integer"},
                    "description": "Seconds until the lockout expires.",
                }
            },
        }
    },
)
def auth_login(
    req: LoginRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_db),
):
    """Verify credentials, open a session, set the hardened cookie. A bad
    email or password is an indistinguishable 401 (no account enumeration).
    A locked account is a 423 with a Retry-After header.

    Lockout: ``LOGIN_LOCKOUT_THRESHOLD`` consecutive failures trip a
    ``LOGIN_LOCKOUT_DURATION_MINUTES``-minute lockout for the user. The
    counter is reset on success. A locked user sees the same 401 a
    wrong-password user does (no lockout-state leak)."""
    _require_multi_user()
    ip = request.client.host if request.client else None
    user = session.scalar(select(UserRow).where(UserRow.email == normalize_email(req.email)))
    bad = (
        user is None
        or not user.is_active
        or user.password_hash is None
        or not verify_password(req.password, user.password_hash)
    )
    if bad:
        if user is not None and user.password_hash is not None and user.is_active:
            # Only count a failure if the user exists with a password and
            # is active — an unknown-email attempt is a 401 with no
            # counter to bump (we have no user to lock out).
            record_login_failure(user, session=session)
        audit(
            "auth.login.fail",
            actor_id=user.id if user else None,
            ip=ip,
            user_agent=request.headers.get("user-agent"),
            metadata={"email": normalize_email(req.email)},
        )
        raise HTTPException(status_code=401, detail="Invalid email or password")
    # `bad` was False, so `user` is a real active user with a password hash.
    assert user is not None
    if is_locked(user):
        assert user.locked_until is not None  # is_locked returned True
        retry_after = max(1, int((_as_utc(user.locked_until) - datetime.now(UTC)).total_seconds()))
        audit(
            "auth.login.locked",
            actor_id=user.id,
            ip=ip,
            user_agent=request.headers.get("user-agent"),
        )
        raise HTTPException(
            status_code=423,
            detail="Account temporarily locked. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )
    sess = create_session(
        user.id,
        ip=ip,
        user_agent=request.headers.get("user-agent"),
        session=session,
    )
    set_session_cookie(response, sess.id)
    user.last_login_at = datetime.now(UTC)
    record_login_success(user, session=session)
    session.commit()
    audit(
        "auth.login.success",
        actor_id=user.id,
        ip=ip,
        user_agent=request.headers.get("user-agent"),
    )
    return _user_to_dict(user)


@app.post("/auth/logout", summary="Log out", tags=["auth"], operation_id="auth_logout")
def auth_logout(
    request: Request,
    response: Response,
    session: Session = Depends(get_db),
):
    """Revoke the current session and clear the cookie. Idempotent — logging
    out without a session is still a 200."""
    _require_multi_user()
    sid = request.cookies.get(config.SESSION_COOKIE_NAME)
    if sid:
        revoke_session(sid, session=session)
    clear_session_cookie(response)
    audit(
        "auth.logout",
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {"status": "logged out"}


@app.post(
    "/auth/redeem-invite",
    status_code=201,
    summary="Redeem an invite",
    tags=["auth"],
    operation_id="auth_redeem_invite",
)
def auth_redeem_invite(
    req: RedeemInviteRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_db),
):
    """Claim a single-use invite: create the account, set its password, mark
    the invite used, and log the new user straight in (sets the session
    cookie). This is the no-auth bootstrap path the CLI drives."""
    _require_multi_user()
    ip = request.client.host if request.client else None
    invite = session.scalar(select(InviteRow).where(InviteRow.token == req.token))
    now = datetime.now(UTC)
    if invite is None or invite.used_at is not None or _as_utc(invite.expires_at) <= now:
        audit(
            "auth.invite.fail",
            ip=ip,
            user_agent=request.headers.get("user-agent"),
            metadata={"reason": "invalid_or_expired"},
        )
        raise HTTPException(status_code=400, detail="Invalid or expired invite")

    email = invite.email or (normalize_email(req.email) if req.email else None)
    if not email:
        raise HTTPException(status_code=400, detail="This invite requires an email")
    if session.scalar(select(UserRow).where(UserRow.email == normalize_email(email))):
        audit(
            "auth.invite.fail",
            ip=ip,
            user_agent=request.headers.get("user-agent"),
            metadata={"reason": "email_taken", "email": email},
        )
        raise HTTPException(status_code=409, detail="An account with this email already exists")

    user = create_user(
        session,
        email=email,
        password=req.password,
        display_name=req.display_name or "",
        is_admin=(invite.role == "admin"),
    )
    invite.used_at = now
    invite.used_by = user.id
    sess = create_session(
        user.id,
        ip=ip,
        user_agent=request.headers.get("user-agent"),
        session=session,
    )
    user.last_login_at = now
    session.commit()
    set_session_cookie(response, sess.id)
    audit(
        "auth.invite.redeem",
        actor_id=user.id,
        target_type="invite",
        target_id=invite.id,
        ip=ip,
        user_agent=request.headers.get("user-agent"),
        metadata={"role": invite.role},
    )
    return _user_to_dict(user)


# Friendly alias so the SPA can POST /signup (the URL a user would type). The
# handler is the one bound to /auth/redeem-invite above; aliasing the function
# under a second route keeps a single source of truth (no duplicated logic).
# CSRF allowlist includes /signup so the SPA can post it without the
# ``X-Requested-With`` header that a freshly-loaded form won't have yet.
app.post(
    "/signup",
    status_code=201,
    summary="Sign up (redeem-invite alias)",
    tags=["auth"],
    operation_id="signup",
)(auth_redeem_invite)


@app.get("/auth/me", summary="Current user", tags=["auth"], operation_id="auth_me")
def auth_me(current_user: UserRow = Depends(get_current_user)):
    """The current user, for the SPA's 'logged in as' indicator and to let the
    CLI verify a stored session is still valid."""
    return _user_to_dict(current_user)


# --- Password change --------------------------------------------------------


@app.put(
    "/auth/password",
    summary="Change password",
    tags=["auth"],
    operation_id="auth_change_password",
)
def auth_change_password(
    req: PasswordChange,
    request: Request,
    response: Response,
    session: Session = Depends(get_db),
    current_user: UserRow = Depends(get_current_user),
):
    """Self-service password change. Verifies the current password, hashes
    the new one, and revokes all of the user's *other* sessions (a stolen
    cookie stops working). The current session is kept so the user isn't
    logged out of the page that triggered the change. 401 on a wrong
    current password; 404 in off/single_user mode."""
    _require_multi_user()
    if not verify_password(req.current_password, current_user.password_hash or ""):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    change_password(current_user.id, new_password=req.new_password)
    sid = request.cookies.get(config.SESSION_COOKIE_NAME)
    revoke_all_sessions(current_user.id, keep=sid)
    audit(
        "auth.password.change",
        actor_id=current_user.id,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return Response(status_code=204)


# --- Password reset ---------------------------------------------------------
# Two ways in: the self-serve flow (user asks, gets an email) and the
# admin-mediated flow (admin mints a token, useful when the user can't
# receive mail). Both land on POST /auth/password-reset/confirm to redeem.
# 15-minute expiry; on success every session is revoked and the lockout
# is cleared.


def _reset_link(request: Request, token: str) -> str:
    """Build the reset link the email points at. PUBLIC_URL wins; otherwise
    fall back to the request's own origin so a single-host deploy needs no
    config. The path is the SPA's /password-reset page, which reads ?token."""
    base = config.PUBLIC_URL or str(request.base_url).rstrip("/")
    return f"{base}/password-reset?token={token}"


@app.post(
    "/auth/password-reset",
    status_code=204,
    summary="Request a password reset email",
    tags=["auth"],
    operation_id="auth_request_password_reset",
)
def auth_request_password_reset(
    req: PasswordResetRequest,
    request: Request,
    session: Session = Depends(get_db),
):
    """Self-serve password reset. Mints a one-time, 15-minute token for the
    account and emails the reset link. Always returns 204 — even when the
    email doesn't exist or the account is inactive — so it can't be used to
    enumerate accounts. Public (no session) and CSRF-exempt, like login."""
    _require_multi_user()
    ip = request.client.host if request.client else None
    user = session.scalar(select(UserRow).where(UserRow.email == normalize_email(req.email)))
    if user is not None and user.is_active:
        token, expires_at = mint_password_reset(user.id)
        ttl_minutes = max(1, round((expires_at - datetime.now(UTC)).total_seconds() / 60))
        mail.get_mailer().send_template(
            user.email,
            "password-reset",
            {"reset_url": _reset_link(request, token), "ttl_minutes": ttl_minutes},
        )
        audit(
            "auth.password.reset_request",
            actor_id=user.id,
            ip=ip,
            user_agent=request.headers.get("user-agent"),
        )
    return Response(status_code=204)


@app.post(
    "/admin/users/{user_id}/reset-password",
    status_code=201,
    summary="Mint a password-reset token",
    tags=["admin"],
    operation_id="admin_reset_password",
)
def admin_reset_password(
    user_id: str,
    request: Request,
    session: Session = Depends(get_db),
    current_user: UserRow = Depends(get_current_user),
):
    """Mint a one-time password-reset token for ``user_id``. Admin-only.
    Returns the token and its expiry; the admin delivers the token out
    of band (the email-delivered story is a 0.6.0 follow-up). 404 if
    the user doesn't exist."""
    _require_admin(current_user)
    target = session.get(UserRow, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    token, expires_at = mint_password_reset(target.id)
    audit(
        "admin.user.reset_password",
        actor_id=current_user.id,
        target_type="user",
        target_id=user_id,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {"reset_token": token, "expires_at": expires_at.isoformat()}


@app.post(
    "/auth/password-reset/confirm",
    status_code=204,
    summary="Confirm a password reset",
    tags=["auth"],
    operation_id="auth_reset_password_confirm",
)
def auth_reset_password_confirm(
    req: PasswordResetConfirm,
    request: Request,
):
    """Redeem a reset token. Public (no session required) and in the CSRF
    allowlist for the same reason as /auth/login — a freshly-loaded
    redemption form has no session cookie to defend. On success every
    session for the user is revoked and the lockout is cleared."""
    _require_multi_user()
    user_id = redeem_password_reset(req.token, new_password=req.new_password)
    if user_id is None:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    audit(
        "auth.password.reset",
        actor_id=user_id,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return Response(status_code=204)


# --- API keys (Bearer auth) -------------------------------------------------
# Per-user long-lived keys for the CLI. The full key material is shown
# exactly once on creation; only the argon2id hash is persisted. Issue is
# throttled at API_KEY_ISSUE_LIMIT per API_KEY_ISSUE_WINDOW_HOURS via a
# per-user count over api_keys, windowed at query time.


def _api_key_to_dict(row: ApiKeyRow) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "prefix": row.prefix,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
    }


@app.post(
    "/auth/api-keys",
    status_code=201,
    summary="Create an API key",
    tags=["auth"],
    operation_id="create_api_key",
    responses={
        429: {
            "model": ErrorResponse,
            "description": "API key issuance rate limit exceeded.",
            "headers": {
                "Retry-After": {
                    "schema": {"type": "integer"},
                    "description": "Seconds until issuance is allowed again.",
                }
            },
        }
    },
)
def create_api_key(
    req: ApiKeyCreate,
    request: Request,
    session: Session = Depends(get_db),
    current_user: UserRow = Depends(get_current_user),
):
    """Mint a new per-user API key. The full key is returned in the
    response and never again — callers must store it in their secret
    manager immediately. Rate limited to
    ``API_KEY_ISSUE_LIMIT`` per user per ``API_KEY_ISSUE_WINDOW_HOURS``."""
    _require_multi_user()
    recent = session.scalar(
        select(func.count())
        .select_from(ApiKeyRow)
        .where(
            ApiKeyRow.user_id == current_user.id,
            ApiKeyRow.created_at
            >= datetime.now(UTC) - timedelta(hours=config.API_KEY_ISSUE_WINDOW_HOURS),
        )
    )
    if (recent or 0) >= config.API_KEY_ISSUE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=(
                f"API key issuance limit: max "
                f"{config.API_KEY_ISSUE_LIMIT} per "
                f"{config.API_KEY_ISSUE_WINDOW_HOURS}h per user"
            ),
            headers={"Retry-After": str(config.API_KEY_ISSUE_WINDOW_HOURS * 3600)},
        )
    full, kid = issue_api_key(current_user.id, name=req.name, expires_at=req.expires_at)
    audit(
        "auth.api_key.create",
        actor_id=current_user.id,
        target_type="api_key",
        target_id=kid,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        metadata={"name": req.name},
    )
    return {"id": kid, "name": req.name, "key": full}


@app.get("/auth/api-keys", summary="List API keys", tags=["auth"], operation_id="list_api_keys")
def list_api_keys(
    session: Session = Depends(get_db),
    current_user: UserRow = Depends(get_current_user),
):
    """List the caller's API keys. The hashed key material is never
    returned — only metadata (id, name, prefix, timestamps, revoked)."""
    _require_multi_user()
    rows = session.scalars(
        select(ApiKeyRow)
        .where(ApiKeyRow.user_id == current_user.id)
        .order_by(ApiKeyRow.created_at.desc())
    ).all()
    return {"keys": [_api_key_to_dict(r) for r in rows]}


@app.delete(
    "/auth/api-keys/{key_id}",
    status_code=204,
    summary="Revoke an API key",
    tags=["auth"],
    operation_id="revoke_api_key",
)
def revoke_api_key_endpoint(
    key_id: str,
    request: Request,
    session: Session = Depends(get_db),
    current_user: UserRow = Depends(get_current_user),
):
    """Revoke a key. Idempotent (already-revoked or unknown id is a 204)."""
    _require_multi_user()
    if revoke_api_key(key_id, user_id=current_user.id):
        audit(
            "auth.api_key.revoke",
            actor_id=current_user.id,
            target_type="api_key",
            target_id=key_id,
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
    return Response(status_code=204)


# --- Admin: user unlock -----------------------------------------------------


def _require_admin(user: UserRow) -> None:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")


@app.post(
    "/admin/users/{user_id}/unlock",
    status_code=204,
    summary="Unlock a user",
    tags=["admin"],
    operation_id="admin_unlock_user",
)
def admin_unlock_user(
    user_id: str,
    request: Request,
    session: Session = Depends(get_db),
    current_user: UserRow = Depends(get_current_user),
):
    """Clear the lockout and failure counter for `user_id`. Admin only.
    204 on success, 404 if the user doesn't exist."""
    _require_admin(current_user)
    target = session.get(UserRow, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    unlock_user(target, session=session)
    audit(
        "admin.user.unlock",
        actor_id=current_user.id,
        target_type="user",
        target_id=user_id,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return Response(status_code=204)


# --- Admin: users -----------------------------------------------------------


@app.get("/admin/users", summary="List users", tags=["admin"], operation_id="admin_list_users")
def admin_list_users(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    q: str | None = None,
    session: Session = Depends(get_db),
    current_user: UserRow = Depends(get_current_user),
):
    """List every user, newest-first. Admin-only. `q` is a
    case-insensitive substring match on email; limit/offset paginate."""
    _require_admin(current_user)
    base = select(UserRow)
    if q:
        base = base.where(UserRow.email.ilike(f"%{_escape_like(q)}%", escape="\\"))
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = session.scalars(
        base.order_by(UserRow.created_at.desc()).offset(offset).limit(limit)
    ).all()
    return {"total": total, "users": [_user_admin_to_dict(r) for r in rows]}


@app.post(
    "/admin/users/{user_id}/deactivate",
    status_code=204,
    summary="Deactivate a user",
    tags=["admin"],
    operation_id="admin_deactivate_user",
)
def admin_deactivate_user(
    user_id: str,
    request: Request,
    session: Session = Depends(get_db),
    current_user: UserRow = Depends(get_current_user),
):
    """Flip is_active=False for `user_id`. Admin-only. A
    deactivated user keeps their row but can't sign in. The calling admin
    can't deactivate themselves (400) — that's how you lock yourself out.
    404 on unknown user."""
    _require_admin(current_user)
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="You cannot deactivate yourself")
    target = session.get(UserRow, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if target.is_active:
        target.is_active = False
        session.commit()
        audit(
            "admin.user.deactivate",
            actor_id=current_user.id,
            target_type="user",
            target_id=user_id,
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
    return Response(status_code=204)


@app.post(
    "/admin/users/{user_id}/activate",
    status_code=204,
    summary="Activate a user",
    tags=["admin"],
    operation_id="admin_activate_user",
)
def admin_activate_user(
    user_id: str,
    request: Request,
    session: Session = Depends(get_db),
    current_user: UserRow = Depends(get_current_user),
):
    """Flip is_active=True for `user_id`. Admin-only. The
    reverse of /deactivate — lets an admin bring a deactivated user
    back. 404 on unknown user."""
    _require_admin(current_user)
    target = session.get(UserRow, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if not target.is_active:
        target.is_active = True
        session.commit()
        audit(
            "admin.user.activate",
            actor_id=current_user.id,
            target_type="user",
            target_id=user_id,
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
    return Response(status_code=204)


# --- Admin: invites ---------------------------------------------------------


@app.post(
    "/admin/invites",
    status_code=201,
    summary="Create an invite",
    tags=["admin"],
    operation_id="admin_create_invite",
)
def admin_create_invite(
    req: InviteCreateRequest,
    request: Request,
    session: Session = Depends(get_db),
    current_user: UserRow = Depends(get_current_user),
):
    """Issue a new invite. Admin-only. The redeem token is returned in the
    response so the admin can deliver it out of band."""
    _require_admin(current_user)
    if req.role not in ("user", "admin"):
        raise HTTPException(status_code=400, detail="role must be 'user' or 'admin'")
    if req.ttl_days <= 0 or req.ttl_days > 90:
        raise HTTPException(status_code=400, detail="ttl_days must be between 1 and 90")
    invite = create_invite(
        session,
        email=req.email,
        role=req.role,
        created_by=current_user.id,
        ttl_days=req.ttl_days,
    )
    audit(
        "admin.invite.create",
        actor_id=current_user.id,
        target_type="invite",
        target_id=invite.id,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        metadata={"role": req.role, "email": req.email},
    )
    return {
        "id": invite.id,
        "token": invite.token,
        "email": invite.email,
        "role": invite.role,
        "expires_at": invite.expires_at.isoformat(),
    }


# --- Admin: audit log -------------------------------------------------------


@app.get(
    "/admin/audit",
    summary="Read the audit log",
    tags=["admin"],
    operation_id="admin_list_audit",
)
def admin_list_audit(
    since: datetime | None = None,
    action: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db),
    current_user: UserRow = Depends(get_current_user),
):
    """Paginated read of the audit log. Admin-only. Filters: `since`
    (inclusive on created_at), `action` (exact match). Newest first."""
    _require_admin(current_user)
    q = select(AuditLogRow)
    if since is not None:
        q = q.where(AuditLogRow.created_at >= since)
    if action is not None:
        q = q.where(AuditLogRow.action == action)
    total = session.scalar(select(func.count()).select_from(q.subquery())) or 0
    q = q.order_by(AuditLogRow.created_at.desc()).offset(offset).limit(limit)
    rows = session.scalars(q).all()
    return {
        "total": total,
        "events": [
            {
                "id": r.id,
                "actor_id": r.actor_id,
                "action": r.action,
                "target_type": r.target_type,
                "target_id": r.target_id,
                "ip": r.ip,
                "user_agent": r.user_agent,
                "metadata": r.metadata_,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


# --- Per-user provider keys -------------------------------------------------


def _provider_key_to_dict(row: ProviderKeyRow) -> dict:
    return {
        "provider": row.provider,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
    }


@app.get(
    "/settings/provider-keys",
    summary="List configured provider keys",
    tags=["settings"],
    operation_id="get_provider_keys",
)
def get_provider_keys(
    session: Session = Depends(get_db),
    current_user: UserRow = Depends(get_current_user),
):
    """Which providers the caller has configured. The raw key material is
    never returned — only the provider name and timestamps."""
    _require_multi_user()
    rows = session.scalars(
        select(ProviderKeyRow)
        .where(ProviderKeyRow.user_id == current_user.id)
        .order_by(ProviderKeyRow.provider)
    ).all()
    return {"keys": [_provider_key_to_dict(r) for r in rows]}


@app.put(
    "/settings/provider-keys",
    summary="Set or clear a provider key",
    tags=["settings"],
    operation_id="upsert_provider_key",
)
def upsert_provider_key(
    req: ProviderKeysUpdate,
    request: Request,
    session: Session = Depends(get_db),
    current_user: UserRow = Depends(get_current_user),
):
    """Set (or clear) a per-user provider key. The raw key is encrypted
    at rest with Fernet; the master key is the SHA-256 of
    ``SECRETS_ENCRYPTION_KEY`` (see ``surgite.secrets``). The response
    is just a confirmation — the key material is never echoed back."""
    _require_multi_user()
    if req.provider not in summarizer.PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider {req.provider!r}; choose from {', '.join(summarizer.PROVIDERS)}",
        )
    existing = session.scalar(
        select(ProviderKeyRow).where(
            ProviderKeyRow.user_id == current_user.id,
            ProviderKeyRow.provider == req.provider,
        )
    )
    if req.clear:
        if existing is not None and existing.revoked_at is None:
            existing.revoked_at = datetime.now(UTC)
            session.commit()
            audit(
                "settings.provider_key.clear",
                actor_id=current_user.id,
                target_type="provider_key",
                target_id=req.provider,
                ip=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
            )
        return {"configured": False}
    if not req.key:
        raise HTTPException(status_code=400, detail="key is required unless clear=true")
    if existing is None:
        existing = ProviderKeyRow(
            user_id=current_user.id,
            org_id=current_user.personal_org_id,
            provider=req.provider,
            encrypted_key=encrypt(req.key),
        )
        session.add(existing)
    else:
        existing.encrypted_key = encrypt(req.key)
        existing.revoked_at = None
    session.commit()
    audit(
        "settings.provider_key.set",
        actor_id=current_user.id,
        target_type="provider_key",
        target_id=req.provider,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {"configured": True}


def _get_prompt_setting(
    session: Session, repo_id: int | None, owner_id: str
) -> PromptSettingsRow | None:
    """The settings row for `owner_id` scoped to `repo_id` (or the owner's
    global row for None). No fallback — returns None when this exact scope has
    no row yet."""
    return session.scalar(
        select(PromptSettingsRow).where(
            PromptSettingsRow.repo_id == repo_id,
            PromptSettingsRow.owner_id == owner_id,
        )
    )


def _get_or_create_prompt_setting(
    owner_id: str, session: Session | None = None, repo_id: int | None = None
) -> PromptSettingsRow:
    with session_scope(session) as s:
        row = _get_prompt_setting(s, repo_id, owner_id)
        if row is None:
            row = PromptSettingsRow(
                repo_id=repo_id, owner_id=owner_id, org_id=personal_org_id(s, owner_id)
            )
            s.add(row)
            s.commit()
            s.refresh(row)
        return row


def _resolve_settings_dict(session: Session, repo_id: int | None, owner_id: str) -> dict:
    """Settings that apply to `repo_id` for `owner_id`: its own row if it has
    one, else the owner's global default. This is the lookup the summarizer
    uses per repo."""
    row = _get_prompt_setting(session, repo_id, owner_id) if repo_id is not None else None
    if row is None:
        row = _get_or_create_prompt_setting(owner_id, session, repo_id=None)
    return _settings_row_to_dict(row)


def _settings_row_to_dict(row: PromptSettingsRow) -> dict:
    return {
        "repo_id": row.repo_id,
        "user_name": row.user_name,
        "user_role": row.user_role,
        "tone": row.tone,
        "group_count": row.group_count,
        "output_format": row.output_format,
        "custom_instructions": row.custom_instructions,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@app.get(
    "/settings/prompt",
    summary="Get prompt settings",
    tags=["settings"],
    operation_id="get_prompt_settings",
)
def get_prompt_settings(
    repo_id: int | None = None,
    session: Session = Depends(get_db),
    current_user: UserRow = Depends(get_current_user),
):
    """Return the prompt settings for `repo_id`. If that repo has no row of its
    own, return the global default (its `repo_id` will be null, signalling the
    UI that the values are inherited rather than repo-specific)."""
    row = _get_prompt_setting(session, repo_id, current_user.id) if repo_id is not None else None
    if row is None:
        row = _get_or_create_prompt_setting(current_user.id, session, repo_id=None)
    return _settings_row_to_dict(row)


@app.put(
    "/settings/prompt",
    summary="Update prompt settings",
    tags=["settings"],
    operation_id="update_prompt_settings",
)
def update_prompt_settings(
    update: PromptSettingsUpdate,
    repo_id: int | None = None,
    session: Session = Depends(get_db),
    current_user: UserRow = Depends(get_current_user),
):
    """Upsert the prompt settings for the caller, optionally scoped to a repo
    they own (`repo_id`). Only the provided fields are changed. 404 if
    `repo_id` is given but the caller doesn't own that repo."""
    if repo_id is not None and _owned_repo(session, repo_id, current_user.id) is None:
        raise HTTPException(status_code=404, detail="Repo not found")

    row = _get_prompt_setting(session, repo_id, current_user.id)
    if row is None:
        row = PromptSettingsRow(
            repo_id=repo_id, owner_id=current_user.id, org_id=current_user.personal_org_id
        )
        session.add(row)

    update_data = update.model_dump(exclude_none=True)
    for field, value in update_data.items():
        setattr(row, field, value)

    row.updated_at = datetime.now(UTC)
    session.commit()
    session.refresh(row)
    return _settings_row_to_dict(row)


@app.get("/commits", summary="List commits", tags=["commits"], operation_id="list_commits")
def list_commits(
    since: date | None = None,
    until: date | None = None,
    author: str | None = None,
    repo: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db),
    current_user: UserRow = Depends(get_current_user),
):
    """Paginated, filterable list of the caller's ingested commits. Filters:
    `since`/`until` (inclusive dates), `author` (substring), `repo` (name).
    Newest first. Scoped to the caller's own repos."""
    total, commits = _query_commits(
        since, until, author, repo, limit, offset, current_user.id, session=session
    )
    return {"total": total, "commits": commits}


def _looks_like_hash(value: str) -> bool:
    return len(value) >= 7 and all(c in "0123456789abcdef" for c in value.lower())


@app.get(
    "/commits/{hash}",
    summary="Get a commit by hash",
    tags=["commits"],
    operation_id="get_commit",
)
def get_commit(
    hash: str,
    session: Session = Depends(get_db),
    current_user: UserRow = Depends(get_current_user),
):
    """Resolve a full or abbreviated (>=7 hex chars) commit hash to one of the
    caller's commits. 400 on a malformed hash, 404 if no match, 409 if an
    abbreviation matches more than one commit."""
    if not _looks_like_hash(hash):
        raise HTTPException(
            status_code=400,
            detail="Hash must be hex and at least 7 characters",
        )

    rows = session.scalars(
        select(CommitRow).where(
            CommitRow.hash.startswith(hash.lower()),
            CommitRow.owner_id == current_user.id,
        )
    ).all()

    if not rows:
        raise HTTPException(status_code=404, detail="Commit not found")
    if len(rows) > 1:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Prefix matches multiple commits",
                "candidates": [r.hash for r in rows],
            },
        )
    return _row_to_dict(rows[0])


def _aggregate_commits(
    commits: list[dict],
) -> tuple[dict[str, int], dict[str, int], dict[str, list[Commit]]]:
    """Roll a list of commit dicts up into the per-repo and per-day counts the
    summary view needs, plus the Commit objects grouped by repo for the log."""
    by_repo: dict[str, int] = defaultdict(int)
    by_day: dict[str, int] = defaultdict(int)
    repo_commits: dict[str, list[Commit]] = {}
    for c in commits:
        repo_name = c["repo"]
        by_repo[repo_name] += 1
        by_day[c["date"]] += 1
        repo_commits.setdefault(repo_name, []).append(
            Commit(hash=c["hash"], date=c["date"], author=c["author"], message=c["message"])
        )
    return by_repo, by_day, repo_commits


def _repo_name_to_id(session: Session, owner_id: str) -> dict[str, int]:
    return {
        r.name: r.id
        for r in session.scalars(select(RepoRow).where(RepoRow.owner_id == owner_id)).all()
    }


def _check_ai_preconditions(request: Request, provider: str | None, total: int, *, user_id: str):
    """Shared gate for the AI paths: per-user + per-IP-outer rate limit.
    The per-user 5/60s bucket is the primary throttle — one user can't
    burn the LLM budget for everyone. The 100/60s per-IP
    outer is the backstop for the "fresh signup spam" case (an attacker
    cycling accounts can't share a per-user bucket because they have no
    user yet). Raises the appropriate HTTPException; returns the resolved
    provider on success."""
    check_summary_user_limit(request, user_id=user_id)
    check_ip_outer_rate_limit(request)
    if total > AI_SUMMARY_MAX_COMMITS:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Too many commits for AI summary "
                f"({total} > {AI_SUMMARY_MAX_COMMITS}); narrow the date range."
            ),
        )
    try:
        resolved = summarizer.resolve_provider(provider)
    except ProviderError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    # _resolve_key handles the per-user DB row vs env-var fallback.
    try:
        from surgite.summarizer import _resolve_key

        _resolve_key(resolved, user_id)
    except ProviderError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return resolved


@app.get(
    "/summary",
    summary="Commit summary for a period",
    tags=["summaries"],
    operation_id="get_summary",
)
async def summary(
    request: Request,
    since: date | None = None,
    until: date | None = None,
    author: str | None = None,
    repo: str | None = None,
    ai: bool = False,
    provider: str | None = None,
    combined: bool = False,
    commits: bool = False,
    session: Session = Depends(get_db),
    current_user: UserRow = Depends(get_current_user),
):
    """Commit summary for the period. With ai=true, returns one AI summary per
    repo (ai_summaries); the additional whole-log summary (ai_summary) costs an
    extra provider call and is only generated when combined=true.

    The raw `commits` list is omitted by default (it can be hundreds of KB the
    web UI never renders); pass commits=true to include it, or use /commits.

    This is a pure read against the DB; freshness is owned by the background
    ingest scheduler (see _scheduler_loop) and the per-repo BackgroundTask
    on POST /repos. No git fetch happens here."""
    total, commit_rows = _query_commits(
        since, until, author, repo, limit=None, offset=0, owner_id=current_user.id, session=session
    )
    by_repo, by_day, repo_commits = _aggregate_commits(commit_rows)
    log_by_repo = {name: format_log(cs) for name, cs in repo_commits.items()}

    ai_summary = None
    ai_provider = None
    ai_model = None
    ai_summaries = None
    if ai:
        _check_ai_preconditions(request, provider, total, user_id=current_user.id)
        name_to_id = _repo_name_to_id(session, current_user.id)
        global_settings = _resolve_settings_dict(session, None, current_user.id)
        settings_by_repo = {
            name: _resolve_settings_dict(session, name_to_id.get(name), current_user.id)
            for name in log_by_repo
        }
        ai_summaries = await summarizer.generate_summary_per_repo(
            log_by_repo,
            provider=provider,
            settings=global_settings,
            settings_by_repo=settings_by_repo,
            user_id=current_user.id,
        )
        if combined:
            all_commit_objs = [c for cs in repo_commits.values() for c in cs]
            try:
                result = await summarizer.generate_summary(
                    format_log(all_commit_objs),
                    provider=provider,
                    settings=global_settings,
                    user_id=current_user.id,
                )
            except ProviderError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
            except httpx.HTTPError as e:
                raise HTTPException(status_code=502, detail=f"Provider request failed: {e}") from e
            ai_summary = result["summary"]
            ai_provider = result["provider"]
            ai_model = result["model"]

    return {
        "period": {
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
        },
        "total_commits": total,
        "by_repo": dict(by_repo),
        "by_day": dict(sorted(by_day.items())),
        "commits": commit_rows if commits else [],
        "log_by_repo": log_by_repo,
        "ai_summary": ai_summary,
        "ai_provider": ai_provider,
        "ai_model": ai_model,
        "ai_summaries": ai_summaries,
    }


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.get(
    "/summary/stream",
    summary="Stream an AI summary (SSE)",
    tags=["summaries"],
    operation_id="stream_summary",
)
async def summary_stream(
    request: Request,
    since: date | None = None,
    until: date | None = None,
    author: str | None = None,
    repo: str | None = None,
    provider: str | None = None,
    session: Session = Depends(get_db),
    current_user: UserRow = Depends(get_current_user),
):
    """Server-sent-events variant of /summary?ai=true. Emits a `meta` event
    (the same stats/log payload /summary returns) followed by per-repo `delta`
    events as the provider streams tokens, a `repo_done`/`repo_error` per repo,
    and a final `done`. The UI fills each card in as text arrives instead of
    blocking on a spinner for the whole fan-out.

    All DB reads happen up front: the StreamingResponse generator runs after
    the request handler returns and the Depends-injected session is closed, so
    it must only touch the provider, never the DB."""
    total, commit_rows = _query_commits(
        since, until, author, repo, limit=None, offset=0, owner_id=current_user.id, session=session
    )
    _check_ai_preconditions(request, provider, total, user_id=current_user.id)

    by_repo, by_day, repo_commits = _aggregate_commits(commit_rows)
    log_by_repo = {name: format_log(cs) for name, cs in repo_commits.items()}
    name_to_id = _repo_name_to_id(session, current_user.id)
    global_settings = _resolve_settings_dict(session, None, current_user.id)
    settings_by_repo = {
        name: _resolve_settings_dict(session, name_to_id.get(name), current_user.id)
        for name in log_by_repo
    }
    resolved = summarizer.resolve_provider(provider)

    meta = {
        "period": {
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
        },
        "total_commits": total,
        "by_repo": dict(by_repo),
        "by_day": dict(sorted(by_day.items())),
        "repos": list(log_by_repo),
        "provider": resolved.name,
        "model": summarizer.display_model(resolved),
    }

    async def event_stream():
        yield _sse("meta", meta)
        async with httpx.AsyncClient() as client:
            for name, log_text in log_by_repo.items():
                try:
                    async for chunk in summarizer.stream_summary(
                        log_text,
                        provider=provider,
                        settings=settings_by_repo.get(name, global_settings),
                        client=client,
                        user_id=current_user.id,
                    ):
                        yield _sse("delta", {"repo": name, "text": chunk})
                    yield _sse(
                        "repo_done",
                        {
                            "repo": name,
                            "provider": resolved.name,
                            "model": summarizer.display_model(resolved),
                        },
                    )
                except (ProviderError, httpx.HTTPError) as e:
                    log.warning("Stream failed for repo %s: %s", name, e, extra={"repo": name})
                    yield _sse("repo_error", {"repo": name, "detail": str(e)})
        yield _sse("done", {})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get(
    "/repos",
    response_model=RepoListResponse,
    summary="List repos",
    tags=["repos"],
    operation_id="list_repos",
)
def list_repos(
    session: Session = Depends(get_db),
    current_user: UserRow = Depends(get_current_user),
):
    """List the repos the caller has registered, with ingest timestamps."""
    rows = session.scalars(select(RepoRow).where(RepoRow.owner_id == current_user.id)).all()
    return {"repos": [_repo_to_dict(r) for r in rows]}


@app.post(
    "/repos",
    status_code=201,
    response_model=RepoResponse,
    summary="Register a repo",
    tags=["repos"],
    operation_id="create_repo",
)
def create_repo(
    req: RepoCreate,
    background: BackgroundTasks,
    request: Request,
    session: Session = Depends(get_db),
    current_user: UserRow = Depends(get_current_user),
):
    """Register a new repo. In multi_user mode with
    ``REPO_ADD_GLOBAL_ONLY=true`` only admins can add — the clone-url path
    is a code-execution surface and the operator probably wants to gate it.
    The default is open to every authenticated user (the 0.4.0 UX)."""
    from surgite.git import _repo_name_from_url, is_remote_url

    if config.REPO_ADD_GLOBAL_ONLY and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Only admins can add repos")
    if not is_remote_url(req.url):
        raise HTTPException(
            status_code=400,
            detail="Must be a remote git URL (https://, git@, git:// or ssh://)",
        )
    name = _repo_name_from_url(req.url)

    existing = session.scalar(select(RepoRow).where(RepoRow.clone_url == req.url))
    if existing:
        raise HTTPException(status_code=409, detail="Repo already registered")
    if session.scalar(select(RepoRow).where(RepoRow.name == name)):
        raise HTTPException(status_code=409, detail="A repo with this name already exists")
    repo = RepoRow(
        name=name,
        clone_url=req.url,
        owner_id=current_user.id,
        org_id=current_user.personal_org_id,
        added_at=datetime.now(UTC),
    )
    session.add(repo)
    session.commit()
    session.refresh(repo)

    audit(
        "repo.create",
        actor_id=current_user.id,
        target_type="repo",
        target_id=str(repo.id),
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        metadata={"name": name, "clone_url": req.url},
    )
    _claim_ingest(repo.id)
    background.add_task(_run_ingest, repo.id, name)
    return _repo_to_dict(repo)


@app.post(
    "/repos/{repo_id}/ingest",
    status_code=202,
    response_model=IngestAccepted,
    summary="Sync a repo",
    tags=["repos"],
    operation_id="ingest_repo",
)
def ingest_repo(
    repo_id: int,
    background: BackgroundTasks,
    session: Session = Depends(get_db),
    current_user: UserRow = Depends(get_current_user),
):
    """Queue an ingest for a repository owned by the caller."""
    repo = _owned_repo(session, repo_id, current_user.id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")
    if not _claim_ingest(repo_id):
        raise HTTPException(status_code=409, detail="Ingest already in progress")
    background.add_task(_run_ingest, repo_id, repo.name)
    return {"accepted": True}


@app.delete(
    "/repos/{repo_id}",
    status_code=204,
    summary="Delete a repo",
    tags=["repos"],
    operation_id="delete_repo",
)
def delete_repo(
    repo_id: int,
    session: Session = Depends(get_db),
    current_user: UserRow = Depends(get_current_user),
):
    """Remove a repo the caller owns (and its commits cascade). 404 if the
    repo doesn't exist or isn't owned by the caller."""
    repo = _owned_repo(session, repo_id, current_user.id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")
    session.delete(repo)
    session.commit()


@app.post(
    "/summaries",
    status_code=201,
    summary="Create a shareable summary link",
    tags=["summaries"],
    operation_id="create_share",
)
def create_share(
    req: ShareCreate,
    session: Session = Depends(get_db),
    current_user: UserRow = Depends(get_current_user),
):
    """Persist the parameters of a summary behind a short slug. The slug is the
    only secret guarding it — resolving /summaries/{slug} re-runs the query.
    The share records its creator (`owner_id`), which GET /summaries/{slug}
    checks — a non-owner gets the same 404 an unknown slug does."""
    now = datetime.now(UTC)
    slug = secrets.token_urlsafe(8)
    row = SharedSummaryRow(
        slug=slug,
        owner_id=current_user.id,
        org_id=current_user.personal_org_id,
        params=req.model_dump(),
        created_at=now,
        expires_at=now + timedelta(days=SHARE_TTL_DAYS),
    )
    session.add(row)
    session.commit()
    return {"slug": slug, "expires_at": row.expires_at.isoformat()}


def _share_to_dict(row: SharedSummaryRow) -> dict:
    return {
        "slug": row.slug,
        "params": row.params,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
    }


def _as_utc(dt: datetime) -> datetime:
    """Treat a tz-naive datetime as UTC. Postgres returns aware datetimes for
    our timezone=True columns, but SQLite (the test DB) hands back naive ones —
    normalise so the expiry comparison works on both."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


@app.get(
    "/summaries/mine",
    summary="List my shared summaries",
    tags=["summaries"],
    operation_id="list_my_shares",
)
def list_my_shares(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db),
    current_user: UserRow = Depends(get_current_user),
):
    """The caller's saved shares, newest first. Expired shares are
    excluded — they're not resolvable, so showing them is dead UI."""
    now = datetime.now(UTC)
    base = select(SharedSummaryRow).where(
        SharedSummaryRow.owner_id == current_user.id,
        SharedSummaryRow.expires_at > now,
    )
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = session.scalars(
        base.order_by(SharedSummaryRow.created_at.desc()).offset(offset).limit(limit)
    ).all()
    return {"total": total, "summaries": [_share_to_dict(r) for r in rows]}


@app.get(
    "/summaries/{slug}",
    summary="Resolve a shared summary",
    tags=["summaries"],
    operation_id="get_share",
)
def get_share(
    slug: str,
    session: Session = Depends(get_db),
    current_user: UserRow = Depends(get_current_user),
):
    """Resolve a slug to its stored summary params. 404 for unknown,
    expired, OR not-owned-by-the-caller slugs. The 404-not-403 is
    deliberate: a 403 would tell an attacker "this slug exists, you just
    can't see it", which is an enumeration vector."""
    row = session.get(SharedSummaryRow, slug)
    if (
        row is None
        or _as_utc(row.expires_at) <= datetime.now(UTC)
        or row.owner_id != current_user.id
    ):
        raise HTTPException(status_code=404, detail="Share not found or expired")
    return _share_to_dict(row)


# Serve the built SvelteKit SPA same-origin in production. Mounted LAST so it never
# shadows the API routes above, and only when the build exists (in dev the frontend
# runs on the Vite server instead, so this is skipped and startup doesn't fail).
_FRONTEND_BUILD = Path(__file__).resolve().parent.parent / "frontend" / "build"


@app.get(
    "/s/{slug}",
    summary="Shared-summary SPA shell",
    tags=["ui"],
    operation_id="share_page",
)
def share_page(slug: str):
    """Serve the SPA shell for a shared-summary deep link so a hard refresh on
    /s/{slug} works. The client-side route reads the slug and re-runs the
    query via GET /summaries/{slug}. In dev (no build) the Vite server handles
    this route instead, so a 404 here is correct."""
    return _serve_spa_shell()


@app.get("/login", summary="Login SPA shell", tags=["ui"], operation_id="login_page")
def login_page():
    """SPA shell for /login. The client-side route renders the login form. In
    dev (no build) the Vite server handles this route instead."""
    return _serve_spa_shell()


@app.get("/signup", summary="Signup SPA shell", tags=["ui"], operation_id="signup_page")
def signup_page():
    """SPA shell for /signup. The client-side route reads ``?token=...`` from
    the query string and renders the redemption form. In dev (no build) the
    Vite server handles this route instead."""
    return _serve_spa_shell()


def _serve_spa_shell():
    fallback = _FRONTEND_BUILD / "200.html"
    if fallback.is_file():
        return FileResponse(fallback)
    raise HTTPException(status_code=404, detail="Frontend build not available")


if _FRONTEND_BUILD.is_dir():
    app.mount("/", StaticFiles(directory=_FRONTEND_BUILD, html=True), name="frontend")
