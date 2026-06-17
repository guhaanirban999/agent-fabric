"""initial agent_entries table

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_entries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False, index=True),
        sa.Column("version", sa.String(64), nullable=False, server_default="0.0.0"),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("domain", sa.String(128), nullable=False, server_default="default", index=True),
        sa.Column("tags", JSONB(), nullable=False, server_default="[]"),
        sa.Column("endpoint_url", sa.Text(), nullable=False),
        sa.Column("transport", sa.String(32), nullable=False),
        sa.Column("auth", JSONB(), nullable=False, server_default="{}"),
        sa.Column("agent_card", JSONB(), nullable=False, server_default="{}"),
        sa.Column("skills", JSONB(), nullable=False, server_default="[]"),
        sa.Column("card_jws", sa.Text(), nullable=True),
        sa.Column("trusted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("health", sa.String(16), nullable=False, server_default="unknown", index=True),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True), nullable=True),
        sa.Column("registered_by", sa.String(255), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("agent_entries")
