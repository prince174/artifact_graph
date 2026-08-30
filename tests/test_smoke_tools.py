from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from scripts.validate_live import ensure_authenticated


ROOT = Path(__file__).parents[1]


def test_validate_live_skips_login_when_authentication_is_disabled():
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        return httpx.Response(200, json={"version": "test"})

    with httpx.Client(transport=httpx.MockTransport(handler), base_url="http://graph") as client:
        ensure_authenticated(client, None, None)

    assert [(request.method, request.url.path) for request in requests] == [("GET", "/api/version")]


def test_validate_live_logs_in_without_leaking_password():
    logged_in = False
    secret = "a secret & value"

    def handler(request: httpx.Request):
        nonlocal logged_in
        if request.url.path == "/login":
            form = parse_qs(request.content.decode())
            assert form == {"username": ["root"], "password": [secret]}
            logged_in = True
            return httpx.Response(
                303,
                headers={"location": "/", "set-cookie": "artifact_graph_session=test; Path=/; HttpOnly"},
            )
        if logged_in and request.headers.get("cookie") == "artifact_graph_session=test":
            return httpx.Response(200, json={"version": "test"})
        return httpx.Response(401, json={"detail": "Authentication required"})

    with httpx.Client(transport=httpx.MockTransport(handler), base_url="http://graph") as client:
        ensure_authenticated(client, "root", secret)


@pytest.mark.parametrize("username,password", [(None, None), ("root", None), (None, "secret")])
def test_validate_live_requires_both_credentials(username, password):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(401, json={"detail": "Authentication required"})
    )
    with httpx.Client(transport=transport, base_url="http://graph") as client:
        with pytest.raises(RuntimeError, match="provide WEB_USERNAME and WEB_PASSWORD"):
            ensure_authenticated(client, username, password)


def test_validate_live_reports_login_status_without_password():
    secret = "must-not-appear"

    def handler(request: httpx.Request):
        return httpx.Response(401, json={"detail": "Authentication required"})

    with httpx.Client(transport=httpx.MockTransport(handler), base_url="http://graph") as client:
        with pytest.raises(RuntimeError) as error:
            ensure_authenticated(client, "root", secret)
    assert "HTTP 401" in str(error.value)
    assert secret not in str(error.value)


def test_linux_smoke_uses_cookie_login_and_deploy_passes_env_file():
    smoke = (ROOT / "scripts/smoke-linux.sh").read_text(encoding="utf-8")
    deploy = (ROOT / "scripts/deploy.sh").read_text(encoding="utf-8")
    validator = (ROOT / "scripts/validate_live.py").read_text(encoding="utf-8")

    assert '"$base_url/login"' in smoke
    assert "--data-binary @-" in smoke
    assert '-c "$cookie_jar"' in smoke
    assert '-b "$cookie_jar"' in smoke
    assert "SMOKE_ENV_FILE" in smoke
    assert "password=$web_password" not in smoke
    assert 'SMOKE_ENV_FILE="$deploy_dir/.env"' in deploy
    assert 'add_argument("--password"' not in validator
