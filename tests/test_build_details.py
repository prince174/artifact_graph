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
            "url": "http://browser-teamcity/content/sbom.json", "contentHref": "/content/sbom.json",
        }]
    finally:
        await collector.close()
        settings.teamcity_public_url = old_public_url


def test_successful_build_has_actual_pushes_and_only_sbom_artifacts():
    build = {"id": 42, "number": "5", "status": "SUCCESS", "webUrl": "http://teamcity/build/42"}
    images = [{"image": "registry/service:1", "engine": "docker"}]
    artifacts = [{"name": "log.txt"}, {"name": "sbom.json", "url": "http://teamcity/sbom"}]
    log = "docker push registry/service:1\ndigest: sha256:" + "d" * 64
    node = build_node(build, images, artifacts, log)
    assert node["id"] == "build:42"
    assert node["pushedImages"] == [{**images[0], "evidence": "teamcity_build_log", "digest": "sha256:" + "d" * 64}]
    assert node["hasImagePush"] is True
    assert node["hasSbom"] is True
    assert node["sbomArtifacts"] == [{**artifacts[1], "relatedImages": ["sha256:" + "d" * 64]}]


def test_failed_build_does_not_claim_image_was_pushed():
    node = build_node({"id": 7, "status": "FAILURE"}, [{"image": "registry/service:1"}], [{"name": "sbom.json"}])
    assert node["pushedImages"] == []
    assert node["hasImagePush"] is False
    assert node["sbomArtifacts"][0]["relatedImages"] == []


def test_executed_push_preserves_all_source_paths_separately_from_log_evidence():
    images = [{"engine": "docker", "image": "registry/app:1", "evidence": path}
              for path in ["workspace/repo/pom.xml", "workspace/repo/ci/push.sh", "workspace/repo/pom.xml"]]
    log = "docker push registry/app:1\ndigest: sha256:" + "a" * 64
    node = build_node({"id": 8, "status": "SUCCESS"}, images, [], log)
    push = node["pushedImages"][0]
    assert push["evidence"] == "teamcity_build_log"
    assert push["sourcePaths"] == ["workspace/repo/ci/push.sh", "workspace/repo/pom.xml"]
    assert push["digest"] == "sha256:" + "a" * 64


def test_configured_source_without_confirmed_log_is_not_a_push():
    images = [{"engine": "docker", "image": "registry/app:1", "evidence": "repo/pom.xml"}]
    node = build_node({"id": 9, "status": "SUCCESS"}, images, [], "build complete")
    assert not node["hasImagePush"] and node["pushedImages"] == []
