import asyncio
import logging

import httpx
import pytest

from app import dry_run
from app.collectors import BitbucketDataCenterCollector, Repository
from app.config import settings
from app.logging_setup import SafeMessages


@pytest.fixture
def scope(monkeypatch):
    monkeypatch.setattr(settings, "bitbucket_provider", "datacenter")
    monkeypatch.setattr(settings, "bitbucket_project_keys", "INFSEC")


@pytest.mark.asyncio
async def test_wrong_or_missing_effective_scope_fails_before_network(scope, monkeypatch):
    monkeypatch.setattr(settings, "bitbucket_project_keys", "")
    monkeypatch.setattr(dry_run, "repository_provider", lambda: pytest.fail("must not access network"))
    assert await dry_run.traverse("INFSEC", 100) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("unsafe,limit,expected", [(False, 100, 0), (True, 100, 1), (False, 1, 1)])
async def test_dry_run_real_collector_no_sql_no_foreign_project_no_traversal(scope, monkeypatch, caplog, unsafe, limit, expected):
    import sqlalchemy
    monkeypatch.setattr(sqlalchemy, "create_engine", lambda *a, **k: pytest.fail("dry-run must not use database"))
    caplog.set_level(logging.DEBUG, logger="app")
    seen = []
    def handler(request):
        seen.append(request.url.path)
        assert request.method == "GET"
        assert "/AILAB/" not in request.url.path
        assert "/../" not in request.url.path
        if request.url.path.endswith("/repos"):
            return httpx.Response(200, json={"isLastPage": True, "values": [
                {"project": {"key": "AILAB", "name": "Other"}, "slug": "excluded", "name": "No"},
                {"project": {"key": "INFSEC", "name": "Security"}, "slug": "agent", "name": "Agent"}]})
        if request.url.path.endswith("/default-branch"):
            return httpx.Response(200, json={"id": "refs/heads/main"})
        if request.url.path.endswith("/branches"):
            return httpx.Response(200, json={"isLastPage": True, "values": [{"id": "refs/heads/main", "latestCommit": "a" * 40}]})
        assert request.url.params["at"] == "a" * 40
        if request.url.path.endswith("/files"):
            return httpx.Response(200, json={"isLastPage": True, "values": ["ci/build.sh", "lib/log.sh"]})
        return httpx.Response(200, text="bash ../lib/log.sh" if unsafe else "echo ok")
    collector = BitbucketDataCenterCollector("https://bb/bitbucket", "secret", ("INFSEC",))
    await collector.client.aclose()
    collector.client = httpx.AsyncClient(base_url="https://bb/bitbucket", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(dry_run, "repository_provider", lambda: collector)
    assert await dry_run.traverse("INFSEC", limit) == expected
    assert collector.client.is_closed
    assert seen[0] == "/bitbucket/rest/api/1.0/projects/INFSEC/repos"
    assert "bitbucket_repository_start" in caplog.text
    assert "bitbucket_repository_ready" in caplog.text
    assert "INFSEC" in caplog.text
    assert "secret" not in caplog.text
    if unsafe:
        assert "unresolved=2" in caplog.text
    if limit == 1:
        assert not any("/raw/" in path for path in seen)


def test_log_filter_redacts_credentials_and_exception_text(monkeypatch):
    monkeypatch.setattr(settings, "bitbucket_token", "sensitive-token")
    record = logging.LogRecord("app.collectors", logging.DEBUG, "", 0, "path=%s", ("sensitive-token",), None)
    record.exc_text = "private traceback"
    assert SafeMessages().filter(record)
    assert record.getMessage() == "path=[REDACTED]"
    assert record.exc_text is None


def test_fingerprint_is_repeatable():
    assert dry_run.fingerprint() == dry_run.fingerprint()
    assert len(dry_run.fingerprint()) == 16


@pytest.mark.asyncio
async def test_debug_requests_omit_query_and_credentials(scope, caplog):
    caplog.set_level(logging.DEBUG, logger="app")
    collector = BitbucketDataCenterCollector("https://bb", "never-print-this-token")
    try:
        await collector.validate_request(httpx.Request("GET", "https://bb/rest/api/1.0/repos?token=never-print-this-token",
                                                     headers={"Authorization": "Bearer never-print-this-token"}))
        assert "api_request" in caplog.text
        assert "never-print-this-token" not in caplog.text
        with pytest.raises(ValueError):
            collector.url("/rest/api/1.0/raw/%2e%2e/lib/log.sh")
        assert "api_url_rejected" in caplog.text
    finally:
        await collector.close()


def test_timeout_is_failure_without_database_or_startup(scope, monkeypatch):
    from app import transport_security, logging_setup
    monkeypatch.setattr(transport_security, "validate_runtime_settings", lambda: None)
    monkeypatch.setattr(logging_setup, "configure_logging", lambda *a: None)
    async def wait(*args):
        await asyncio.sleep(5)
    monkeypatch.setattr(dry_run, "traverse", wait)
    assert dry_run.run(.01, "INFSEC", 100) == 1
