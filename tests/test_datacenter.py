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
        if path.endswith("/files"):
            return httpx.Response(200, json={"values": ["ci/build.sh"], "isLastPage": True})
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
        if request.url.path.endswith("/files"):
            return httpx.Response(200, json={"values": ["pom.xml"], "isLastPage": True})
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


@pytest.mark.asyncio
async def test_file_index_pagination_membership_and_commit_cache():
    requests = []
    def handler(request):
        requests.append(request)
        assert request.url.path.startswith("/bitbucket/rest/api/1.0/projects/PRJ/repos/repo/")
        if request.url.path.endswith("/files"):
            start = int(request.url.params["start"])
            return httpx.Response(200, json={"values": ["lib/log.sh"] if start == 0 else ["ci/build.sh", "other/log.sh"],
                                            "isLastPage": start == 17, "nextPageStart": 17})
        assert request.url.path.endswith("/raw/lib/log.sh")
        return httpx.Response(200, text="echo hello")
    collector = BitbucketDataCenterCollector("https://bb/bitbucket", "reader")
    await collector.client.aclose()
    collector.client = httpx.AsyncClient(base_url="https://bb/bitbucket", transport=httpx.MockTransport(handler))
    repo = Repository("bitbucket_dc", "PRJ", "PRJ", "Project", "repo", "Repo", "", (), revision="a" * 40)
    try:
        assert await collector.file_text(repo, "../lib/log.sh") is None
        assert await collector.file_text(repo, "log.sh") is None
        assert await collector.file_text(repo, "missing.sh") is None
        assert await collector.file_text(repo, "lib/log.sh") == "echo hello"
        assert await collector.file_text(repo, "lib/log.sh") == "echo hello"
        assert len(requests) == 3
        assert all(r.url.params["at"] == "a" * 40 for r in requests)
        assert await collector.file_text(repo, "lib/log.sh", revision="b" * 40) == "echo hello"
        assert len(requests) == 6
        assert all(r.url.params["at"] == "b" * 40 for r in requests[3:])
    finally:
        await collector.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("page", [
    {"values": ["../lib/log.sh"], "isLastPage": True},
    {"values": ["/lib/log.sh"], "isLastPage": True},
    {"values": ["lib/./log.sh"], "isLastPage": True},
    {"values": ["lib/\nlog.sh"], "isLastPage": True},
    {"values": [], "isLastPage": False, "nextPageStart": 0},
    {"values": "not a list", "isLastPage": True},
    {"values": []},
])
async def test_invalid_file_index_is_not_cached_or_used_for_raw_requests(page):
    collector = BitbucketDataCenterCollector("https://bb", "reader")
    await collector.client.aclose()
    def handler(request):
        assert request.url.path.endswith("/files")
        return httpx.Response(200, json=page)
    collector.client = httpx.AsyncClient(base_url="https://bb", transport=httpx.MockTransport(handler))
    repo = Repository("bitbucket_dc", "PRJ", "PRJ", "Project", "repo", "Repo", "", (), revision="a" * 40)
    try:
        with pytest.raises(httpx.DecodingError):
            await collector.file_text(repo, "pom.xml")
        assert not collector.file_indexes
    finally:
        await collector.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404])
async def test_index_access_failure_has_no_unchecked_raw_fallback(status):
    collector = BitbucketDataCenterCollector("https://bb", "reader")
    await collector.client.aclose()
    def handler(request):
        assert request.url.path.endswith("/files")
        return httpx.Response(status)
    collector.client = httpx.AsyncClient(base_url="https://bb", transport=httpx.MockTransport(handler))
    repo = Repository("bitbucket_dc", "PRJ", "PRJ", "Project", "repo", "Repo", "", (), revision="a" * 40)
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await collector.file_text(repo, "pom.xml")
        assert not collector.file_indexes
    finally:
        await collector.close()


@pytest.mark.asyncio
async def test_recursive_source_expansion_uses_only_indexed_root_paths():
    from app.source_analysis import expand_scripts
    seen = []
    files = {"ci/build.sh": "bash ../lib/log.sh\nbash ci/../lib/publish.sh\nbash missing.sh",
             "lib/publish.sh": "docker push registry/app:1"}
    def handler(request):
        seen.append(request.url.path)
        assert request.url.params["at"] == "a" * 40
        if request.url.path.endswith("/files"):
            return httpx.Response(200, json={"values": list(files), "isLastPage": True})
        path = request.url.path.split("/raw/", 1)[1]
        assert path in files
        return httpx.Response(200, text=files[path])
    collector = BitbucketDataCenterCollector("https://bb", "reader")
    await collector.client.aclose()
    collector.client = httpx.AsyncClient(base_url="https://bb", transport=httpx.MockTransport(handler))
    repo = Repository("bitbucket_dc", "PRJ", "PRJ", "Project", "repo", "Repo", "", (), revision="a" * 40)
    try:
        result = await expand_scripts(collector, [repo], ["bash ci/build.sh"])
        assert [s.path for s in result] == ["teamcity:inline", "PRJ/repo/ci/build.sh", "PRJ/repo/lib/publish.sh"]
        assert len(seen) == 3
        with pytest.raises(ValueError, match="immutable commit"):
            await collector.file_text(replace(repo, revision="main"), "ci/build.sh")
        assert len(seen) == 3
    finally:
        await collector.close()
