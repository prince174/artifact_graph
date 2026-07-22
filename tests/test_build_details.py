import httpx
import pytest

from app.collectors import TeamCityCollector
from app.config import settings
from app.service import build_node


@pytest.mark.asyncio
async def test_teamcity_artifacts_are_collected_recursively():
    old_public_url = settings.teamcity_public_url
    settings.teamcity_public_url = "http://browser-teamcity"
    def handler(request):
        if request.url.path.endswith("/artifacts/children"):
            return httpx.Response(200, json={"file": [{"name": "artifacts", "children": {"href": "/children/artifacts"}}]})
        if request.url.path == "/children/artifacts":
            return httpx.Response(200, json={"file": [{"name": "build", "children": {"href": "/children/build"}}]})
        return httpx.Response(200, json={"file": [{"name": "sbom.json", "size": 45, "content": {"href": "/content/sbom.json"}}]})

    collector = TeamCityCollector("http://teamcity", "token")
    await collector.client.aclose()
    collector.client = httpx.AsyncClient(base_url="http://teamcity", transport=httpx.MockTransport(handler))
    try:
        assert await collector.artifacts("42") == [{
            "name": "sbom.json", "path": "artifacts/build/sbom.json", "size": 45,
            "url": "http://browser-teamcity/content/sbom.json",
        }]
    finally:
        await collector.close()
        settings.teamcity_public_url = old_public_url


def test_successful_build_has_actual_pushes_and_only_sbom_artifacts():
    build = {"id": 42, "number": "5", "status": "SUCCESS", "webUrl": "http://teamcity/build/42"}
    images = [{"image": "registry/service:1", "engine": "docker"}]
    artifacts = [{"name": "log.txt"}, {"name": "sbom.json", "url": "http://teamcity/sbom"}]
    node = build_node(build, images, artifacts)
    assert node["id"] == "build:42"
    assert node["pushedImages"] == images
    assert node["sbomArtifacts"] == [{**artifacts[1], "relatedImages": ["registry/service:1"]}]


def test_failed_build_does_not_claim_image_was_pushed():
    node = build_node({"id": 7, "status": "FAILURE"}, [{"image": "registry/service:1"}], [{"name": "sbom.json"}])
    assert node["pushedImages"] == []
    assert node["sbomArtifacts"][0]["relatedImages"] == []
