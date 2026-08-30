import httpx

from app import diagnostics


def test_http_failure_reports_provider_status_path_and_retries_without_query(monkeypatch):
    monkeypatch.setattr(diagnostics.settings, "api_retry_attempts", 4)
    request = httpx.Request("GET", "http://teamcity:8111/app/rest/buildTypes?token=secret")
    response = httpx.Response(503, request=request)
    details = diagnostics.upstream_failure(httpx.HTTPStatusError("failed", request=request, response=response))
    assert details == {"errorType": "HTTPStatusError", "provider": "teamcity", "retries": 3, "httpStatus": 503, "method": "GET", "endpoint": "/app/rest/buildTypes"}
    assert "secret" not in diagnostics.upstream_message(details)


def test_non_retryable_bitbucket_error_has_no_retries():
    request = httpx.Request("GET", "https://api.bitbucket.org/2.0/repositories/acme")
    response = httpx.Response(401, request=request)
    details = diagnostics.upstream_failure(httpx.HTTPStatusError("failed", request=request, response=response))
    assert details["provider"] == "bitbucket"
    assert details["retries"] == 0
    assert diagnostics.upstream_message(details) == "bitbucket HTTP 401 GET /2.0/repositories/acme"
