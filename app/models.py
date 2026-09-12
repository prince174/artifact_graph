from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from .config import settings
from .transport_security import validate_database_settings

validate_database_settings()


class Base(DeclarativeBase):
    pass


class Node(Base):
    __tablename__ = "nodes"
    id: Mapped[str] = mapped_column(String(500), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    label: Mapped[str] = mapped_column(String(500), index=True)
    data: Mapped[str] = mapped_column(Text, default="{}")


class Edge(Base):
    __tablename__ = "edges"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(500), index=True)
    target: Mapped[str] = mapped_column(String(500), index=True)
    relation: Mapped[str] = mapped_column(String(60))
    data: Mapped[str] = mapped_column(Text, default="{}")
    __table_args__ = (UniqueConstraint("source", "target", "relation"),)


class Scan(Base):
    __tablename__ = "scans"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="running")
    message: Mapped[str] = mapped_column(Text, default="")
    details: Mapped[str] = mapped_column(Text, default="{}")


class GraphSnapshot(Base):
    __tablename__ = "graph_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("scans.id", ondelete="CASCADE"), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    node_count: Mapped[int] = mapped_column(Integer)
    edge_count: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[str] = mapped_column(Text)


class PersistentCacheEntry(Base):
    __tablename__ = "persistent_cache"
    namespace: Mapped[str] = mapped_column(String(40), primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    accessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_key: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(60), index=True)
    payload: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
    last_status: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)
