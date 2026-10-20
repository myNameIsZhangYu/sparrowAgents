"""initial schema

Revision ID: 0001
Revises: None
Create Date: 2026-10-16
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 初始 schema 只包含结构化记忆与会话；任务表由运行时 bootstrap 补齐。
    op.create_table(
        "memories",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("key", sa.Text, nullable=False, unique=True),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("category", sa.Text, nullable=False, server_default="fact"),
        sa.Column("created_at", sa.Text, nullable=False, server_default=sa.func.strftime("%Y-%m-%dT%H:%M:%fZ", "now")),
        sa.Column("updated_at", sa.Text, nullable=False, server_default=sa.func.strftime("%Y-%m-%dT%H:%M:%fZ", "now")),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("session_id", sa.Text, nullable=False, unique=True),
        sa.Column("started_at", sa.Text, nullable=False, server_default=sa.func.strftime("%Y-%m-%dT%H:%M:%fZ", "now")),
        sa.Column("ended_at", sa.Text),
        sa.Column("total_tokens", sa.Integer, server_default="0"),
    )
    op.create_index("idx_memories_category", "memories", ["category"])


def downgrade() -> None:
    # downgrade 会删除数据，仅用于本地清理。
    op.drop_table("sessions")
    op.drop_table("memories")
