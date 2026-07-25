import hashlib
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import SQLAlchemyError

from .models import PersistentCacheEntry


class PersistentCache:
    """JSON cache backed by the application database and bounded per namespace."""

    def __init__(self, session_factory, namespace: str, ttl_hours: int = 168, max_rows: int = 5000):
        self.session_factory = session_factory
        self.namespace = namespace
        self.ttl = timedelta(hours=max(1, ttl_hours))
        self.max_rows = max(1, max_rows)
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key(value) -> str:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, value):
        cache_key, now = self.key(value), datetime.now(timezone.utc)
        try:
            with self.session_factory.begin() as db:
                row = db.get(PersistentCacheEntry, (self.namespace, cache_key))
                if not row or _utc(row.created_at) < now - self.ttl:
                    if row:
                        db.delete(row)
                    self.misses += 1
                    return None
                try:
                    result = json.loads(row.payload)
                except (TypeError, json.JSONDecodeError):
                    db.delete(row)
                    self.misses += 1
                    return None
                row.accessed_at = now
                self.hits += 1
                return result
        except SQLAlchemyError:
            self.misses += 1
            return None

    def put(self, value, payload) -> None:
        cache_key, now = self.key(value), datetime.now(timezone.utc)
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        try:
            with self.session_factory.begin() as db:
                row = db.get(PersistentCacheEntry, (self.namespace, cache_key))
                if row:
                    row.payload, row.created_at, row.accessed_at = encoded, now, now
                else:
                    db.add(PersistentCacheEntry(namespace=self.namespace, cache_key=cache_key, payload=encoded, created_at=now, accessed_at=now))
                old = (
                    db.query(PersistentCacheEntry)
                    .filter(PersistentCacheEntry.namespace == self.namespace)
                    .order_by(PersistentCacheEntry.accessed_at.desc())
                    .offset(self.max_rows)
                    .all()
                )
                for item in old:
                    db.delete(item)
        except SQLAlchemyError:
            return


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
