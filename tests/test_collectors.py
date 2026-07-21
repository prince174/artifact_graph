import httpx
import pytest

from app.collectors import BitbucketCloudCollector, BitbucketDataCenterCollector, Repository, teamcity_properties
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
