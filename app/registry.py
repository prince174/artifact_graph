import hashlib
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from .config import settings


MANIFEST_ACCEPT = ", ".join([
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.index.v1+json",
])


@dataclass(frozen=True)
class ImageReference:
    registry: str
    repository: str
    reference: str


def parse_image_reference(image: str, default_registry: str = "") -> ImageReference:
    value = image.removeprefix("docker://")
    digest = ""
    if "@" in value:
        value, digest = value.rsplit("@", 1)
    first, separator, remainder = value.partition("/")
    if separator and ("." in first or ":" in first or first == "localhost"):
        registry, repository = first, remainder
    else:
        registry, repository = default_registry, value
    last = repository.rsplit("/", 1)[-1]
    if digest:
        reference = digest
    elif ":" in last:
        repository, reference = repository.rsplit(":", 1)
    else:
        reference = "latest"
    return ImageReference(registry, repository, reference)


class RegistryCollector:
    def __init__(self):
        auth = httpx.BasicAuth(settings.registry_username, settings.registry_token) if settings.registry_username else None
        self.client = httpx.AsyncClient(
            base_url=settings.registry_url.rstrip("/"), auth=auth, verify=settings.verify_tls,
            timeout=30, follow_redirects=True,
        )
        self.registry_host = urlsplit(settings.registry_url).netloc

    async def manifest(self, image: str) -> dict | None:
        reference = parse_image_reference(image, self.registry_host)
        if reference.registry and reference.registry.lower() != self.registry_host.lower():
            return None
        path = f"/v2/{reference.repository}/manifests/{reference.reference}"
        response = await self.client.get(path, headers={"Accept": MANIFEST_ACCEPT})
        response.raise_for_status()
        digest = response.headers.get("Docker-Content-Digest") or f"sha256:{hashlib.sha256(response.content).hexdigest()}"
        return {
            "registryProvider": settings.registry_provider,
            "repository": reference.repository,
            "reference": reference.reference,
            "digest": digest,
            "mediaType": response.headers.get("Content-Type", "").split(";", 1)[0],
            "manifestUrl": f"{settings.registry_public_url.rstrip('/')}{path}",
        }

    async def close(self):
        await self.client.aclose()
