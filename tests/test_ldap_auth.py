from types import SimpleNamespace
import ssl

import httpx
import pytest
from fastapi import FastAPI, Request

from app import auth, ldap_auth
from app.config import settings


@pytest.fixture
def ldap_config(monkeypatch):
    for key, value in dict(web_auth_provider="ldap", ldap_url="ldaps://directory.example",
                           ldap_bind_dn="cn=reader", ldap_bind_password="reader-secret",
                           ldap_user_base_dn="dc=example", ldap_user_filter="(uid={username})",
                           ldap_ca_file="", web_password="s" * 32).items():
        monkeypatch.setattr(settings, key, value)


@pytest.mark.parametrize("url", ["http://bad", "ldap://user:pass@host", "ldaps://host/path"])
def test_invalid_ldap_url(ldap_config, monkeypatch, url):
    monkeypatch.setattr(settings, "ldap_url", url)
    with pytest.raises(ValueError):
        ldap_auth.validate_ldap_settings()


@pytest.mark.parametrize("secure", [True, False])
@pytest.mark.parametrize("count,password,expected", [(1, "correct", True), (0, "correct", False),
                                                     (2, "correct", False), (1, "wrong", False)])
def test_ldap_search_bind_and_cleanup(ldap_config, monkeypatch, secure, count, password, expected):
    import ldap3
    from ldap3.core.exceptions import LDAPInvalidCredentialsResult
    monkeypatch.setattr(settings, "ldap_url", ("ldaps" if secure else "ldap") + "://directory.example")
    connections = []
    class Connection:
        def __init__(self, server, **kwargs):
            assert server.ssl == secure
            assert server.tls.validate == ssl.CERT_REQUIRED
            assert kwargs["auto_referrals"] is False
            self.password = kwargs["password"]
            self.events = []
            self.entries = [SimpleNamespace(entry_dn="uid=alice,dc=example")] * count
            self.result = {"result": 0}
            connections.append(self)
        def open(self): self.events.append("open")
        def start_tls(self): self.events.append("tls"); return True
        def bind(self):
            self.events.append("bind")
            if self.password == "wrong": raise LDAPInvalidCredentialsResult()
            return True
        def search(self, base, query, **kwargs):
            assert query == r"(uid=alice\2a\29\28uid=\2a\29)"
            assert kwargs["size_limit"] == 2
        def unbind(self): self.events.append("unbind")
    monkeypatch.setattr(ldap3, "Connection", Connection)
    assert ldap_auth.authenticate_ldap("alice*)(uid=*)", password) is expected
    for conn in connections:
        assert conn.events == (["open", "bind", "unbind"] if secure else ["open", "tls", "bind", "unbind"])


def test_empty_credentials_never_bind(ldap_config):
    assert not ldap_auth.authenticate_ldap("alice", "")
    assert not ldap_auth.authenticate_ldap("", "secret")


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", [True, False])
async def test_ldap_login_has_no_local_fallback(ldap_config, monkeypatch, allowed):
    monkeypatch.setattr(auth, "limiter", auth.LoginLimiter())
    monkeypatch.setattr(auth, "authenticate_ldap", lambda username, password: allowed)
    app = FastAPI()
    @app.post("/login")
    async def login(request: Request): return await auth.login(request)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
        response = await client.post("/login", data={"username": settings.web_username, "password": settings.web_password})
        assert response.status_code == (303 if allowed else 401)
        assert (auth.COOKIE in client.cookies) == allowed
