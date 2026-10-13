from datetime import datetime, timedelta, timezone

import httpx
import pytest
from urllib.parse import urlencode
from pydantic import ValidationError

from app.collectors import TeamCityCollector
from app.config import Settings, settings


@pytest.mark.parametrize("days", [0, -1, "bad"])
def test_invalid_lookback(days):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, teamcity_build_lookback_days=days)


def test_default_window():
    config = Settings(_env_file=None)
    assert config.teamcity_build_lookback_days == 90
    assert config.teamcity_build_limit == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("days", [90, 14])
async def test_window_and_pagination_for_history_and_queue(monkeypatch, days):
    monkeypatch.setattr(settings, "teamcity_build_lookback_days", days)
    monkeypatch.setattr(settings, "teamcity_build_limit", 0)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    requests = []
    def handler(request):
        locator = request.url.params["locator"]
        requests.append(locator)
        queue = request.url.path.endswith("buildQueue")
        raw = locator.split("date:" if queue else "sinceDate:")[1].split(",")[0]
        actual = datetime.strptime(raw, "%Y%m%dT%H%M%S%z")
        assert abs((actual - cutoff).total_seconds()) < 5
        assert "count:100" in locator
        second = "start:1," in locator
        if second:
            assert "lookupLimit:10000" in locator
        return httpx.Response(200, json={
            "build": [{"id": (3 if queue else 1) + int(second)}],
            **({} if second else {"nextHref": request.url.path + "?" + urlencode({"locator": locator.replace("start:0,", "start:1,") + ",lookupLimit:10000", "fields": request.url.params["fields"]})}),
        })
    collector = TeamCityCollector("https://tc/context", "secret")
    await collector.client.aclose()
    collector.client = httpx.AsyncClient(base_url="https://tc/context", transport=httpx.MockTransport(handler))
    try:
        assert [b["id"] for b in await collector.builds("Cfg")] == [3, 4, 1, 2]
        assert len(requests) == 4
    finally:
        await collector.close()
