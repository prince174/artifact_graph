import pytest

from app.upstream_urls import public_api_url, resolve_api_url


@pytest.mark.parametrize(("base", "path", "expected"), [
    ("https://bb.example", "/rest/api/1.0/repos", "https://bb.example/rest/api/1.0/repos"),
    ("https://bb.example/bitbucket", "/rest/api/1.0/repos", "https://bb.example/bitbucket/rest/api/1.0/repos"),
    ("https://bb.example/bitbucket/", "rest/api/1.0/repos?start=100", "https://bb.example/bitbucket/rest/api/1.0/repos?start=100"),
    ("https://ci.example/tc", "/app/rest/builds", "https://ci.example/tc/app/rest/builds"),
    ("https://ci.example/tc", "/tc/app/rest/builds", "https://ci.example/tc/app/rest/builds"),
    ("https://ci.example/tc", "tc/app/rest/builds", "https://ci.example/tc/app/rest/builds"),
    ("https://ci.example/tc", "/tc-other/app/rest/builds", "https://ci.example/tc/tc-other/app/rest/builds"),
    ("https://ci.example/tc", "https://ci.example/tc/app/rest/builds?count=3", "https://ci.example/tc/app/rest/builds?count=3"),
    ("https://ci.example/tc", "https://CI.EXAMPLE:443/tc/app/rest/builds", "https://ci.example/tc/app/rest/builds"),
    ("http://ci.example:8111/tc", "/tc/app/rest/builds", "http://ci.example:8111/tc/app/rest/builds"),
    ("https://[::1]:8443/tc", "https://[::1]:8443/tc/app/rest/builds", "https://[::1]:8443/tc/app/rest/builds"),
])
def test_api_url_preserves_context_without_duplicating_href_prefix(base, path, expected):
    assert resolve_api_url(base, path) == expected


@pytest.mark.parametrize("href", [
    "https://other.example/tc/app/rest/builds",
    "http://ci.example/tc/app/rest/builds",
    "https://ci.example:8443/tc/app/rest/builds",
    "https://ci.example:0/tc/app/rest/builds",
    "https://user:password@ci.example/tc/app/rest/builds",
    "https://user@ci.example/tc/app/rest/builds",
    "//ci.example/tc/app/rest/builds",
    "///other.example/app/rest/builds",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "/tc/app/rest/builds#fragment",
    "/tc/app/rest/builds#",
    "/tc/../admin",
    "/tc/%2e%2e/admin",
    "/tc/..%2fadmin",
    "/tc/%5cadmin",
    "/tc/app\n/rest/builds",
    "/tc/app/%0arest/builds",
    " /tc/app/rest/builds",
    "https://ci.example:invalid/app/rest/builds",
    "",
])
def test_api_url_rejects_unsafe_provider_next_or_artifact_href(href):
    with pytest.raises(ValueError):
        resolve_api_url("https://ci.example/tc", href)


@pytest.mark.parametrize("base", [
    "ci.example/tc",
    "ftp://ci.example/tc",
    "https://user:password@ci.example/tc",
    "https://ci.example/tc?token=secret",
    "https://ci.example/tc#fragment",
    "https://ci.example/tc/../other",
])
def test_api_url_rejects_invalid_or_credential_bearing_base(base):
    with pytest.raises(ValueError):
        resolve_api_url(base, "/app/rest/builds")


@pytest.mark.parametrize(("internal", "public", "href", "expected"), [
    ("http://tc:8111", "https://ci.example", "/app/rest/builds/id:1/artifacts/content/sbom.json", "https://ci.example/app/rest/builds/id:1/artifacts/content/sbom.json"),
    ("http://tc:8111/tc", "https://ci.example/teamcity", "/tc/app/rest/builds?id=1", "https://ci.example/teamcity/app/rest/builds?id=1"),
    ("http://tc:8111/tc", "https://ci.example/teamcity/", "/app/rest/builds?id=1", "https://ci.example/teamcity/app/rest/builds?id=1"),
    ("http://tc:8111/tc", "https://ci.example", "http://tc:8111/tc/app/rest/builds", "https://ci.example/app/rest/builds"),
    ("http://tc:8111", "https://ci.example/teamcity", "/app/rest/builds", "https://ci.example/teamcity/app/rest/builds"),
    ("http://tc:8111/tc", "https://ci.example/tc", "/tc/app/rest/builds", "https://ci.example/tc/app/rest/builds"),
])
def test_public_api_url_replaces_internal_context(internal, public, href, expected):
    assert public_api_url(internal, public, href) == expected


@pytest.mark.parametrize("href", [
    "http://other:8111/tc/app/rest/builds",
    "http://tc:8111/other/app/rest/builds",
    "//tc:8111/tc/app/rest/builds",
])
def test_public_api_url_rejects_foreign_origin_or_context(href):
    with pytest.raises(ValueError):
        public_api_url("http://tc:8111/tc", "https://ci.example/teamcity", href)
