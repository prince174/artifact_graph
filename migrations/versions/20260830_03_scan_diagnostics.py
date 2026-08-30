"""add scan diagnostics

Revision ID: 20260830_03
Revises: 20260726_02
"""
from alembic import op
import sqlalchemy as sa

revision = "20260830_03"
down_revision = "20260726_02"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("scans", sa.Column("details", sa.Text(), nullable=False, server_default="{}"))


def downgrade():
    op.drop_column("scans", "details")
