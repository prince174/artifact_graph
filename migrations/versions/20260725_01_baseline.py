"""initial graph, scan and snapshot schema

Revision ID: 20260725_01
"""
from alembic import op
import sqlalchemy as sa

revision = "20260725_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("nodes", sa.Column("id", sa.String(500), primary_key=True), sa.Column("kind", sa.String(40), nullable=False), sa.Column("label", sa.String(500), nullable=False), sa.Column("data", sa.Text(), nullable=False))
    op.create_index("ix_nodes_kind", "nodes", ["kind"])
    op.create_index("ix_nodes_label", "nodes", ["label"])
    op.create_table("edges", sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True), sa.Column("source", sa.String(500), nullable=False), sa.Column("target", sa.String(500), nullable=False), sa.Column("relation", sa.String(60), nullable=False), sa.Column("data", sa.Text(), nullable=False), sa.UniqueConstraint("source", "target", "relation"))
    op.create_index("ix_edges_source", "edges", ["source"])
    op.create_index("ix_edges_target", "edges", ["target"])
    op.create_table("scans", sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True), sa.Column("started_at", sa.DateTime(timezone=True), nullable=False), sa.Column("finished_at", sa.DateTime(timezone=True)), sa.Column("status", sa.String(30), nullable=False), sa.Column("message", sa.Text(), nullable=False))
    op.create_table("graph_snapshots", sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True), sa.Column("scan_id", sa.Integer(), sa.ForeignKey("scans.id", ondelete="CASCADE"), nullable=False, unique=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("node_count", sa.Integer(), nullable=False), sa.Column("edge_count", sa.Integer(), nullable=False), sa.Column("content_hash", sa.String(64), nullable=False), sa.Column("payload", sa.Text(), nullable=False))
    op.create_index("ix_graph_snapshots_scan_id", "graph_snapshots", ["scan_id"], unique=True)
    op.create_index("ix_graph_snapshots_content_hash", "graph_snapshots", ["content_hash"])


def downgrade():
    op.drop_table("graph_snapshots")
    op.drop_table("scans")
    op.drop_table("edges")
    op.drop_table("nodes")
