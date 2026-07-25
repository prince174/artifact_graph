from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, PersistentCacheEntry
from app.persistent_cache import PersistentCache


def cache_db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def test_persistent_cache_survives_new_cache_instance():
    session = cache_db()
    PersistentCache(session, "build").put(("tc", 12), {"log": "docker push repo:1"})
    restored = PersistentCache(session, "build")
    assert restored.get(("tc", 12)) == {"log": "docker push repo:1"}
    assert restored.hits == 1


def test_persistent_cache_treats_expired_and_corrupt_rows_as_misses():
    session = cache_db()
    cache = PersistentCache(session, "source", ttl_hours=1)
    cache.put("old", ["value"])
    with session.begin() as db:
        row = db.get(PersistentCacheEntry, ("source", cache.key("old")))
        row.created_at = datetime.now(timezone.utc) - timedelta(hours=2)
        db.add(PersistentCacheEntry(namespace="source", cache_key=cache.key("bad"), payload="{bad", created_at=datetime.now(timezone.utc), accessed_at=datetime.now(timezone.utc)))
    assert cache.get("old") is None
    assert cache.get("bad") is None


def test_persistent_cache_enforces_namespace_row_limit():
    session = cache_db()
    cache = PersistentCache(session, "build", max_rows=2)
    for key in ("one", "two", "three"):
        cache.put(key, key)
    with session() as db:
        assert db.query(PersistentCacheEntry).filter_by(namespace="build").count() == 2
