"""add repository ingest outcomes

Revision ID: h2c3d4e5f6a7
Revises: b9d4f1a7c2e8
Create Date: 2026-09-08
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "h2c3d4e5f6a7"
down_revision: str | Sequence[str] | None = "b9d4f1a7c2e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "repos", sa.Column("last_ingest_attempt_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("repos", sa.Column("last_ingest_error", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("repos", "last_ingest_error")
    op.drop_column("repos", "last_ingest_attempt_at")
