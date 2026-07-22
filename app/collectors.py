import asyncio
from dataclasses import dataclass
from typing import AsyncIterator, Protocol

import httpx

from .config import settings


@dataclass(frozen=True)
class Repository:
    provider: str
    namespace: str
    project_key: str
    project_name: str
    slug: str
    name: str
    web_url: str
    clone_urls: tuple[str, ...]
    default_branch: str = "main"


class RepositoryProvider(Protocol):
    async def repositories(self) -> AsyncIterator[Repository]: ...
    async def file_text(self, repository: Repository, path: str, revision: str | None = None) -> str | None: ...
    async def close(self) -> None: ...


class ApiClient:
    def __init__(self, base_url: str, token: str, *, basic_user: str = ""):
        auth = httpx.BasicAuth(basic_user, token) if basic_user else None
        headers = {"Accept": "application/json"}
        if token and not auth:
            headers["Authorization"] = f"Bearer {token}"
        self.client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), auth=auth, headers=headers,
            verify=settings.verify_tls, timeout=30, follow_redirects=True,
        )

    async def get_json(self, path: str, **params):
        attempts = max(1, settings.api_retry_attempts)
        for attempt in range(attempts):
            response = None
            try:
                response = await self.client.get(path, params=params)
                if response.status_code not in {429, 500, 502, 503, 504}:
                    response.raise_for_status()
                    return response.json()
                response.raise_for_status()
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code not in {429, 500, 502, 503, 504}:
                    raise
                if attempt + 1 >= attempts:
                    raise
                retry_after = response.headers.get("Retry-After") if response is not None else None
                delay = float(retry_after) if retry_after and retry_after.isdigit() else settings.api_retry_backoff_seconds * (2 ** attempt)
                await asyncio.sleep(delay)

    async def close(self):
        await self.client.aclose()


class BitbucketDataCenterCollector(ApiClient):
    async def repositories(self):
        start = 0
        while True:
            page = await self.get_json("/rest/api/1.0/repos", limit=100, start=start)
            for repo in page.get("values", []):
                project = repo["project"]
                links = repo.get("links", {})
                yield Repository(
                    provider="bitbucket_dc", namespace=project["key"],
                    project_key=project["key"], project_name=project["name"],
                    slug=repo["slug"], name=repo["name"],
                    web_url=_first_href(links.get("self")),
                    clone_urls=tuple(x["href"] for x in links.get("clone", [])),
                    default_branch=(repo.get("defaultBranch") or "main").removeprefix("refs/heads/"),
                )
            if page.get("isLastPage", True):
                break
            start = page["nextPageStart"]

    async def file_text(self, repository, path, revision=None):
        at = revision or f"refs/heads/{repository.default_branch}"
        response = await self.client.get(
            f"/rest/api/1.0/projects/{repository.project_key}/repos/{repository.slug}/raw/{path}",
            params={"at": at},
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.text


class BitbucketCloudCollector(ApiClient):
    def __init__(self, workspace: str, token: str, email: str = ""):
        super().__init__("https://api.bitbucket.org/2.0", token, basic_user=email)
        self.workspace = workspace

    async def repositories(self):
        next_url = f"/repositories/{self.workspace}"
        params = {"pagelen": 100, "fields": "+values.project,+values.mainbranch"}
        while next_url:
            page = await self.get_json(next_url, **params)
            params = {}
            for repo in page.get("values", []):
                project = repo.get("project") or {}
                project_key = project.get("key") or "UNASSIGNED"
                project_name = project.get("name") or "Unassigned"
                links = repo.get("links", {})
                yield Repository(
                    provider="bitbucket_cloud", namespace=self.workspace,
                    project_key=project_key, project_name=project_name,
                    slug=repo["slug"], name=repo["name"],
                    web_url=_href(links.get("html")) or _href(links.get("self")),
                    clone_urls=tuple(x["href"] for x in links.get("clone", [])),
                    default_branch=(repo.get("mainbranch") or {}).get("name") or "main",
                )
            next_url = page.get("next")

    async def file_text(self, repository, path, revision=None):
        revision = revision or repository.default_branch
        response = await self.client.get(f"/repositories/{self.workspace}/{repository.slug}/src/{revision}/{path}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.text


class TeamCityCollector(ApiClient):
    async def build_types(self):
        result, start, page_size = [], 0, 100
        while True:
            data = await self.get_json(
                "/app/rest/buildTypes", locator=f"start:{start},count:{page_size}",
                fields="count,nextHref,buildType(id,name,projectId,webUrl,href)",
            )
            page = data.get("buildType", [])
            result.extend(page)
            if not data.get("nextHref") and len(page) < page_size:
                break
            start += len(page)
            if not page:
                break
        return result

    async def build_type(self, build_type_id: str):
        fields = "id,name,projectId,webUrl,settings(property(name,value)),steps(step(id,name,type,properties(property(name,value)))),vcs-root-entries(vcs-root-entry(vcs-root(id,name,properties(property(name,value))))),snapshot-dependencies,artifact-dependencies"
        return await self.get_json(f"/app/rest/buildTypes/id:{build_type_id}", fields=fields)

    async def builds(self, build_type_id: str):
        data = await self.get_json("/app/rest/builds", locator=f"buildType:{build_type_id},state:finished,count:5", fields="build(id,number,status,state,statusText,startDate,finishDate,webUrl)")
        return data.get("build", [])

    async def artifacts(self, build_id: str):
        files = []

        async def visit(path: str, prefix: str = ""):
            data = await self.get_json(path)
            for item in data.get("file", []):
                name = item.get("name", "")
                full_name = f"{prefix}/{name}".strip("/")
                if child := item.get("children", {}).get("href"):
                    await visit(child, full_name)
                elif content := item.get("content", {}).get("href"):
                    files.append({
                        "name": name,
                        "path": full_name,
                        "size": item.get("size"),
                        "url": f"{settings.teamcity_public_url.rstrip('/')}{content}",
                    })

        await visit(f"/app/rest/builds/id:{build_id}/artifacts/children")
        return files


def repository_provider() -> RepositoryProvider:
    if settings.bitbucket_provider == "cloud":
        if not settings.bitbucket_workspace:
            raise ValueError("BITBUCKET_WORKSPACE is required for cloud provider")
        email = settings.bitbucket_email if settings.bitbucket_auth == "api_token" else ""
        if settings.bitbucket_auth == "api_token" and not email:
            raise ValueError("BITBUCKET_EMAIL is required for Cloud API token authentication")
        return BitbucketCloudCollector(settings.bitbucket_workspace, settings.bitbucket_token, email)
    if settings.bitbucket_provider == "datacenter":
        return BitbucketDataCenterCollector(settings.bitbucket_url, settings.bitbucket_token)
    raise ValueError(f"Unsupported BITBUCKET_PROVIDER: {settings.bitbucket_provider}")


def teamcity_properties(container: dict, key: str = "properties") -> dict[str, str]:
    return {item["name"]: item.get("value", "") for item in container.get(key, {}).get("property", [])}


def _href(value):
    return value.get("href", "") if isinstance(value, dict) else ""


def _first_href(value):
    return value[0].get("href", "") if isinstance(value, list) and value else _href(value)
