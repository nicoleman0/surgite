import os

from dotenv import load_dotenv

load_dotenv()


def _get_database_url() -> str:
    """Construct DATABASE_URL from POSTGRES_* env vars if not directly set."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    host = os.environ.get("POSTGRES_HOST", "db")
    port = os.environ.get("POSTGRES_PORT", "5432")
    user = os.environ.get("POSTGRES_USER", "surgite")
    password = os.environ.get("POSTGRES_PASSWORD", "surgite")
    db = os.environ.get("POSTGRES_DB", "surgite")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


DATABASE_URL = _get_database_url()
API_HOST = os.environ.get("API_HOST", "127.0.0.1")
API_PORT = int(os.environ.get("API_PORT", 8000))
REPO_CACHE_DIR = os.environ.get("REPO_CACHE_DIR", "/var/surgite/repos")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
LOG_FORMAT = os.environ.get("LOG_FORMAT", "")  # "json" enables JSON logs; default is human-readable
# Seconds between automatic background ingests of every registered repo.
# Set to 0 (or any non-positive value) to disable the scheduler — useful
# for one-off test runs where the lifespan can't actually start a task.
INGEST_INTERVAL = int(os.environ.get("INGEST_INTERVAL", "300"))
GIT_TIMEOUT_SECONDS = int(os.environ.get("GIT_TIMEOUT_SECONDS", "120"))
# Lifetime of a shared-summary slug (the /s/<slug> links).
SHARE_TTL_DAYS = int(os.environ.get("SHARE_TTL_DAYS", "7"))
# Summary provider keys (ANTHROPIC_API_KEY / GROQ_API_KEY / DEEPSEEK_API_KEY)
# are read by surgite.summarizer at call time, not here.

# --- Auth (0.5.0) -----------------------------------------------------------
# AUTH_MODE selects how requests resolve to a user (see surgite.auth):
#   off         — anonymous; every request resolves to the bootstrap user.
#                 The backward-compatible 0.4.0 behaviour, and the default.
#   single_user — every request resolves to the bootstrap user, but the auth
#                 machinery (hashing, sessions) exists and is exercised.
#   multi_user  — full session-cookie auth; /login + /logout exposed.
AUTH_MODE = os.environ.get("AUTH_MODE", "off")
# The account that owns all data in off/single_user mode, and the account the
# 0.4.0 → 0.5.0 migration backfills existing rows to. Created on demand.
BOOTSTRAP_OWNER_EMAIL = os.environ.get("BOOTSTRAP_OWNER_EMAIL", "owner@localhost")
# Session lifetime and sliding-refresh threshold (a session within this many
# days of expiry gets a fresh cookie on the next request).
SESSION_TTL_DAYS = int(os.environ.get("SESSION_TTL_DAYS", "14"))
SESSION_REFRESH_THRESHOLD_DAYS = int(os.environ.get("SESSION_REFRESH_THRESHOLD_DAYS", "7"))
# How often the lifespan scheduler purges expired sessions, in seconds.
SESSION_CLEANUP_INTERVAL = int(os.environ.get("SESSION_CLEANUP_INTERVAL", "3600"))
# The session cookie name. The __Host- prefix forces Secure + host-only +
# path=/ at the browser, which is the hardening we want in production. In
# DEBUG mode (plain-HTTP local dev) the prefix and Secure flag are dropped,
# because __Host- cookies are rejected by browsers over http://.
DEBUG = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")
SESSION_COOKIE_NAME = "surgite_session" if DEBUG else "__Host-surgite_session"
# Master key for at-rest provider-key encryption. Generated on first run if
# unset and saved to .secrets_key with chmod 600 (see surgite.secrets); the
# operator is told to back it up. Rotation: scripts/rotate-secrets.sh.
SECRETS_ENCRYPTION_KEY = os.environ.get("SECRETS_ENCRYPTION_KEY", "")
# When true, only admins can POST /repos. Default false so the existing
# "every authenticated user can add a repo" UX is preserved.
REPO_ADD_GLOBAL_ONLY = os.environ.get("REPO_ADD_GLOBAL_ONLY", "").lower() in (
    "1",
    "true",
    "yes",
)
# Lockout policy. 10 fails / 15 min -> 15 min lockout, per (email, ip).
# Tunable so tests can lower the window without waiting.
LOGIN_LOCKOUT_THRESHOLD = int(os.environ.get("LOGIN_LOCKOUT_THRESHOLD", "10"))
LOGIN_LOCKOUT_WINDOW_MINUTES = int(os.environ.get("LOGIN_LOCKOUT_WINDOW_MINUTES", "15"))
LOGIN_LOCKOUT_DURATION_MINUTES = int(os.environ.get("LOGIN_LOCKOUT_DURATION_MINUTES", "15"))
# Per-user rate limits on /summary?ai=true and /summary/stream. The per-IP
# outer guard catches the "fresh signup, spam" case.
SUMMARY_RATE_LIMIT_REQUESTS = int(os.environ.get("SUMMARY_RATE_LIMIT_REQUESTS", "5"))
SUMMARY_RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("SUMMARY_RATE_LIMIT_WINDOW_SECONDS", "60"))
IP_OUTER_RATE_LIMIT_REQUESTS = int(os.environ.get("IP_OUTER_RATE_LIMIT_REQUESTS", "100"))
IP_OUTER_RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("IP_OUTER_RATE_LIMIT_WINDOW_SECONDS", "60"))
# Comma-separated list of trusted proxy IPs / CIDR ranges. When set,
# X-Forwarded-For is only honoured when the direct peer is in this list.
# Default is empty: no proxies trusted, use direct peer address.
_TRUSTED_PROXIES_RAW = os.environ.get("TRUSTED_PROXIES", "")
TRUSTED_PROXIES = [p.strip() for p in _TRUSTED_PROXIES_RAW.split(",") if p.strip()]
# Per-user API key issuance throttle. 10 keys per 24h per user is generous
# for normal use and catches a runaway script.
API_KEY_ISSUE_LIMIT = int(os.environ.get("API_KEY_ISSUE_LIMIT", "10"))
API_KEY_ISSUE_WINDOW_HOURS = int(os.environ.get("API_KEY_ISSUE_WINDOW_HOURS", "24"))

# --- Email (0.6.0) ----------------------------------------------------------
# SMTP for email-delivered password reset. When SMTP_HOST is unset, the app
# uses the LoggingMailer (writes the rendered email to the log stream) so a
# self-hoster who hasn't configured mail still gets working password reset —
# the link lands where they already look for operational signals.
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "surgite <no-reply@localhost>")
# "starttls" (default, port 587), "ssl" (implicit TLS, port 465), or "none".
SMTP_TLS = os.environ.get("SMTP_TLS", "starttls").lower()
# Base URL used to build links in emails (the reset link). Defaults to the
# request's own origin when unset, so a single-host deployment needs no config.
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")

# GitHub App web-flow credentials. Leaving these blank simply hides the
# GitHub connection option; HTTPS token connections remain available.
GITHUB_APP_CLIENT_ID = os.environ.get("GITHUB_APP_CLIENT_ID", "")
GITHUB_APP_CLIENT_SECRET = os.environ.get("GITHUB_APP_CLIENT_SECRET", "")
