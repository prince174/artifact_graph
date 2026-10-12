import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import httpx
import pytest

from app import diagnose


@pytest.fixture
def api_server():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.server.seen.append((self.path, self.headers.get("Authorization")))
            if self.path == "/slow":
                time.sleep(.15)
            status = {"/denied": 403, "/redirect": 302}.get(self.path, 200)
            self.send_response(status)
            self.send_header("Content-Type", "text/html" if self.path == "/login" else "application/json")
            if status == 302:
                self.send_header("Location", "/login")
            self.end_headers()
            try:
                self.wfile.write(b'{"values": []}' if self.path != "/wrong" else b'{}')
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.seen = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", server
    server.shutdown()
    server.server_close()
    thread.join()


def test_real_http_success_context_path_and_no_redirect(api_server):
    url, server = api_server
    assert diagnose.http_check(url + "/bitbucket", "/repos?limit=1", "secret", True, 2, "values")[0]
    assert server.seen == [("/bitbucket/repos?limit=1", "Bearer secret")]
    assert not diagnose.http_check(url, "/redirect", "secret", True, 2, "values")[0]
    assert len(server.seen) == 2


@pytest.mark.parametrize("path", ["/denied", "/login", "/wrong"])
def test_bad_api_response_is_failure(api_server, path):
    url, _ = api_server
    assert not diagnose.http_check(url, path, "secret", True, 2, "values")[0]


def test_http_timeout_is_reported_without_exception_details(api_server, capsys):
    url, _ = api_server
    assert not diagnose.report("API", lambda: diagnose.http_check(url, "/slow", "secret", True, .03, "values"))
    output = capsys.readouterr().out
    assert "ReadTimeout" in output
    assert "secret" not in output
    assert url not in output


def test_exceptions_cannot_leak_secrets(capsys):
    def fail():
        raise RuntimeError("postgresql://user:secret@host/db")
    assert not diagnose.report("PostgreSQL", fail)
    assert "secret" not in capsys.readouterr().out


def test_run_continues_after_database_failure(monkeypatch, capsys):
    from app.config import settings
    from app import transport_security
    monkeypatch.setattr(transport_security, "validate_runtime_settings", lambda: None)
    monkeypatch.setattr(transport_security, "tls_verification", lambda: True)
    monkeypatch.setattr(settings, "bitbucket_provider", "datacenter")
    monkeypatch.setattr(settings, "bitbucket_project_keys", "ONE, TWO,ONE")
    monkeypatch.setattr(diagnose, "database_check", lambda *args: (False, "unavailable"))
    calls = []
    def check(*args):
        calls.append(args[1])
        return True, "OK"
    monkeypatch.setattr(diagnose, "http_check", check)
    assert diagnose.run(2) == 1
    assert calls == ["/rest/api/1.0/projects/ONE/repos?limit=1", "/rest/api/1.0/projects/TWO/repos?limit=1", "/app/rest/server"]
    monkeypatch.setattr(diagnose, "database_check", lambda *args: (True, "OK"))
    assert diagnose.run(2) == 0


def test_database_connection_timeout_and_disposal(monkeypatch):
    class Engine:
        disposed = False
        def connect(self):
            raise RuntimeError("private DB error")
        def dispose(self):
            self.disposed = True
    engine = Engine()
    def create(url, connect_args):
        assert connect_args == {"connect_timeout": 2, "options": "-c statement_timeout=1500"}
        return engine
    monkeypatch.setattr(diagnose, "create_engine", create)
    with pytest.raises(RuntimeError):
        diagnose.database_check(SimpleNamespace(database_url="postgresql+psycopg://user:pw@localhost/db"), 1.5)
    assert engine.disposed


@pytest.mark.parametrize("timeout", ["0", "-1", "nan", "inf"])
def test_invalid_timeouts_exit_before_loading_settings(monkeypatch, timeout):
    monkeypatch.setattr("sys.argv", ["diagnose", "--timeout", timeout])
    with pytest.raises(SystemExit) as exc:
        diagnose.main()
    assert exc.value.code == 2


def test_invalid_url_and_missing_token_do_not_send_requests():
    assert not diagnose.http_check("https://user:secret@bb", "/repos", "token", True, 1, "values")[0]
    assert not diagnose.http_check("https://bb", "/repos", "", True, 1, "values")[0]
