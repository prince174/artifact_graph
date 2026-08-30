"""add webhook outbox

Revision ID: 20260830_04
Revises: 20260830_03
"""
from alembic import op
import sqlalchemy as sa

revision = "20260830_04"
down_revision = "20260830_03"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("webhook_deliveries", sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True), sa.Column("event_key", sa.String(160), nullable=False), sa.Column("event_type", sa.String(60), nullable=False), sa.Column("payload", sa.Text(), nullable=False), sa.Column("status", sa.String(20), nullable=False), sa.Column("attempts", sa.Integer(), nullable=False), sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False), sa.Column("last_status", sa.String(120), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("sent_at", sa.DateTime(timezone=True)), sa.UniqueConstraint("event_key"))
    for column in ("event_key", "event_type", "status", "next_attempt_at"):
        op.create_index(f"ix_webhook_deliveries_{column}", "webhook_deliveries", [column])


def downgrade():
    op.drop_table("webhook_deliveries")
