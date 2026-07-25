"""Safe baseline for current graph schema."""
from alembic import op
from sqlalchemy import inspect
from app.models import Base

revision = "20260725_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    existing = set(inspect(bind).get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in existing:
            table.create(bind)


def downgrade():
    bind = op.get_bind()
    existing = set(inspect(bind).get_table_names())
    for table in reversed(Base.metadata.sorted_tables):
        if table.name in existing:
            table.drop(bind)
