from dataclasses import replace

import httpx
import pytest

from app.collectors import BitbucketDataCenterCollector, Repository


@pytest.mark.asyncio
async def test_selected_projects_only_paginate_their_own_repositories():
    seen = []
    def handler(request):
        path = request.url.path
        seen.append(path)
        assert path.startswith("/bitbucket/rest/api/1.0/projects/")
        key = path.split("/")[6]
        assert key in {"ONE", "TWO"}
        if path.endswith("/repos"):
            start = int(request.url.params["start"])
            return httpx.Response(200, json={"values": [
                {"project": {"key": key, "name": key}, "slug": f"repo{start}", "name": "Repo"},
                {"project": {"key": "EXCLUDED", "name": "No"}, "slug": "no", "name": "No"},
            ], "isLastPage": start == 7, "nextPageStart": 7})
        if path.endswith("/default-branch"):
            return httpx.Response(200, json={"id": "refs/heads/main"})
        return httpx.Response(200, json={"values": [{"id": "refs/heads/main", "latestCommit": "a" * 40}]})
    collector = BitbucketDataCenterCollector("https://bb/bitbucket", "reader", ("ONE", "TWO", "ONE"))
    await collector.client.aclose()
    collector.client = httpx.AsyncClient(base_url="https://bb/bitbucket", transport=httpx.MockTransport(handler))
    try:
        repos = [repo async for repo in collector.repositories()]
        assert [(repo.project_key, repo.slug) for repo in repos] == [
            ("ONE", "repo0"), ("ONE", "repo7"), ("TWO", "repo0"), ("TWO", "repo7")]
        assert sum(path.endswith("/repos") for path in seen) == 4
    finally:
        await collector.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 404])
async def test_selected_project_failure_never_falls_back_to_all(status):
    collector = BitbucketDataCenterCollector("https://bb", "reader", ("MISSING",))
    await collector.client.aclose()
    paths = []
    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(status)
    collector.client = httpx.AsyncClient(base_url="https://bb", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(httpx.HTTPStatusError):
            _ = [repo async for repo in collector.repositories()]
        assert paths == ["/rest/api/1.0/projects/MISSING/repos"]
    finally:
        await collector.close()


def test_project_settings_reach_provider(monkeypatch):
    from app.config import settings
    from app.collectors import repository_provider
    monkeypatch.setattr(settings, "bitbucket_provider", "datacenter")
    monkeypatch.setattr(settings, "bitbucket_project_keys", " ONE, TWO, ONE , ,")
    monkeypatch.setattr("app.collectors.BitbucketDataCenterCollector", lambda url, token, keys: keys)
    assert repository_provider() == ("ONE", "TWO")


@pytest.mark.asyncio
async def test_dc_pagination_context_exact_branch_and_immutable_file():
    seen = []
    def handler(request):
        seen.append(request)
        assert request.url.path.startswith("/bitbucket/rest/api/1.0/")
        assert request.headers["Authorization"] == "Bearer reader"
        path = request.url.path
        if path == "/bitbucket/rest/api/1.0/repos":
            start = int(request.url.params["start"])
            slug = "first" if start == 0 else "second"
            return httpx.Response(200, json={"values": [{"project": {"key": "PRJ", "name": "Project"}, "slug": slug, "name": slug}],
                                            "isLastPage": start == 7, "nextPageStart": 7})
        if path.endswith("/default-branch"):
            return httpx.Response(200, json={"id": "refs/heads/release/stable", "displayId": "release/stable"})
        if path.endswith("/branches"):
            assert request.url.params["filterText"] == "release/stable"
            start = int(request.url.params["start"])
            return httpx.Response(200, json={"values": [{"id": "refs/heads/release/stable-old" if start == 0 else "refs/heads/release/stable", "latestCommit": "wrong" if start == 0 else "a" * 40}],
                                            "isLastPage": start == 3, "nextPageStart": 3})
        assert request.url.params["at"] == "a" * 40
        return httpx.Response(200, text="docker push registry/product:1")
    collector = BitbucketDataCenterCollector("https://bb/bitbucket", "reader")
    await collector.client.aclose()
    collector.client = httpx.AsyncClient(base_url="https://bb/bitbucket", headers={"Authorization": "Bearer reader"}, transport=httpx.MockTransport(handler))
    try:
        repos = [repo async for repo in collector.repositories()]
        assert [repo.slug for repo in repos] == ["first", "second"]
        assert all(repo.default_branch == "release/stable" and repo.revision == "a" * 40 for repo in repos)
        assert "docker push" in await collector.file_text(repos[0], "ci/build.sh")
        assert "docker push" in await collector.file_text(repos[0], "ci/build.sh")
        assert sum("/raw/" in r.url.path for r in seen) == 1
    finally:
        await collector.close()


@pytest.mark.asyncio
async def test_empty_dc_repository_does_not_invent_main_or_fetch_files():
    def handler(request):
        if request.url.path.endswith("/default-branch"):
            return httpx.Response(200, json={"id": "refs/heads/master"})
        if request.url.path.endswith("/branches"):
            return httpx.Response(200, json={"values": [], "isLastPage": True})
        pytest.fail("Empty repository must not request raw files")
    collector = BitbucketDataCenterCollector("https://bb", "reader")
    await collector.client.aclose()
    collector.client = httpx.AsyncClient(base_url="https://bb", transport=httpx.MockTransport(handler))
    try:
        assert await collector.default_revision("PRJ", "empty") == ("master", "")
        repo = Repository("bitbucket_dc", "PRJ", "PRJ", "Project", "empty", "Empty", "", (), "master", source_available=False)
        assert await collector.file_text(repo, "pom.xml") is None
    finally:
        await collector.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404])
async def test_dc_branch_failures_are_not_silently_empty(status):
    collector = BitbucketDataCenterCollector("https://bb", "reader")
    await collector.client.aclose()
    collector.client = httpx.AsyncClient(base_url="https://bb", transport=httpx.MockTransport(lambda _: httpx.Response(status)))
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await collector.default_revision("PRJ", "repo")
    finally:
        await collector.close()


@pytest.mark.asyncio
async def test_dc_file_cache_changes_when_commit_changes():
    revisions = []
    def handler(request):
        revisions.append(request.url.params["at"])
        return httpx.Response(200, text=request.url.params["at"])
    collector = BitbucketDataCenterCollector("https://bb", "reader")
    await collector.client.aclose()
    collector.client = httpx.AsyncClient(base_url="https://bb", transport=httpx.MockTransport(handler))
    repo = Repository("bitbucket_dc", "PRJ", "PRJ", "Project", "repo", "Repo", "", (), revision="a" * 40)
    try:
        assert await collector.file_text(repo, "pom.xml") == "a" * 40
        assert await collector.file_text(replace(repo, revision="b" * 40), "pom.xml") == "b" * 40
        assert revisions == ["a" * 40, "b" * 40]
    finally:
        await collector.close()
