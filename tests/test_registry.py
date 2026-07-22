import httpx
import pytest

from app.config import settings
from app.registry import RegistryCollector, parse_image_reference
from app.service import registry_manifest


@pytest.mark.parametrize(("image", "expected"), [
    ("registry:5000/team/api:1.2", ("registry:5000", "team/api", "1.2")),
    ("registry:5000/team/api@sha256:abc", ("registry:5000", "team/api", "sha256:abc")),
    ("team/api", ("registry:5000", "team/api", "latest")),
])
def test_parse_oci_image_reference(image, expected):
    result = parse_image_reference(image, "registry:5000")
    assert (result.registry, result.repository, result.reference) == expected


@pytest.mark.asyncio
async def test_registry_reads_manifest_digest_and_builds_public_url(monkeypatch):
    monkeypatch.setattr(settings, "registry_url", "http://registry:5000")
    monkeypatch.setattr(settings, "registry_public_url", "https://registry.example")
    monkeypatch.setattr(settings, "registry_provider", "nexus")
    collector = RegistryCollector()
    await collector.client.aclose()
    collector.client = httpx.AsyncClient(
        base_url="http://registry:5000",
        transport=httpx.MockTransport(lambda request: httpx.Response(
            200, content=b'{"schemaVersion":2}', headers={
                "Docker-Content-Digest": "sha256:manifest", "Content-Type": "application/vnd.oci.image.manifest.v1+json",
            },
        )),
    )
    try:
        result = await collector.manifest("registry:5000/team/api:1")
    finally:
        await collector.close()
    assert result == {
        "registryProvider": "nexus", "repository": "team/api", "reference": "1",
        "digest": "sha256:manifest", "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "manifestUrl": "https://registry.example/v2/team/api/manifests/1",
    }


@pytest.mark.asyncio
async def test_external_registry_is_not_queried(monkeypatch):
    monkeypatch.setattr(settings, "registry_url", "http://registry:5000")
    collector = RegistryCollector()
    try:
        assert await collector.manifest("quay.io/team/api:1") is None
    finally:
        await collector.close()


@pytest.mark.asyncio
async def test_registry_failure_is_recorded_without_failing_graph_scan():
    class UnavailableRegistry:
        async def manifest(self, _):
            request = httpx.Request("GET", "http://registry/v2/x/manifests/1")
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("missing", request=request, response=response)

    assert await registry_manifest(UnavailableRegistry(), "registry/x:1") == {
        "registryStatus": "unavailable", "registryStatusCode": 404,
    }
