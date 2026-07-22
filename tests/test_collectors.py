import httpx
import pytest

from app.collectors import ApiClient, BitbucketCloudCollector, BitbucketDataCenterCollector, Repository, TeamCityCollector, teamcity_properties
from app.config import settings
from app.service import normalize_url


@pytest.mark.asyncio
async def test_cloud_maps_api_response_to_common_repository():
    payload = {"values": [{"name": "API", "slug": "api", "project": {"key": "DEMO", "name": "Demo"},
        "mainbranch": {"name": "main"}, "links": {"html": {"href": "https://bitbucket.org/acme/api"},
        "clone": [{"name": "https", "href": "https://bitbucket.org/acme/api.git"}]}}]}
    collector = BitbucketCloudCollector("acme", "token", "reader@example.com")
    await collector.client.aclose()
    collector.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)), base_url="https://api.bitbucket.org/2.0")
    repos = [repo async for repo in collector.repositories()]
    await collector.close()
    assert repos == [Repository("bitbucket_cloud", "acme", "DEMO", "Demo", "api", "API", "https://bitbucket.org/acme/api", ("https://bitbucket.org/acme/api.git",), "main")]


@pytest.mark.asyncio
async def test_datacenter_maps_api_response_to_same_contract():
    payload = {"values": [{"name": "API", "slug": "api", "project": {"key": "DEMO", "name": "Demo"},
        "links": {"self": [{"href": "http://bb/projects/DEMO/repos/api"}], "clone": [{"href": "http://bb/scm/demo/api.git"}]}}], "isLastPage": True}
    collector = BitbucketDataCenterCollector("http://bb", "token")
    await collector.client.aclose()
    collector.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)), base_url="http://bb")
    repos = [repo async for repo in collector.repositories()]
    await collector.close()
    assert repos[0].slug == "api"
    assert repos[0].provider == "bitbucket_dc"


@pytest.mark.asyncio
@pytest.mark.parametrize("collector_factory", [
    lambda: BitbucketCloudCollector("acme", "token", "reader@example.com"),
    lambda: BitbucketDataCenterCollector("http://bb", "token"),
])
async def test_repository_files_are_cached_during_scan(collector_factory):
    calls = 0
    collector = collector_factory()

    def handler(_):
        nonlocal calls
        calls += 1
        return httpx.Response(200, text="docker push registry/api:1")

    await collector.client.aclose()
    collector.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://provider")
    repo = Repository("provider", "acme", "DEMO", "Demo", "api", "API", "url", (), "main")
    try:
        assert await collector.file_text(repo, "pom.xml") == "docker push registry/api:1"
        assert await collector.file_text(repo, "pom.xml") == "docker push registry/api:1"
    finally:
        await collector.close()
    assert calls == 1


def test_clone_url_normalization_matches_https_and_ssh():
    expected = "bitbucket.org/acme/api"
    assert normalize_url("https://user@bitbucket.org/acme/api.git") == expected
    assert normalize_url("git@bitbucket.org:acme/api.git") == expected
    assert normalize_url("ssh://git@bitbucket.org/acme/api.git") == expected


def test_reads_teamcity_settings_properties():
    detail = {"settings": {"property": [
        {"name": "artifactRules", "value": "**/sbom.json => artifacts"},
        {"name": "buildNumberCounter", "value": "1"},
    ]}}
    assert teamcity_properties(detail, "settings") == {
        "artifactRules": "**/sbom.json => artifacts",
        "buildNumberCounter": "1",
    }


@pytest.mark.asyncio
async def test_api_retries_transient_status_with_backoff(monkeypatch):
    calls = 0
    sleeps = []

    def handler(_):
        nonlocal calls
        calls += 1
        return httpx.Response(503 if calls == 1 else 200, json={"ok": True})

    monkeypatch.setattr(settings, "api_retry_attempts", 3)
    monkeypatch.setattr(settings, "api_retry_backoff_seconds", 0.25)
    monkeypatch.setattr("app.collectors.asyncio.sleep", lambda delay: _record_sleep(sleeps, delay))
    collector = ApiClient("http://service", "token")
    await collector.client.aclose()
    collector.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://service")
    try:
        assert await collector.get_json("/data") == {"ok": True}
    finally:
        await collector.close()
    assert calls == 2
    assert sleeps == [0.25]


@pytest.mark.asyncio
async def test_api_does_not_retry_permanent_client_error(monkeypatch):
    calls = 0

    def handler(_):
        nonlocal calls
        calls += 1
        return httpx.Response(404, json={"error": "missing"})

    monkeypatch.setattr(settings, "api_retry_attempts", 4)
    collector = ApiClient("http://service", "token")
    await collector.client.aclose()
    collector.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://service")
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await collector.get_json("/missing")
    finally:
        await collector.close()
    assert calls == 1


async def _record_sleep(sleeps, delay):
    sleeps.append(delay)


@pytest.mark.asyncio
async def test_teamcity_build_types_are_paginated():
    starts = []

    def handler(request):
        start = int(request.url.params["locator"].split(",")[0].split(":")[1])
        starts.append(start)
        size = 100 if start == 0 else 1
        return httpx.Response(200, json={
            "buildType": [{"id": f"Build_{start + i}"} for i in range(size)],
            **({"nextHref": "/next"} if start == 0 else {}),
        })

    collector = TeamCityCollector("http://teamcity", "token")
    await collector.client.aclose()
    collector.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://teamcity")
    try:
        result = await collector.build_types()
    finally:
        await collector.close()
    assert len(result) == 101
    assert starts == [0, 100]
