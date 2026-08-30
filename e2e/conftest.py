import os

import pytest


@pytest.fixture(scope="session")
def base_url() -> str:
    return os.environ.get("E2E_BASE_URL", "http://localhost:18082").rstrip("/")


@pytest.fixture(scope="session")
def credentials() -> tuple[str, str]:
    return (
        os.environ.get("E2E_WEB_USERNAME", "e2e-root"),
        os.environ.get("E2E_WEB_PASSWORD", "e2e-only-password-32-characters"),
    )


@pytest.fixture(autouse=True)
def browser_issues(page):
    issues: list[str] = []
    page.on("pageerror", lambda error: issues.append(f"page error: {error}"))
    page.on("requestfailed", lambda request: issues.append(
        f"request failed: {request.method} {request.url} ({request.failure})"
    ))
    page.set_default_timeout(15_000)
    yield issues
    assert issues == []
