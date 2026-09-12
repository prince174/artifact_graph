import ssl
import os
import subprocess
import sys

import pytest

from app import transport_security as security


def test_custom_ca_extends_default_trust_store(monkeypatch):
    class Context:
        loaded = None
        def load_verify_locations(self, *, cafile):
            self.loaded = cafile
    context = Context()
    monkeypatch.setattr(security.ssl, "create_default_context", lambda: context)
    monkeypatch.setattr(security.settings, "verify_tls", True)
    monkeypatch.setattr(security.settings, "tls_ca_file", "/app/certs/corporate.pem")
    assert security.tls_verification() is context
    assert context.loaded == "/app/certs/corporate.pem"


@pytest.fixture
def production(monkeypatch):
    for key, value in {"deployment_mode": "production", "app_mode": "live", "verify_tls": True,
                       "web_auth_enabled": True, "web_cookie_secure": True, "web_password": "x" * 32,
                       "bitbucket_provider": "datacenter", "bitbucket_token": "reader", "teamcity_token": "reader",
                       "bitbucket_url": "https://bb/bitbucket", "teamcity_url": "https://tc/teamcity",
                       "teamcity_public_url": "https://public/teamcity", "registry_enabled": False,
                       "tls_ca_file": "", "database_mode": "external",
                       "database_url": "postgresql+psycopg://user:dummy@db/graph?sslmode=verify-full"}.items():
        monkeypatch.setattr(security.settings, key, value)


def test_secure_production_is_valid(production):
    security.validate_runtime_settings()
    assert isinstance(security.tls_verification(), ssl.SSLContext)


@pytest.mark.parametrize(("key", "value"), [
    ("bitbucket_url", "http://bb"), ("teamcity_url", "https://user:secret@tc"),
    ("teamcity_public_url", "https://tc?secret=123"), ("verify_tls", False),
    ("web_auth_enabled", False), ("web_cookie_secure", False), ("web_password", "short"),
    ("bitbucket_token", ""), ("teamcity_token", ""), ("app_mode", "demo"),
    ("teamcity_build_limit", 0), ("teamcity_build_limit", 101),
])
def test_production_rejects_unsafe_settings(production, monkeypatch, key, value):
    monkeypatch.setattr(security.settings, key, value)
    with pytest.raises(ValueError):
        security.validate_runtime_settings()


def test_bad_corporate_ca_fails_closed(production, monkeypatch, tmp_path):
    monkeypatch.setattr(security.settings, "tls_ca_file", str(tmp_path / "missing.pem"))
    with pytest.raises(OSError):
        security.validate_runtime_settings()


@pytest.mark.parametrize("query", ["", "sslmode=disable", "sslmode=require",
    "sslmode=verify-full&sslmode=disable", "sslmode=verify-full&host=evil",
    "sslmode=verify-full&service=other", "sslmode=verify-full&sslnegotiation=direct"])
def test_external_database_rejects_unsafe_options(production, monkeypatch, query):
    monkeypatch.setattr(security.settings, "database_url", "postgresql+psycopg://u:p@db/graph?" + query)
    with pytest.raises(ValueError):
        security.validate_database_settings()


def test_internal_mode_is_restricted_to_bundled_database(production, monkeypatch):
    monkeypatch.setattr(security.settings, "database_mode", "internal")
    monkeypatch.setattr(security.settings, "database_url", "postgresql+psycopg://u:p@postgres/graph")
    security.validate_database_settings()
    monkeypatch.setattr(security.settings, "database_url", "postgresql+psycopg://u:p@external/graph")
    with pytest.raises(ValueError):
        security.validate_database_settings()


def test_unsafe_database_rejected_before_engine_creation():
    env = dict(os.environ, DEPLOYMENT_MODE="production", DATABASE_MODE="external",
               DATABASE_URL="postgresql+psycopg://u:private-value@db/graph?sslmode=disable")
    result = subprocess.run([sys.executable, "-c", "import app.models"], env=env,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode != 0
    assert "External database requires" in result.stderr
    assert "private-value" not in result.stderr
