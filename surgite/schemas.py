from datetime import datetime

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """The shared API error envelope."""

    detail: str


class RepoCreate(BaseModel):
    url: str
    connection_id: str | None = None


class RepoResponse(BaseModel):
    id: int
    name: str
    clone_url: str
    added_at: datetime | None
    last_ingested_at: datetime | None
    last_ingest_attempt_at: datetime | None
    last_ingest_error: str | None
    connection_id: str | None


class RepoConnectionUpdate(BaseModel):
    connection_id: str | None = None


class GitConnectionCreate(BaseModel):
    name: str
    origin: str
    username: str
    token: str


class GitConnectionResponse(BaseModel):
    id: str
    name: str
    kind: str
    host: str
    status: str
    created_at: datetime | None
    updated_at: datetime | None
    affected_repositories: int


class RepoListResponse(BaseModel):
    repos: list[RepoResponse]
    stale_after_seconds: int | None


class IngestAccepted(BaseModel):
    accepted: bool


class ShareCreate(BaseModel):
    """Summary parameters saved behind a shareable slug."""

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
    """Invite-redemption fields; open invites also require an email."""

    token: str
    password: str
    email: str | None = None
    display_name: str | None = None


class ApiKeyCreate(BaseModel):
    """API key creation fields."""

    name: str
    expires_at: datetime | None = None


class InviteCreateRequest(BaseModel):
    """Invite creation fields."""

    email: str | None = None
    role: str = "user"
    ttl_days: int = 14


class ProviderKeyStatus(BaseModel):
    """Provider key metadata without key material."""

    provider: str
    created_at: str | None = None
    revoked_at: str | None = None


class ProviderKeysResponse(BaseModel):
    """Visible providers and the caller's key metadata."""

    providers: list[str]
    default: str
    keys: list[ProviderKeyStatus]


class ProviderKeysUpdate(BaseModel):
    """Set, replace, or revoke a provider key."""

    provider: str
    key: str | None = None
    clear: bool = False


class PasswordChange(BaseModel):
    """Self-service password change fields."""

    current_password: str
    new_password: str


class PasswordResetRequest(BaseModel):
    """Password reset request fields."""

    email: str


class PasswordResetConfirm(BaseModel):
    """Password reset confirmation fields."""

    token: str
    new_password: str
