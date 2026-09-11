"""add owner-scoped Git connections

Revision ID: j3d4e5f6a7b8
Revises: i1c2d3e4f5a6
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "j3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "i1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "git_connections",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "owner_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "org_id", sa.String(), sa.ForeignKey("orgs.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("host", sa.String(), nullable=False),
        sa.Column("encrypted_secret", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("owner_id", "name", name="uq_git_connections_owner_name"),
    )
    op.create_index("ix_git_connections_owner_id", "git_connections", ["owner_id"])
    op.create_table(
        "github_auth_states",
        sa.Column("state_hash", sa.String(), primary_key=True),
        sa.Column(
            "user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_github_auth_states_user_id", "github_auth_states", ["user_id"])
    with op.batch_alter_table("repos") as batch:
        batch.add_column(sa.Column("connection_id", sa.String(), nullable=True))
        batch.create_foreign_key(
            "fk_repos_connection_id",
            "git_connections",
            ["connection_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.drop_constraint("uq_repos_name", type_="unique")
        batch.drop_constraint("uq_repos_clone_url", type_="unique")
        batch.create_unique_constraint("uq_repos_owner_name", ["owner_id", "name"])
        batch.create_unique_constraint("uq_repos_owner_clone_url", ["owner_id", "clone_url"])
    op.create_index("ix_repos_connection_id", "repos", ["connection_id"])


def downgrade() -> None:
    op.drop_index("ix_repos_connection_id", table_name="repos")
    with op.batch_alter_table("repos") as batch:
        batch.drop_constraint("uq_repos_owner_clone_url", type_="unique")
        batch.drop_constraint("uq_repos_owner_name", type_="unique")
        batch.create_unique_constraint("uq_repos_clone_url", ["clone_url"])
        batch.create_unique_constraint("uq_repos_name", ["name"])
        batch.drop_constraint("fk_repos_connection_id", type_="foreignkey")
        batch.drop_column("connection_id")
    op.drop_index("ix_github_auth_states_user_id", table_name="github_auth_states")
    op.drop_table("github_auth_states")
    op.drop_index("ix_git_connections_owner_id", table_name="git_connections")
    op.drop_table("git_connections")
