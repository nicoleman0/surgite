import asyncio
import json
import logging
import os
import secrets
import threading
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import partial
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, or_, select
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
from surgite.connections import (
    _encrypt_secret,
    connection_to_dict,
    consume_github_state,
    github_repositories,
    github_token,
    repo_credentials,
    start_github_authorization,
    token_host,
)
from surgite.db import (
    ApiKeyRow,
    AuditLogRow,
    CommitRow,
    GitConnectionRow,
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
from surgite.git import GitCredentials, get_raw_log, ls_remote, parse_log
from surgite.logging_config import configure_logging
from surgite.models import Commit
from surgite.rate_limit import (
    check_ip_outer_rate_limit,
    check_summary_user_limit,
)
from surgite.schemas import (
    ApiKeyCreate,
    ErrorResponse,
    GitConnectionCreate,
    GitConnectionResponse,
    IngestAccepted,
    InviteCreateRequest,
    LoginRequest,
    PasswordChange,
    PasswordResetConfirm,
    PasswordResetRequest,
    PromptSettingsUpdate,
    ProviderKeysResponse,
    ProviderKeysUpdate,
    RedeemInviteRequest,
    RepoConnectionUpdate,
    RepoCreate,
    RepoListResponse,
    RepoResponse,
    ShareCreate,
)
from surgite.secrets import encrypt
from surgite.summarizer import ProviderError

configure_logging()
log = logging.getLogger(__name__)

AI_SUMMARY_MAX_COMMITS = 500
INGEST_ERROR = "Could not sync repository; check its URL and credentials."

_active_ingests: set[int] = set()
_active_ingests_lock = threading.Lock()


def _ingest_interval_seconds() -> int:
    """Read the scheduler interval at startup."""
    return int(os.environ.get("INGEST_INTERVAL", "300"))


def _ingest_concurrency() -> int:
    return max(1, int(os.environ.get("INGEST_CONCURRENCY", "4")))


def _claim_ingest(repo_id: int) -> bool:
    """Reserve a repo for ingestion in this process."""
    with _active_ingests_lock:
        if repo_id in _active_ingests:
            return False
        _active_ingests.add(repo_id)
        return True


def _release_ingest(repo_id: int) -> None:
    with _active_ingests_lock:
        _active_ingests.discard(repo_id)


def _ingest_all_repos(executor: ThreadPoolExecutor | None = None) -> list[dict]:
    """Ingest every unreserved repo and return their results."""
    results: list[dict] = []
    with session_scope() as s:
        repo_data = [(r.id, r.name) for r in s.scalars(select(RepoRow)).all()]

    work = [(repo_id, repo_name) for repo_id, repo_name in repo_data if _claim_ingest(repo_id)]
    if executor is None:
        return [_run_ingest(repo_id, fallback_name=repo_name) for repo_id, repo_name in work]
    futures = [executor.submit(_run_ingest, repo_id, repo_name) for repo_id, repo_name in work]
    for future in futures:
        results.append(future.result())

    return results


def _ingest_repo(
    repo_id: int,
    repo_name: str,
    clone_url: str,
    owner_id: str,
    since: date | None = None,
    until: date | None = None,
    session: Session | None = None,
    credentials: GitCredentials | None = None,
) -> dict:
    """Ingest commits for one repo."""
    from surgite.config import REPO_CACHE_DIR
    from surgite.git import ensure_repo

    actual_path = ensure_repo(
        str(repo_id), clone_url, REPO_CACHE_DIR, config.GIT_TIMEOUT_SECONDS, credentials
    )

    since_str = (since or date.today() - timedelta(days=7)).isoformat()
    until_str = (until or date.today()).isoformat()

    raw = get_raw_log(actual_path, since_str, until_str, timeout=config.GIT_TIMEOUT_SECONDS)
    commits = parse_log(raw)
    inserted = updated = unchanged = 0

    with session_scope(session) as s:
        existing: set[str] = set()
        for start in range(0, len(commits), 500):
            hashes = [c.hash for c in commits[start : start + 500]]
            existing.update(
                s.scalars(
                    select(CommitRow.hash).where(
                        CommitRow.repo_id == repo_id, CommitRow.hash.in_(hashes)
                    )
                )
            )
        now = datetime.now(UTC)
        new_rows = [
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
            for c in commits
            if c.hash not in existing
        ]
        for start in range(0, len(new_rows), 500):
            s.add_all(new_rows[start : start + 500])
        inserted = len(new_rows)
        unchanged = len(commits) - inserted

        s.commit()

    return {"repo": repo_name, "inserted": inserted, "updated": updated, "unchanged": unchanged}


def _run_ingest(repo_id: int, fallback_name: str | None = None) -> dict:
    """Run a reserved ingest and persist its outcome."""
    repo_name = fallback_name or str(repo_id)
    started = time.monotonic()
    try:
        with session_scope() as s:
            repo = s.get(RepoRow, repo_id)
            if repo is None:
                return {"repo": repo_name, "skipped": "deleted"}
            repo_name = repo.name
            clone_url = repo.clone_url
            owner_id = repo.owner_id
            credentials = repo_credentials(repo, s)

        result = _ingest_repo(repo_id, repo_name, clone_url, owner_id, credentials=credentials)
        completed_at = datetime.now(UTC)
        with session_scope() as s:
            repo = s.get(RepoRow, repo_id)
            if repo is not None:
                repo.last_ingest_attempt_at = completed_at
                repo.last_ingested_at = completed_at
                repo.last_ingest_error = None
                s.commit()
        log.info(
            "Ingest completed for %s",
            repo_name,
            extra={**result, "duration_seconds": round(time.monotonic() - started, 3)},
        )
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
    """Delete expired shared summaries."""
    now = datetime.now(UTC)
    with session_scope() as s:
        rows = s.scalars(select(SharedSummaryRow).where(SharedSummaryRow.expires_at <= now)).all()
        for row in rows:
            s.delete(row)
        s.commit()
        return len(rows)


def _scheduler_tick(executor: ThreadPoolExecutor) -> None:
    """Run one background-maintenance pass."""
    _ingest_all_repos(executor)
    _delete_expired_summaries()
    purge_expired_sessions()


async def _scheduler_loop(interval: int, executor: ThreadPoolExecutor) -> None:
    """Run maintenance in a worker thread at the configured interval."""
    log.info("Background ingest scheduler started (interval=%ds)", interval)
    try:
        while True:
            try:
                await asyncio.to_thread(_scheduler_tick, executor)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("Background ingest loop failed: %s", exc)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        log.info("Background ingest scheduler stopped")
        raise


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Manage bootstrap and background-ingest resources."""
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

    interval = _ingest_interval_seconds()
    executor = ThreadPoolExecutor(
        max_workers=_ingest_concurrency(), thread_name_prefix="surgite-ingest"
    )
    app.state.ingest_executor = executor
    task: asyncio.Task | None = None
    if interval > 0:
        task = asyncio.create_task(_scheduler_loop(interval, executor))
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        executor.shutdown(wait=True, cancel_futures=True)
        if getattr(app.state, "ingest_executor", None) is executor:
            app.state.ingest_executor = None


async def _await_ingest(future: Future[dict]) -> None:
    await asyncio.wrap_future(future)


def _submit_ingest(
    background: BackgroundTasks, request: Request, repo_id: int, repo_name: str
) -> None:
    executor: ThreadPoolExecutor | None = getattr(request.app.state, "ingest_executor", None)
    if executor is None:  # TestClient without a lifespan; production always has the pool.
        background.add_task(_run_ingest, repo_id, repo_name)
    else:
        background.add_task(_await_ingest, executor.submit(_run_ingest, repo_id, repo_name))


# Shared OpenAPI error envelope.
_ERROR_RESPONSES: dict = {
    "default": {"model": ErrorResponse, "description": 'Error: `{"detail": "..."}`.'}
}

app = FastAPI(title="surgite", lifespan=_lifespan, responses=_ERROR_RESPONSES)

# Development-only Vite origins; production is same-origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# Stateful SPA requests must carry an intent header.
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
        "connection_id": row.connection_id,
    }


def _owned_repo(session: Session, repo_id: int, owner_id: str) -> RepoRow | None:
    """Return an owned repo without revealing cross-owner rows."""
    repo = session.get(RepoRow, repo_id)
    return repo if repo is not None and repo.owner_id == owner_id else None


def _owned_connection(
    session: Session, connection_id: str, owner_id: str
) -> GitConnectionRow | None:
    row = session.get(GitConnectionRow, connection_id)
    return row if row is not None and row.owner_id == owner_id else None


def _usable_connection(session: Session, connection_id: str, owner_id: str) -> GitConnectionRow:
    row = _owned_connection(session, connection_id, owner_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    if row.disconnected_at is not None:
        raise HTTPException(status_code=409, detail="Connection required")
    return row


def _row_to_dict(row: CommitRow) -> dict:
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
    """Query one owner's commits and return the total and page."""
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
    """Check process liveness and database readiness."""
    session.execute(select(1))
    return {"status": "ok"}


async def _check_providers() -> dict[str, str]:
    """Check configured provider hosts without making billable requests."""
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
    """Check the database, one caller-owned repo, and provider hosts."""
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
            credentials = repo_credentials(repo, session)
            await loop.run_in_executor(
                None, partial(ls_remote, repo.clone_url, credentials=credentials)
            )
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
    """List available providers and the caller's configuration status."""
    if config.AUTH_MODE == "multi_user" and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    if config.AUTH_MODE == "multi_user":
        return {
            "default": summarizer.default_provider(),
            "providers": summarizer.provider_status_for(current_user.id),
        }
    return {"default": summarizer.default_provider(), "providers": summarizer.provider_status()}


# --- Auth (multi_user only) -------------------------------------------------


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
    """Serialize the fields visible to admins."""
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
    """Authenticate credentials and create a cookie session."""
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
            record_login_failure(user, session=session)
        audit(
            "auth.login.fail",
            actor_id=user.id if user else None,
            ip=ip,
            user_agent=request.headers.get("user-agent"),
            metadata={"email": normalize_email(req.email)},
        )
        raise HTTPException(status_code=401, detail="Invalid email or password")
    assert user is not None
    if is_locked(user):
        assert user.locked_until is not None
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
    """Revoke the current session and clear its cookie."""
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
    """Redeem an invite and start the new user's session."""
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


# User-facing alias for invite redemption.
app.post(
    "/signup",
    status_code=201,
    summary="Sign up (redeem-invite alias)",
    tags=["auth"],
    operation_id="signup",
)(auth_redeem_invite)


@app.get("/auth/me", summary="Current user", tags=["auth"], operation_id="auth_me")
def auth_me(current_user: UserRow = Depends(get_current_user)):
    """Return the current user."""
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
    """Change the password and revoke the user's other sessions."""
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


def _reset_link(request: Request, token: str) -> str:
    """Build a password-reset URL from PUBLIC_URL or the request origin."""
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
    """Email a reset link without disclosing whether the account exists."""
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
    """Mint a one-time reset token for a user."""
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
    """Redeem a reset token and revoke the user's sessions."""
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
    """Create a rate-limited API key and return its plaintext once."""
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
    """List the caller's API key metadata."""
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
    """Idempotently revoke an API key."""
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
    """Clear a user's login lockout."""
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
    """List users, optionally filtering by email."""
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
    """Deactivate a user while preventing admin self-lockout."""
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
    """Reactivate a user."""
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
    """Create an invite and return its redemption token."""
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
    """List audit events with optional time and action filters."""
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
    response_model=ProviderKeysResponse,
)
def get_provider_keys(
    session: Session = Depends(get_db),
    current_user: UserRow = Depends(get_current_user),
):
    """List provider names and the caller's key metadata, never key values."""
    _require_multi_user()
    rows = session.scalars(
        select(ProviderKeyRow)
        .where(ProviderKeyRow.user_id == current_user.id)
        .order_by(ProviderKeyRow.provider)
    ).all()
    return {
        "providers": list(summarizer.PROVIDERS),
        "default": summarizer.default_provider(),
        "keys": [_provider_key_to_dict(r) for r in rows],
    }


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
    """Encrypt and store, replace, or revoke a provider key."""
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
    """Return the exact prompt-settings row without fallback."""
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
    """Resolve repo prompt settings with global fallback."""
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
    """Return repo prompt settings with global fallback."""
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
    """Update global or repo-specific prompt settings."""
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
    """List the caller's commits with filters and pagination."""
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
    repo: str | None = None,
    session: Session = Depends(get_db),
    current_user: UserRow = Depends(get_current_user),
):
    """Resolve a full or abbreviated hash within the caller's commits."""
    if not _looks_like_hash(hash):
        raise HTTPException(
            status_code=400,
            detail="Hash must be hex and at least 7 characters",
        )

    q = select(CommitRow).where(
        CommitRow.hash.startswith(hash.lower()), CommitRow.owner_id == current_user.id
    )
    if repo is not None:
        repo_row = session.scalar(
            select(RepoRow).where(RepoRow.name == repo, RepoRow.owner_id == current_user.id)
        )
        if repo_row is None:
            raise HTTPException(status_code=404, detail="Commit not found")
        q = q.where(CommitRow.repo_id == repo_row.id)
    rows = session.scalars(q.order_by(CommitRow.repo_id)).all()

    if not rows:
        raise HTTPException(status_code=404, detail="Commit not found")
    if len({row.hash for row in rows}) > 1:
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
    """Aggregate commit counts and group commits by repo."""
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


def _source_sync_snapshot(
    session: Session, owner_id: str, repo: str | None
) -> dict[str, str | None]:
    """Capture conservative ingest times before querying commits."""
    query = select(RepoRow).where(RepoRow.owner_id == owner_id)
    if repo is not None:
        query = query.where(RepoRow.name == repo)
    return {
        row.name: row.last_ingested_at.isoformat() if row.last_ingested_at else None
        for row in session.scalars(query).all()
    }


@dataclass(frozen=True)
class PreparedSummary:
    total: int
    by_repo: dict[str, int]
    by_day: dict[str, int]
    log_by_repo: dict[str, str]
    source_synced_at: dict[str, str | None]
    global_settings: dict
    settings_by_repo: dict[str, dict]
    commits: list[dict]


def _prepare_summary(
    *,
    since: date | None,
    until: date | None,
    author: str | None,
    repo: str | None,
    owner_id: str,
    ai: bool,
) -> PreparedSummary:
    """Read and format one summary snapshot in a short-lived worker session."""
    with session_scope() as session:
        source_synced_at = _source_sync_snapshot(session, owner_id, repo)
        q = select(CommitRow).where(CommitRow.owner_id == owner_id)
        if since:
            q = q.where(CommitRow.date >= since)
        if until:
            q = q.where(CommitRow.date <= until)
        if author:
            q = q.where(CommitRow.author.ilike(f"%{_escape_like(author)}%", escape="\\"))
        if repo:
            repo_row = session.scalar(
                select(RepoRow).where(RepoRow.name == repo, RepoRow.owner_id == owner_id)
            )
            if repo_row is None:
                return PreparedSummary(0, {}, {}, {}, {}, {}, {}, [])
            q = q.where(CommitRow.repo_id == repo_row.id)

        total = session.scalar(select(func.count()).select_from(q.subquery())) or 0
        if ai and total > AI_SUMMARY_MAX_COMMITS:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Too many commits for AI summary ({total} > {AI_SUMMARY_MAX_COMMITS}); "
                    "narrow the date range."
                ),
            )
        # The extra row catches a concurrent ingest between count and fetch.
        rows = session.scalars(
            q.order_by(CommitRow.date.desc()).limit(501)
            if ai
            else q.order_by(CommitRow.date.desc())
        ).all()
        if ai and len(rows) > AI_SUMMARY_MAX_COMMITS:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Too many commits for AI summary (> {AI_SUMMARY_MAX_COMMITS}); narrow the date range."
                ),
            )
        commit_rows = [_row_to_dict(row) for row in rows]
        by_repo, by_day, repo_commits = _aggregate_commits(commit_rows)
        log_by_repo = {name: format_log(commits) for name, commits in repo_commits.items()}
        repo_ids = [row.repo_id for row in rows]
        settings_rows = session.scalars(
            select(PromptSettingsRow).where(
                PromptSettingsRow.owner_id == owner_id,
                or_(PromptSettingsRow.repo_id.is_(None), PromptSettingsRow.repo_id.in_(repo_ids)),
            )
        ).all()
        settings = {row.repo_id: _settings_row_to_dict(row) for row in settings_rows}
        global_settings = settings.get(None, {})
        names_by_id = {
            row.id: row.name
            for row in session.scalars(select(RepoRow).where(RepoRow.id.in_(repo_ids))).all()
        }
        settings_by_repo = {
            names_by_id[repo_id]: settings.get(repo_id, global_settings)
            for repo_id in set(repo_ids)
            if repo_id in names_by_id
        }
    return PreparedSummary(
        total,
        dict(by_repo),
        dict(sorted(by_day.items())),
        log_by_repo,
        source_synced_at,
        global_settings,
        settings_by_repo,
        commit_rows,
    )


async def _check_ai_preconditions(
    request: Request, provider: str | None, total: int, *, user_id: str
):
    """Apply AI limits and resolve the provider credentials."""
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
    try:
        from surgite.summarizer import _resolve_key

        api_key = await asyncio.to_thread(_resolve_key, resolved, user_id)
    except ProviderError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return resolved, api_key


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
    """Summarize stored commits, optionally with per-repo AI output."""
    prepared = await asyncio.to_thread(
        _prepare_summary,
        since=since,
        until=until,
        author=author,
        repo=repo,
        owner_id=current_user.id,
        ai=ai,
    )

    ai_summary = None
    ai_provider = None
    ai_model = None
    ai_summaries = None
    if ai:
        _, api_key = await _check_ai_preconditions(
            request, provider, prepared.total, user_id=current_user.id
        )
        ai_summaries = await summarizer.generate_summary_per_repo(
            prepared.log_by_repo,
            provider=provider,
            settings=prepared.global_settings,
            settings_by_repo=prepared.settings_by_repo,
            user_id=current_user.id,
            api_key=api_key,
        )
        if combined:
            try:
                result = await summarizer.generate_summary(
                    "\n".join(prepared.log_by_repo.values()),
                    provider=provider,
                    settings=prepared.global_settings,
                    user_id=current_user.id,
                    api_key=api_key,
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
        "total_commits": prepared.total,
        "by_repo": prepared.by_repo,
        "by_day": prepared.by_day,
        "source_synced_at": {
            name: prepared.source_synced_at.get(name) for name in prepared.by_repo
        },
        "commits": prepared.commits if commits else [],
        "log_by_repo": prepared.log_by_repo,
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
    """Stream per-repo AI summaries as server-sent events."""
    prepared = await asyncio.to_thread(
        _prepare_summary,
        since=since,
        until=until,
        author=author,
        repo=repo,
        owner_id=current_user.id,
        ai=True,
    )
    resolved, api_key = await _check_ai_preconditions(
        request, provider, prepared.total, user_id=current_user.id
    )

    meta = {
        "period": {
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
        },
        "total_commits": prepared.total,
        "by_repo": prepared.by_repo,
        "by_day": prepared.by_day,
        "source_synced_at": {
            name: prepared.source_synced_at.get(name) for name in prepared.by_repo
        },
        "repos": list(prepared.log_by_repo),
        "provider": resolved.name,
        "model": summarizer.display_model(resolved),
    }

    async def event_stream() -> AsyncIterator[str]:
        yield _sse("meta", meta)
        if not prepared.log_by_repo:
            yield _sse("done", {})
            return
        async with httpx.AsyncClient() as client:
            try:
                model = await summarizer.resolve_model(client, resolved, api_key)
            except ProviderError as exc:
                log.warning("Summary model discovery failed: %s", exc)
                for name in prepared.log_by_repo:
                    yield _sse("repo_error", {"repo": name, "detail": str(exc)})
                yield _sse("done", {})
                return
            queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue(maxsize=64)
            sem = asyncio.Semaphore(summarizer._MAX_PARALLEL_SUMMARIES)

            async def produce(name: str, log_text: str) -> None:
                async with sem:
                    try:
                        async for chunk in summarizer.stream_summary(
                            log_text,
                            provider=provider,
                            settings=prepared.settings_by_repo.get(name, prepared.global_settings),
                            model=model,
                            client=client,
                            user_id=current_user.id,
                            api_key=api_key,
                        ):
                            await queue.put(("delta", {"repo": name, "text": chunk}))
                        terminal = (
                            "repo_done",
                            {"repo": name, "provider": resolved.name, "model": model},
                        )
                    except (ProviderError, httpx.HTTPError) as exc:
                        log.warning(
                            "Stream failed for repo %s: %s", name, exc, extra={"repo": name}
                        )
                        terminal = ("repo_error", {"repo": name, "detail": str(exc)})
                    except Exception:
                        log.exception("Stream failed unexpectedly for repo %s", name)
                        terminal = (
                            "repo_error",
                            {"repo": name, "detail": "Summary stream failed."},
                        )
                    await queue.put(terminal)

            tasks = [
                asyncio.create_task(produce(name, log_text))
                for name, log_text in prepared.log_by_repo.items()
            ]
            try:
                remaining = len(tasks)
                while remaining:
                    event, data = await queue.get()
                    yield _sse(event, data)
                    if event in {"repo_done", "repo_error"}:
                        remaining -= 1
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        yield _sse("done", {})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get(
    "/connections",
    response_model=list[GitConnectionResponse],
    summary="List Git connections",
    tags=["connections"],
)
def list_connections(
    session: Session = Depends(get_db), current_user: UserRow = Depends(get_current_user)
):
    """List the caller's connection metadata without secret material."""
    rows = session.scalars(
        select(GitConnectionRow)
        .where(GitConnectionRow.owner_id == current_user.id)
        .order_by(GitConnectionRow.name)
    ).all()
    return [connection_to_dict(row, session) for row in rows]


@app.post(
    "/connections",
    status_code=201,
    response_model=GitConnectionResponse,
    summary="Create HTTPS token connection",
    tags=["connections"],
)
def create_connection(
    req: GitConnectionCreate,
    session: Session = Depends(get_db),
    current_user: UserRow = Depends(get_current_user),
):
    """Store a reusable HTTPS credential for the current user."""
    name = req.name.strip()
    if not name or not req.username.strip() or not req.token.strip():
        raise HTTPException(status_code=400, detail="Name, username, and token are required")
    try:
        host = token_host(req.origin)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    existing = session.scalar(
        select(GitConnectionRow).where(
            GitConnectionRow.owner_id == current_user.id, GitConnectionRow.name == name
        )
    )
    if existing and (existing.kind != "token" or existing.disconnected_at is None):
        raise HTTPException(status_code=409, detail="A connection with this name already exists")
    row = existing or GitConnectionRow(
        owner_id=current_user.id, org_id=current_user.personal_org_id, name=name, kind="token"
    )
    row.host = host
    row.encrypted_secret = _encrypt_secret(
        {
            "origin": req.origin.rstrip("/"),
            "username": req.username.strip(),
            "token": req.token.strip(),
        }
    )
    row.disconnected_at = None
    row.updated_at = datetime.now(UTC)
    session.add(row)
    session.commit()
    session.refresh(row)
    audit(
        "connection.create",
        actor_id=current_user.id,
        target_type="connection",
        target_id=row.id,
        metadata={"kind": "token", "host": host},
    )
    return connection_to_dict(row, session)


@app.delete(
    "/connections/{connection_id}",
    status_code=204,
    summary="Disconnect Git connection",
    tags=["connections"],
)
def disconnect_connection(
    connection_id: str,
    session: Session = Depends(get_db),
    current_user: UserRow = Depends(get_current_user),
):
    row = _owned_connection(session, connection_id, current_user.id)
    if row is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    row.encrypted_secret = None
    row.disconnected_at = row.updated_at = datetime.now(UTC)
    session.commit()


@app.get("/connections/github/start", summary="Start GitHub connection", tags=["connections"])
def github_start(
    session: Session = Depends(get_db), current_user: UserRow = Depends(get_current_user)
):
    try:
        state = start_github_authorization(current_user.id, session)
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "url": f"https://github.com/login/oauth/authorize?client_id={config.GITHUB_APP_CLIENT_ID}&state={state}"
    }


@app.get("/connections/github/callback", summary="Complete GitHub connection", tags=["connections"])
def github_callback(
    code: str,
    state: str,
    session: Session = Depends(get_db),
    current_user: UserRow = Depends(get_current_user),
):
    try:
        consume_github_state(state, current_user.id, session)
        secret = github_token(code=code)
    except (ValueError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=400, detail="GitHub authorisation failed") from exc
    name, number = "GitHub", 2
    while session.scalar(
        select(GitConnectionRow).where(
            GitConnectionRow.owner_id == current_user.id, GitConnectionRow.name == name
        )
    ):
        name, number = f"GitHub {number}", number + 1
    row = GitConnectionRow(
        owner_id=current_user.id,
        org_id=current_user.personal_org_id,
        name=name,
        kind="github",
        host="github.com",
        encrypted_secret=_encrypt_secret(secret),
    )
    session.add(row)
    session.commit()
    return RedirectResponse(url=f"{config.PUBLIC_URL or ''}/?github_connected=1", status_code=303)


@app.get(
    "/connections/{connection_id}/repositories",
    summary="List GitHub repositories",
    tags=["connections"],
)
def list_github_repositories(
    connection_id: str,
    session: Session = Depends(get_db),
    current_user: UserRow = Depends(get_current_user),
):
    row = _owned_connection(session, connection_id, current_user.id)
    if row is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    if row.kind != "github":
        raise HTTPException(status_code=400, detail="Connection is not GitHub")
    try:
        return {"repositories": github_repositories(row, session)}
    except (RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(
            status_code=409, detail="GitHub connection requires reconnection"
        ) from exc


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
    """List the caller's registered repos."""
    rows = session.scalars(select(RepoRow).where(RepoRow.owner_id == current_user.id)).all()
    interval = _ingest_interval_seconds()
    return {
        "repos": [_repo_to_dict(r) for r in rows],
        "stale_after_seconds": interval * 2 if interval > 0 else None,
    }


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
    """Register a remote repo and queue its initial ingest."""
    from surgite.git import _repo_name_from_url, is_remote_url

    if config.REPO_ADD_GLOBAL_ONLY and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Only admins can add repos")
    if not is_remote_url(req.url):
        raise HTTPException(
            status_code=400,
            detail="Must be a remote git URL (https://, git@, git:// or ssh://)",
        )
    name = _repo_name_from_url(req.url)

    parsed = urlparse(req.url)
    if parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="Repository URLs must not contain credentials")
    existing = session.scalar(
        select(RepoRow).where(RepoRow.clone_url == req.url, RepoRow.owner_id == current_user.id)
    )
    if existing:
        raise HTTPException(status_code=409, detail="Repo already registered")
    if session.scalar(
        select(RepoRow).where(RepoRow.name == name, RepoRow.owner_id == current_user.id)
    ):
        raise HTTPException(status_code=409, detail="A repo with this name already exists")
    if req.connection_id:
        connection = _usable_connection(session, req.connection_id, current_user.id)
        if parsed.scheme != "https" or parsed.netloc.lower() != connection.host:
            raise HTTPException(
                status_code=400, detail="Connection credentials do not match this HTTPS repository"
            )
    repo = RepoRow(
        name=name,
        clone_url=req.url,
        owner_id=current_user.id,
        org_id=current_user.personal_org_id,
        added_at=datetime.now(UTC),
        connection_id=req.connection_id or None,
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
    _submit_ingest(background, request, repo.id, name)
    return _repo_to_dict(repo)


@app.put(
    "/repos/{repo_id}/connection",
    response_model=RepoResponse,
    summary="Set repo connection",
    tags=["repos"],
)
def set_repo_connection(
    repo_id: int,
    req: RepoConnectionUpdate,
    session: Session = Depends(get_db),
    current_user: UserRow = Depends(get_current_user),
):
    repo = _owned_repo(session, repo_id, current_user.id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repo not found")
    if req.connection_id is not None:
        _usable_connection(session, req.connection_id, current_user.id)
    repo.connection_id = req.connection_id
    session.commit()
    session.refresh(repo)
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
    request: Request,
    session: Session = Depends(get_db),
    current_user: UserRow = Depends(get_current_user),
):
    """Queue an ingest for a repository owned by the caller."""
    repo = _owned_repo(session, repo_id, current_user.id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")
    if repo.connection_id:
        _usable_connection(session, repo.connection_id, current_user.id)
    if not _claim_ingest(repo_id):
        raise HTTPException(status_code=409, detail="Ingest already in progress")
    _submit_ingest(background, request, repo_id, repo.name)
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
    """Delete an owned repo and its commits."""
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
    """Save summary parameters behind an owner-scoped slug."""
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
    """Treat SQLite's naive datetimes as UTC."""
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
    """List the caller's unexpired shares."""
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
    """Resolve an unexpired, caller-owned share without leaking other slugs."""
    row = session.get(SharedSummaryRow, slug)
    if (
        row is None
        or _as_utc(row.expires_at) <= datetime.now(UTC)
        or row.owner_id != current_user.id
    ):
        raise HTTPException(status_code=404, detail="Share not found or expired")
    return _share_to_dict(row)


# Mount the production SPA last so it cannot shadow API routes.
_FRONTEND_BUILD = Path(__file__).resolve().parent.parent / "frontend" / "build"


@app.get(
    "/s/{slug}",
    summary="Shared-summary SPA shell",
    tags=["ui"],
    operation_id="share_page",
)
def share_page(slug: str):
    """Serve the SPA shell for a shared-summary deep link."""
    return _serve_spa_shell()


@app.get("/login", summary="Login SPA shell", tags=["ui"], operation_id="login_page")
def login_page():
    """Serve the login SPA shell."""
    return _serve_spa_shell()


@app.get("/signup", summary="Signup SPA shell", tags=["ui"], operation_id="signup_page")
def signup_page():
    """Serve the signup SPA shell."""
    return _serve_spa_shell()


def _serve_spa_shell():
    fallback = _FRONTEND_BUILD / "200.html"
    if fallback.is_file():
        return FileResponse(fallback)
    raise HTTPException(status_code=404, detail="Frontend build not available")


if _FRONTEND_BUILD.is_dir():
    app.mount("/", StaticFiles(directory=_FRONTEND_BUILD, html=True), name="frontend")
