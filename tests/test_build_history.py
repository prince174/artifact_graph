from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.build_history import incremental_builds


class Cache:
    value = None
    def get(self, key): return self.value
    def put(self, key, value): self.value = value


@pytest.mark.asyncio
async def test_delta_retains_history_refreshes_active_and_expires_old_builds():
    now = datetime.now(timezone.utc)
    date = lambda days: (now - timedelta(days=days)).strftime("%Y%m%dT%H%M%S%z")
    calls = []
    class Collector:
        client = SimpleNamespace(base_url="http://tc")
        async def _builds_window(self, configuration, since=None):
            calls.append(since)
            return ([dict(id=1, state="finished", startDate=date(2)), dict(id=2, state="running", startDate=date(1))]
                    if since is None else [dict(id=3, state="finished", startDate=date(0))])
        async def _refresh_build(self, build_id):
            assert build_id == 2
            return dict(id=2, state="finished", startDate=date(1))
    collector, cache = Collector(), Cache()
    await incremental_builds(collector, "cfg", cache, 90, 0)
    cache.value["builds"].append(dict(id=0, state="finished", startDate=date(91)))
    result = await incremental_builds(collector, "cfg", cache, 90, 0)
    assert {x["id"] for x in result} == {1, 2, 3}
    assert all(x["state"] == "finished" for x in result)
    assert calls[0] is None and calls[1] > now - timedelta(minutes=6)
    cache.value["fullAt"] -= 8 * 86400
    await incremental_builds(collector, "cfg", cache, 90, 0)
    assert calls[-1] is None


@pytest.mark.asyncio
async def test_failure_does_not_advance_watermark_and_limit_bypasses_cache():
    class Collector:
        client = SimpleNamespace(base_url="http://tc")
        async def _builds_window(self, configuration, since=None):
            raise RuntimeError("upstream offline")
    cache = Cache()
    with pytest.raises(RuntimeError):
        await incremental_builds(Collector(), "cfg", cache, 90, 0)
    assert cache.value is None

    with pytest.raises(RuntimeError):
        await incremental_builds(Collector(), "cfg", cache, 90, 5)
    assert cache.value is None


@pytest.mark.asyncio
async def test_collector_delta_locator_and_removed_queued_build(monkeypatch):
    import httpx
    from app.collectors import TeamCityCollector
    from app.config import settings
    monkeypatch.setattr(settings, "teamcity_build_limit", 0)
    cache, requests = Cache(), []
    date = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%z")
    def handler(request):
        requests.append(request)
        path = request.url.path
        if path.endswith("id:2"):
            return httpx.Response(404)
        if path.endswith("id:1"):
            return httpx.Response(200, json=dict(id=1, state="finished", startDate=date))
        if path.endswith("buildQueue"):
            return httpx.Response(200, json={"build": [] if cache.value else [dict(id=2, state="queued", queuedDate=date)]})
        return httpx.Response(200, json={"build": [dict(id=3 if cache.value else 1, state="finished" if cache.value else "running", startDate=date)]})
    collector = TeamCityCollector("http://tc", "test")
    await collector.client.aclose()
    collector.client = httpx.AsyncClient(base_url="http://tc", transport=httpx.MockTransport(handler))
    collector.history_cache = cache
    try:
        await collector.builds("cfg")
        first_locator = requests[0].url.params["locator"]
        result = await collector.builds("cfg")
        assert {b["id"] for b in result} == {1, 3}
        assert all(b["state"] == "finished" for b in result)
        assert requests[2].url.params["locator"] != first_locator
        assert "buildType:(id:cfg)" in requests[2].url.params["locator"]
    finally:
        await collector.close()
