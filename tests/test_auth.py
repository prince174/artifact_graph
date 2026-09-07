import time

import httpx
import pytest
from fastapi import FastAPI, Request

from app import auth


def test_signed_session_round_trip_and_tampering(monkeypatch):
    monkeypatch.setattr(auth.settings, "web_password", "a-long-test-password")
    token, csrf = auth.issue_session("root")
    decoded = auth.read_session(token)
    assert decoded["user"] == "root" and decoded["csrf"] == csrf
    assert auth.read_session(token + "x") is None


def test_login_page_renders_css_and_optional_error():
    assert auth.login_page().status_code == 200
    failed = auth.login_page(True)
    assert failed.status_code == 401
    assert "Неверные учётные данные" in failed.body.decode()


def test_expired_session_is_rejected(monkeypatch):
    monkeypatch.setattr(auth.settings, "web_password", "test-password")
    monkeypatch.setattr(auth.settings, "web_session_hours", -1)
    token, _ = auth.issue_session("root")
    assert auth.read_session(token) is None


def test_login_limiter_expires_attempts(monkeypatch):
    limiter = auth.LoginLimiter()
    monkeypatch.setattr(auth.settings, "web_login_attempts", 2)
    monkeypatch.setattr(auth.settings, "web_login_window_seconds", 10)
    limiter.fail("client"); limiter.fail("client")
    assert limiter.allow("client") is False
    limiter.attempts["client"][0] = limiter.attempts["client"][1] = time.time() - 11
    assert limiter.allow("client") is True


@pytest.mark.asyncio
async def test_middleware_requires_login_and_csrf(monkeypatch):
    monkeypatch.setattr(auth.settings, "web_auth_enabled", True)
    monkeypatch.setattr(auth.settings, "web_username", "root")
    monkeypatch.setattr(auth.settings, "web_password", "secret-password")
    auth.limiter.attempts.clear()
    app = FastAPI()
    app.add_middleware(auth.AuthMiddleware)

    @app.post("/login")
    async def login(request: Request):
        return await auth.login(request)

    @app.get("/api/data")
    def data():
        return {"ok": True}

    @app.get("/static/app.js")
    def static_asset():
        return {"asset": True}

    @app.post("/api/change")
    def change():
        return {"changed": True}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/data")).status_code == 401
        for host in ("test/health/live?x=", "test/static/x#", "test/login?"):
            assert (await client.get("/api/data", headers={"Host": host})).status_code == 401
            assert (await client.post("/api/change", headers={"Host": host})).status_code == 401
        assert (await client.get("/static/app.js")).status_code == 200
        response = await client.post("/login", content="username=root&password=secret-password", headers={"content-type": "application/x-www-form-urlencoded"}, follow_redirects=False)
        assert response.status_code == 303
        assert "HttpOnly" in response.headers["set-cookie"] and "SameSite=strict" in response.headers["set-cookie"]
        assert (await client.get("/api/data")).status_code == 200
        assert (await client.post("/api/change")).status_code == 403
        session = auth.read_session(client.cookies.get(auth.COOKIE))
        assert (await client.post("/api/change", headers={"x-csrf-token": session["csrf"]})).status_code == 200
