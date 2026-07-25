"""add persistent collector cache

Revision ID: 20260726_02
Revises: 20260725_01
"""
from alembic import op
import sqlalchemy as sa

revision = "20260726_02"
down_revision = "20260725_01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "persistent_cache",
        sa.Column("namespace", sa.String(length=40), nullable=False),
        sa.Column("cache_key", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("namespace", "cache_key"),
    )
    op.create_index("ix_persistent_cache_accessed_at", "persistent_cache", ["accessed_at"])


def downgrade():
    op.drop_index("ix_persistent_cache_accessed_at", table_name="persistent_cache")
    op.drop_table("persistent_cache")
