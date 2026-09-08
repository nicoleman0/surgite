from datetime import datetime

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """The error envelope every route returns on failure. FastAPI's native
    shape — ``HTTPException(detail=...)`` and the CSRF / SQLAlchemy handlers
    in ``surgite.api`` all serialise to ``{"detail": "..."}``. Declared here
    so the OpenAPI snapshot documents the stable error contract (0.6.0
    api-stability policy)."""

    detail: str


class RepoCreate(BaseModel):
    url: str


class RepoResponse(BaseModel):
    id: int
    name: str
    clone_url: str
    added_at: datetime | None
    last_ingested_at: datetime | None
    last_ingest_attempt_at: datetime | None
    last_ingest_error: str | None


class RepoListResponse(BaseModel):
    repos: list[RepoResponse]


class IngestAccepted(BaseModel):
    accepted: bool


class ShareCreate(BaseModel):
    """Parameters of a summary to persist behind a shareable slug. Mirrors the
    /summary query string; all fields optional so a bare 'all repos, last 7
    days' share is valid."""

    repo: str | None = None
    since: str | None = None
    until: str | None = None
    author: str | None = None
    ai: bool = False
    provider: str | None = None
    combined: bool = False


class PromptSettings(BaseModel):
    user_name: str = ""
    user_role: str = ""
    tone: str = "neutral"
    group_count: str = "2-5"
    output_format: str = "markdown"
    custom_instructions: str = ""


class PromptSettingsUpdate(BaseModel):
    user_name: str | None = None
    user_role: str | None = None
    tone: str | None = None
    group_count: str | None = None
    output_format: str | None = None
    custom_instructions: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


class RedeemInviteRequest(BaseModel):
    """Claim an invite and set the new account's password. `email` is required
    only for an open invite (one with no email pinned); for a pinned invite the
    server uses the invite's email and ignores this field."""

    token: str
    password: str
    email: str | None = None
    display_name: str | None = None


class ApiKeyCreate(BaseModel):
    """Mint a new per-user API key. The full key is returned in the response
    exactly once."""

    name: str
    expires_at: datetime | None = None


class InviteCreateRequest(BaseModel):
    """Create a new invite token. `email` is optional — an open invite
    (null email) can be redeemed by any new user; a pinned invite is
    locked to one address. `role` is either ``"user"`` (default) or
    ``"admin"``. The redeem token is returned in the response so the
    admin can copy it out of band."""

    email: str | None = None
    role: str = "user"
    ttl_days: int = 14


class ProviderKeysUpdate(BaseModel):
    """Set (or replace) a per-user provider key. The clear flag revokes the
    existing row; the key field is required otherwise. The raw key is never
    returned by the API."""

    provider: str
    key: str | None = None
    clear: bool = False


class PasswordChange(BaseModel):
    """Self-service password change. The user proves control
    of the current password; on success every other session is revoked
    and the current session is kept."""

    current_password: str
    new_password: str


class PasswordResetRequest(BaseModel):
    """Start the self-serve password reset (0.6.0). The endpoint always
    returns 204, even for an unknown email, so this never reveals whether
    an account exists."""

    email: str


class PasswordResetConfirm(BaseModel):
    """Redeem an admin-minted one-time reset token. 15-minute expiry,
    one-time use; on success all of the user's sessions are revoked
    and the lockout is cleared."""

    token: str
    new_password: str
