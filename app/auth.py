import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from collections import defaultdict, deque
from urllib.parse import parse_qs

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .config import settings

log = logging.getLogger("artifact_graph.auth")
COOKIE = "artifact_graph_session"
PUBLIC_PATHS = {"/login", "/health/live", "/health/ready", "/metrics"}
LOGIN_PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Artifact Graph — вход</title><style>body{margin:0;background:#0d1117;color:#e6edf3;font:15px system-ui;display:grid;place-items:center;height:100vh}form{width:320px;padding:28px;background:#161b22;border:1px solid #30363d;border-radius:10px}input,button{box-sizing:border-box;width:100%;padding:11px;margin-top:12px;border-radius:7px}input{background:#0d1117;color:white;border:1px solid #30363d}button{background:#238636;color:white;border:0;font-weight:700}.error{color:#e99a95}</style></head><body><form method="post" action="/login"><h2>Artifact Graph</h2><p>Войдите для просмотра карты.</p>{error}<input name="username" autocomplete="username" placeholder="Пользователь" required autofocus><input name="password" type="password" autocomplete="current-password" placeholder="Пароль" required><button type="submit">Войти</button></form></body></html>"""


class LoginLimiter:
    def __init__(self):
        self.attempts = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.time()
        values = self.attempts[key]
        while values and values[0] < now - settings.web_login_window_seconds:
            values.popleft()
        return len(values) < settings.web_login_attempts

    def fail(self, key: str) -> None:
        self.attempts[key].append(time.time())

    def clear(self, key: str) -> None:
        self.attempts.pop(key, None)


limiter = LoginLimiter()


def _key() -> bytes:
    return hashlib.sha256((settings.web_password + "\0artifact-graph-session-v1").encode()).digest()


def issue_session(username: str) -> tuple[str, str]:
    csrf = secrets.token_urlsafe(24)
    data = {"user": username, "exp": int(time.time()) + settings.web_session_hours * 3600, "csrf": csrf}
    payload = base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode().rstrip("=")
    signature = hmac.new(_key(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}", csrf


def read_session(value: str | None) -> dict | None:
    try:
        payload, signature = (value or "").rsplit(".", 1)
        expected = hmac.new(_key(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        data = json.loads(decoded)
        return data if data.get("exp", 0) > time.time() else None
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def login_page(error: bool = False) -> HTMLResponse:
    message = '<p class="error">Неверные учётные данные</p>' if error else ""
    return HTMLResponse(LOGIN_PAGE.replace("{error}", message), status_code=401 if error else 200)


async def login(request: Request):
    client = request.client.host if request.client else "unknown"
    if not limiter.allow(client):
        log.warning("login rate limited client=%s", client)
        return HTMLResponse("Слишком много попыток. Повторите позже.", status_code=429)
    form = parse_qs((await request.body()).decode(errors="replace"))
    username, password = form.get("username", [""])[0], form.get("password", [""])[0]
    valid = hmac.compare_digest(username, settings.web_username) and hmac.compare_digest(password, settings.web_password)
    if not valid:
        limiter.fail(client)
        log.warning("login failed client=%s user=%s", client, username[:80])
        return login_page(True)
    limiter.clear(client)
    token, _ = issue_session(username)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(COOKIE, token, max_age=settings.web_session_hours * 3600, httponly=True, secure=settings.web_cookie_secure, samesite="strict", path="/")
    log.info("login succeeded client=%s user=%s", client, username)
    return response


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.scope["path"]
        if not settings.web_auth_enabled or path in PUBLIC_PATHS or path.startswith("/static/"):
            return await call_next(request)
        session = read_session(request.cookies.get(COOKIE))
        if not session:
            if path.startswith("/api/"):
                return JSONResponse({"detail": "Authentication required"}, status_code=401)
            return RedirectResponse("/login", status_code=303)
        request.state.session = session
        if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get("x-csrf-token") != session["csrf"]:
            return JSONResponse({"detail": "Invalid CSRF token"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response


def logout_response() -> JSONResponse:
    response = JSONResponse({"status": "logged_out"})
    response.delete_cookie(COOKIE, path="/")
    return response
