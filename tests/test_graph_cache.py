from app.graph_cache import GraphCache


def test_results_are_isolated_bounded_and_invalidated():
    cache = GraphCache(max_entries=2)
    calls = []
    @cache.cached
    def read(key):
        calls.append(key)
        return {"nodes": [key]}
    read(1)["nodes"].append("mutation")
    assert read(1) == {"nodes": [1]}
    read(2); read(3); read(1)
    assert calls == [1, 2, 3, 1]
    cache.invalidate()
    read(1)
    assert calls == [1, 2, 3, 1, 1]


def test_expiration_size_and_invalidation_during_query():
    cache = GraphCache(max_bytes=10)
    calls = []
    @cache.cached
    def large():
        calls.append(1)
        return "x" * 100
    large(); large()
    assert len(calls) == 2 and cache.size == 0
    cache = GraphCache(ttl=0)
    @cache.cached
    def read():
        calls.append(1)
        return 1
    read(); read()
    assert len(calls) == 4
    @cache.cached
    def racing_read():
        cache.invalidate()
        return 1
    racing_read()
    assert not cache.items
