from urllib.parse import urlsplit

import httpx

from .config import settings


def upstream_failure(exc: Exception) -> dict:
    """Return a serializable, credential-free description of an upstream error."""
    result = {"errorType": type(exc).__name__, "provider": "unknown", "retries": 0}
    if isinstance(exc, httpx.HTTPStatusError):
        result["httpStatus"] = exc.response.status_code
        result["retries"] = max(0, settings.api_retry_attempts - 1) if exc.response.status_code in {429, 500, 502, 503, 504} else 0
        _request_details(result, exc.request)
    elif isinstance(exc, httpx.TransportError):
        result["retries"] = max(0, settings.api_retry_attempts - 1)
        if exc.request:
            _request_details(result, exc.request)
    return result


def upstream_message(details: dict) -> str:
    provider = details.get("provider", "unknown")
    status = f" HTTP {details['httpStatus']}" if details.get("httpStatus") else ""
    endpoint = f" {details['method']} {details['endpoint']}" if details.get("endpoint") else ""
    retries = f" after {details['retries']} retries" if details.get("retries") else ""
    return f"{provider}{status}{endpoint}{retries}".strip()


def _request_details(result: dict, request: httpx.Request) -> None:
    parsed = urlsplit(str(request.url))
    result["method"] = request.method
    result["endpoint"] = parsed.path or "/"
    host = (parsed.hostname or "").lower()
    if "bitbucket" in host:
        result["provider"] = "bitbucket"
    elif "teamcity" in host or parsed.port == 8111:
        result["provider"] = "teamcity"
    elif "registry" in host or parsed.port == 5000:
        result["provider"] = "registry"
