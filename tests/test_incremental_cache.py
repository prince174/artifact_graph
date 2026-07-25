from app.incremental import BoundedCache


def test_bounded_cache_tracks_hits_misses_and_evicts_lru():
    cache = BoundedCache(2)
    assert cache.get("missing") is None
    cache.put("a", 1)
    cache.put("b", 2)
    assert cache.get("a") == 1
    cache.put("c", 3)
    assert cache.get("b") is None
    assert cache.get("c") == 3
    assert len(cache) == 2
    assert cache.hits == 2
    assert cache.misses == 2
    cache.clear()
    assert len(cache) == cache.hits == cache.misses == 0
