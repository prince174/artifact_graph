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
    def handler(request):
        if request.url.path.endswith("/default-branch"):
            return httpx.Response(200, json={"id": "refs/heads/develop"})
        if request.url.path.endswith("/branches"):
            return httpx.Response(200, json={"values": [{"id": "refs/heads/develop", "latestCommit": "abc123"}]})
        return httpx.Response(200, json=payload)
    collector.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://bb")
    repos = [repo async for repo in collector.repositories()]
    await collector.close()
    assert repos[0].slug == "api"
    assert repos[0].provider == "bitbucket_dc"
    assert repos[0].default_branch == "develop"
    assert repos[0].revision == "abc123"


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
    assert normalize_url("https://bb:8443/scm/PRJ/api.git") != normalize_url("https://bb:9443/scm/PRJ/api.git")
    assert normalize_url("https://bb:443/scm/PRJ/api.git") == normalize_url("https://bb/scm/prj/api.git")


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


@pytest.mark.asyncio
async def test_teamcity_requests_paused_configs_and_latest_builds_in_any_state():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "Cfg"} if "/buildTypes/" in request.url.path else {"build": []})

    collector = TeamCityCollector("http://teamcity", "token")
    await collector.client.aclose()
    collector.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://teamcity")
    try:
        await collector.build_type("Cfg")
        await collector.builds("Cfg")
    finally:
        await collector.close()

    assert "paused" in requests[0].url.params["fields"]
    assert "state:any" in requests[1].url.params["locator"]
    assert "count:3" in requests[1].url.params["locator"]
    assert "defaultFilter:false" in requests[1].url.params["locator"]
    assert requests[2].url.path == "/app/rest/buildQueue"


@pytest.mark.asyncio
async def test_teamcity_merges_queue_with_recent_builds_without_duplicates():
    def handler(request):
        if request.url.path.endswith("buildQueue"):
            return httpx.Response(200, json={"build": [{"id": 3, "state": "queued"}]})
        return httpx.Response(200, json={"build": [{"id": 3, "state": "queued"}, {"id": 2, "state": "finished"}, {"id": 1, "state": "finished"}]})

    collector = TeamCityCollector("http://teamcity", "token")
    await collector.client.aclose()
    collector.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://teamcity")
    try:
        builds = await collector.builds("Cfg")
    finally:
        await collector.close()
    assert [build["id"] for build in builds] == [3, 2, 1]


@pytest.mark.asyncio
async def test_teamcity_context_artifact_links_and_configurable_build_limit(monkeypatch):
    requests = []
    monkeypatch.setattr(settings, "teamcity_public_url", "https://public/teamcity")
    monkeypatch.setattr(settings, "teamcity_build_limit", 5)
    def handler(request):
        requests.append(request)
        path = request.url.path
        assert "/tc/tc/" not in path
        if path.endswith("/artifacts/children"):
            return httpx.Response(200, json={"file": [{"name": "artifacts", "children": {"href": "/tc/app/rest/files"}}]})
        if path.endswith("/files"):
            return httpx.Response(200, json={"file": [{"name": "sbom.json", "content": {"href": "/tc/app/rest/content/sbom.json"}}]})
        if path.endswith("/sbom.json"):
            return httpx.Response(200, text='{"bomFormat":"CycloneDX"}')
        if path.endswith("/buildQueue"):
            return httpx.Response(200, json={"build": []})
        return httpx.Response(200, json={"build": [{"id": i} for i in range(6)]})
    collector = TeamCityCollector("https://internal/tc", "reader")
    await collector.client.aclose()
    collector.client = httpx.AsyncClient(base_url="https://internal/tc", transport=httpx.MockTransport(handler))
    try:
        artifacts = await collector.artifacts("1")
        assert artifacts[0]["url"] == "https://public/teamcity/app/rest/content/sbom.json"
        assert (await collector.artifact_content(artifacts[0]["contentHref"]))[0] == '{"bomFormat":"CycloneDX"}'
        assert len(await collector.builds("Cfg")) == 5
        assert "count:5" in requests[-2].url.params["locator"]
    finally:
        await collector.close()


@pytest.mark.asyncio
async def test_api_rejects_foreign_pagination_links_and_redirects():
    sent = []
    def handler(request):
        sent.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://foreign/private"})
    collector = ApiClient("https://trusted/context", "reader")
    await collector.client.aclose()
    collector.client = httpx.AsyncClient(base_url="https://trusted/context", transport=httpx.MockTransport(handler),
                                       follow_redirects=True, event_hooks={"request": [collector.validate_request]})
    try:
        with pytest.raises(ValueError, match="origin"):
            await collector.get_json("https://foreign/private")
        assert not sent
        with pytest.raises(ValueError, match="origin"):
            await collector.get_json("/rest/repos")
        assert sent == ["https://trusted/context/rest/repos"]
    finally:
        await collector.close()
