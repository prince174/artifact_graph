import base64
import hashlib
import re

import httpx
import pytest
from fastapi import FastAPI

from app.auth import AuthMiddleware
from app.security_headers import CONTENT_SECURITY_POLICY, SECURITY_HEADERS, SecurityHeadersMiddleware
from app.web import PAGE


def test_csp_allows_exact_inline_script_without_inline_event_handlers():
    script = PAGE.rsplit("<script>", 1)[1].split("</script>", 1)[0]
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    script_policy = CONTENT_SECURITY_POLICY.split("script-src ", 1)[1].split(";", 1)[0]
    assert f"'sha256-{digest}'" in script_policy
    assert "'unsafe-inline'" not in script_policy
    assert re.search(r"\sonclick=", PAGE) is None


@pytest.mark.asyncio
async def test_security_headers_cover_public_authenticated_and_error_responses(monkeypatch):
    from app import auth

    monkeypatch.setattr(auth.settings, "web_auth_enabled", True)
    monkeypatch.setattr(auth.settings, "web_password", "security-header-test-password")
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/login")
    def login():
        return auth.login_page()

    @app.get("/api/data")
    def data():
        return {"ok": True}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for response in (await client.get("/login"), await client.get("/api/data"), await client.get("/missing")):
            for name, value in SECURITY_HEADERS.items():
                assert response.headers[name] == value
        assert (await client.get("/api/data")).status_code == 401
