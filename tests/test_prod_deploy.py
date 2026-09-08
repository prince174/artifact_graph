import json
import os
import shutil
import subprocess
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import pytest
import yaml


ROOT = Path(__file__).parents[1]
LINUX_SHELL = pytest.mark.skipif(os.name == "nt" or not shutil.which("bash"), reason="Linux deployment scripts")


def test_production_compose_is_an_isolated_hardened_external_server_stack():
    config = yaml.safe_load((ROOT / "compose.prod.yaml").read_text(encoding="utf-8"))
    assert config["name"] == "artefact-graph-prod"
    assert set(config["services"]) == {"graph", "postgres"}
    assert config["volumes"] == {"graph-db": None}
    graph, postgres = config["services"]["graph"], config["services"]["postgres"]
    assert graph["user"] == "10001:10001"
    assert graph["read_only"] and graph["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in graph["security_opt"]
    assert all(port.startswith("127.0.0.1:") for port in graph["ports"])
    assert "ports" not in postgres and "privileged" not in graph
    env = graph["environment"]
    assert env["APP_MODE"] == "live" and env["DEPLOYMENT_MODE"] == "production"
    assert env["VERIFY_TLS"] == env["WEB_AUTH_ENABLED"] == env["WEB_COOKIE_SECURE"] == "true"
    assert env["REGISTRY_ENABLED"] == "false"
    assert env["TEAMCITY_BUILD_LIMIT"] == "${TEAMCITY_BUILD_LIMIT:-5}"
    assert "POSTGRES_PASSWORD:?" in env["DATABASE_URL"]
    assert "WEB_PASSWORD:?" in env["WEB_PASSWORD"]
    assert "POSTGRES_PASSWORD:?" in postgres["environment"]["POSTGRES_PASSWORD"]
    assert all(volume["read_only"] and not volume["bind"]["create_host_path"] for volume in graph["volumes"])
    assert any(volume["target"] == "/app/certs" for volume in graph["volumes"])


def test_production_example_has_no_working_secrets():
    values = {}
    for line in (ROOT / ".env.prod.example").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key] = value
    assert all(values[name] == "" for name in ("POSTGRES_PASSWORD", "WEB_PASSWORD", "BITBUCKET_TOKEN", "TEAMCITY_TOKEN"))
    assert values["BITBUCKET_PROVIDER"] == "datacenter"
    assert values["TEAMCITY_BUILD_LIMIT"] == "5"
    assert values["TLS_CA_FILE"] == ""


def test_production_secrets_are_excluded_from_git_and_docker():
    gitignore = (ROOT / ".gitignore").read_text().splitlines()
    assert ".env.*" in gitignore and "!.env.prod.example" in gitignore
    assert "config/certs/" in gitignore
    dockerignore = (ROOT / ".dockerignore").read_text().splitlines()
    assert ".env*" in dockerignore and "config/certs" in dockerignore
    assert "*.sh text eol=lf" in (ROOT / ".gitattributes").read_text()


@pytest.fixture
def deployment(tmp_path):
    deployment_dir = tmp_path / "deployment with spaces"
    deployment_dir.mkdir()
    (deployment_dir / ".git").mkdir()
    (deployment_dir / "scripts").mkdir()
    for filename in ("compose.yaml", "compose.prod.yaml", ".env.example", ".env.prod.example"):
        shutil.copyfile(ROOT / filename, deployment_dir / filename)
    for filename in (".env", ".env.prod"):
        (deployment_dir / filename).write_text("WEB_USERNAME=root\nWEB_PASSWORD=test-secret\n", encoding="utf-8")
    (deployment_dir / "scripts/smoke-linux.sh").write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n%s\\n%s" "$SMOKE_ENV_FILE" "$GRAPH_URL" "$SMOKE_NOT_BEFORE" > "$SMOKE_LOG"\nexit "${MOCK_SMOKE_EXIT:-0}"\n',
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = f"#!{sys.executable}\n"
    logger = "import json, os, pathlib, sys\nwith open(os.environ['COMMAND_LOG'], 'a') as log:\n log.write(json.dumps([pathlib.Path(sys.argv[0]).name, *sys.argv[1:]]) + '\\n')\n"
    (bin_dir / "git").write_text(
        executable + logger + "if 'status' in sys.argv: print(os.environ.get('MOCK_GIT_DIRTY', ''), end='')\nelif 'rev-parse' in sys.argv: print('a' * 40)\n",
        encoding="utf-8",
    )
    (bin_dir / "docker").write_text(
        executable + logger
        + "if 'config' in sys.argv: print(pathlib.Path(os.environ['MOCK_COMPOSE_CONFIG']).read_text())\nelif 'port' in sys.argv: print('127.0.0.1:18080')\nelif 'up' in sys.argv: sys.exit(int(os.environ.get('MOCK_UP_EXIT', '0')))\n",
        encoding="utf-8",
    )
    for command in bin_dir.iterdir():
        command.chmod(0o755)
    config = {
        "services": {
            "graph": {"environment": {
                "BITBUCKET_URL": "https://bb.example/bitbucket",
                "TEAMCITY_URL": "https://tc.example/teamcity",
                "TEAMCITY_PUBLIC_URL": "https://tc.example/teamcity",
                "WEB_PASSWORD": "w" * 48,
                "TLS_CA_FILE": "",
            }, "volumes": [{"target": "/app/certs", "source": str(tmp_path)}]},
            "postgres": {"environment": {"POSTGRES_PASSWORD": "d" * 48}},
        }
    }
    config_file = tmp_path / "compose.json"
    config_file.write_text(json.dumps(config), encoding="utf-8")
    command_log, smoke_log = tmp_path / "commands.jsonl", tmp_path / "smoke.log"
    env = {
        **os.environ,
        "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        "PYTHON_BIN": sys.executable,
        "COMMAND_LOG": str(command_log),
        "SMOKE_LOG": str(smoke_log),
        "MOCK_COMPOSE_CONFIG": str(config_file),
        "COMPOSE_FILE": "compose.yaml",
        "COMPOSE_PROJECT_NAME": "dangerously-inherited-lab-project",
    }
    env.pop("GRAPH_URL", None)

    def run(*flags, config_change=None, **updates):
        if config_change:
            config_change(config)
            config_file.write_text(json.dumps(config), encoding="utf-8")
        result = subprocess.run(
            ["bash", str(ROOT / "scripts/deploy.sh"), *flags, "https://git.example/graph.git", str(deployment_dir)],
            text=True, capture_output=True, timeout=15, env={**env, **updates},
        )
        commands = [json.loads(line) for line in command_log.read_text().splitlines()] if command_log.exists() else []
        return result, commands

    return deployment_dir, run, smoke_log, tmp_path


@LINUX_SHELL
@pytest.mark.parametrize("mode,filename,project", [("--prod", ".env.prod", "artefact-graph-prod"), ("--lab", ".env", "artefact-graph")])
def test_deploy_uses_explicit_stack_and_checks_a_new_scan(deployment, mode, filename, project):
    directory, run, smoke_log, _ = deployment
    result, commands = run(mode)
    assert result.returncode == 0, result.stderr
    up = next(command for command in commands if command[0] == "docker" and "up" in command)
    assert up[up.index("--project-name") + 1] == project
    assert up[up.index("--env-file") + 1] == str(directory / filename)
    assert up[up.index("-f") + 1] == str(directory / ("compose.prod.yaml" if mode == "--prod" else "compose.yaml"))
    assert "--force-recreate" in up and "--remove-orphans" not in up
    if mode == "--prod":
        assert up[-2:] == ["postgres", "graph"]
    smoke_values = smoke_log.read_text().splitlines()
    assert smoke_values[:2] == [str(directory / filename), "http://localhost:18080"]
    assert datetime.fromisoformat(smoke_values[2].replace("Z", "+00:00")) <= datetime.now(timezone.utc)
    assert (directory / filename).stat().st_mode & 0o777 == 0o600


@LINUX_SHELL
def test_deploy_initial_prod_checkout_creates_blank_template_and_stops(deployment):
    directory, run, _, _ = deployment
    (directory / ".env.prod").unlink()
    result, commands = run("--prod")
    assert result.returncode == 2 and "fill in server URLs" in result.stderr
    assert (directory / ".env.prod").read_bytes() == (ROOT / ".env.prod.example").read_bytes()
    assert not any("up" in command for command in commands)


@LINUX_SHELL
@pytest.mark.parametrize("field,value,expected", [
    ("BITBUCKET_URL", "http://bb.example", "BITBUCKET_URL"),
    ("TEAMCITY_URL", "https://secret:token@tc.example", "TEAMCITY_URL"),
    ("TEAMCITY_PUBLIC_URL", "https://tc.example?token=secret", "TEAMCITY_PUBLIC_URL"),
    ("WEB_PASSWORD", "short", "WEB_PASSWORD"),
    ("WEB_PASSWORD", "d" * 48, "WEB_PASSWORD"),
    ("TLS_CA_FILE", "/etc/shadow", "TLS_CA_FILE"),
    ("TLS_CA_FILE", "/app/certs/../secret", "TLS_CA_FILE"),
    ("TLS_CA_FILE", "/app/certs/missing.pem", "TLS_CA_FILE"),
])
def test_prod_preflight_rejects_insecure_values_before_recreating(deployment, field, value, expected):
    _, run, _, _ = deployment
    result, commands = run("--prod", config_change=lambda config: config["services"]["graph"]["environment"].update({field: value}))
    assert result.returncode != 0 and expected in result.stderr
    assert value not in result.stderr
    assert not any("up" in command for command in commands)


@LINUX_SHELL
def test_prod_preflight_rejects_unsafe_database_password(deployment):
    _, run, _, _ = deployment
    result, commands = run("--prod", config_change=lambda config: config["services"]["postgres"]["environment"].update(POSTGRES_PASSWORD="bad@password"))
    assert result.returncode != 0 and "POSTGRES_PASSWORD" in result.stderr
    assert "bad@password" not in result.stderr
    assert not any("up" in command for command in commands)


@LINUX_SHELL
def test_prod_preflight_accepts_existing_ca_bundle(deployment):
    _, run, _, tmp_path = deployment
    (tmp_path / "company.pem").write_text("test certificate path only", encoding="utf-8")
    result, _ = run("--prod", config_change=lambda config: config["services"]["graph"]["environment"].update(TLS_CA_FILE="/app/certs/company.pem"))
    assert result.returncode == 0, result.stderr


@LINUX_SHELL
@pytest.mark.parametrize("failure", [{"MOCK_UP_EXIT": "1"}, {"MOCK_SMOKE_EXIT": "1"}, {"MOCK_GIT_DIRTY": " M tracked.py"}])
def test_failed_deploy_never_downgrades_code_or_removes_volumes(deployment, failure):
    _, run, _, _ = deployment
    result, commands = run("--prod", **failure)
    assert result.returncode != 0
    assert not any(any(word in command for word in ("down", "rm", "checkout", "reset", "--volumes")) for command in commands)


@contextmanager
def smoke_server(status, graph=None, auth=False):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code, body, headers=None):
            self.send_response(code)
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write((json.dumps(body) if not isinstance(body, str) else body).encode())

        def do_GET(self):
            requests.append(self.path)
            if auth and self.path.startswith("/api/") and self.headers.get("Cookie") != "session=ok":
                return self.send(401, {})
            if self.path == "/health/ready":
                return self.send(200, {"status": "ready", "degraded": True})
            if self.path == "/api/version":
                return self.send(200, {"version": "test"})
            if self.path == "/api/status":
                return self.send(200, status)
            if self.path == "/api/graph":
                return self.send(200, graph if graph is not None else {"nodes": [{"id": "one"}], "edges": []})
            if self.path == "/metrics":
                return self.send(200, "artifact_graph_nodes 1\n")
            self.send(404, {})

        def do_POST(self):
            requests.append(self.path)
            body = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode()
            valid = parse_qs(body) == {"username": ["root"], "password": ["secret & safe"]}
            self.send(303 if valid else 401, "", {"Set-Cookie": "session=ok; Path=/; Secure; HttpOnly"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://localhost:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def run_smoke(url, **updates):
    env = {
        **os.environ, "GRAPH_URL": url, "PYTHON_BIN": sys.executable,
        "SMOKE_TIMEOUT_SECONDS": "0", "SMOKE_NOT_BEFORE": "", "SMOKE_MAX_SCAN_AGE_SECONDS": "300",
        "SMOKE_MIN_NODES": "1", "WEB_USERNAME": "root", "WEB_PASSWORD": "secret & safe",
    }
    env.pop("SMOKE_CA_FILE", None)
    return subprocess.run(["bash", str(ROOT / "scripts/smoke-linux.sh")], capture_output=True, text=True, timeout=10, env={**env, **updates})


def successful_status():
    return {"mode": "live", "refreshMinutes": 60, "lastScan": {"status": "success", "finishedAt": datetime.now(timezone.utc).isoformat()}}


@LINUX_SHELL
def test_smoke_authenticates_secure_cookie_and_accepts_real_graph_without_demo_counts():
    with smoke_server(successful_status(), auth=True) as (url, requests):
        result = run_smoke(url)
    assert result.returncode == 0, result.stderr
    assert "/login" in requests and "/api/graph" in requests
    assert "secret & safe" not in result.stdout + result.stderr


@LINUX_SHELL
@pytest.mark.parametrize("scan_state", [None, "running", "failed", "degraded"])
def test_smoke_does_not_treat_readiness_as_a_successful_scan(scan_state):
    status = successful_status()
    status["lastScan"] = {"status": scan_state} if scan_state else None
    with smoke_server(status) as (url, requests):
        result = run_smoke(url)
    assert result.returncode != 0 and "successful scan timeout" in result.stderr
    assert "/api/graph" not in requests


@LINUX_SHELL
@pytest.mark.parametrize("bad_time", ["2000-01-01T00:00:00Z", "not-a-time", None])
def test_smoke_rejects_old_or_unverifiable_success(bad_time):
    status = successful_status()
    status["lastScan"]["finishedAt"] = bad_time
    with smoke_server(status) as (url, _):
        result = run_smoke(url)
    assert result.returncode != 0 and "successful scan timeout" in result.stderr


@LINUX_SHELL
def test_deployment_smoke_requires_scan_after_deployment_started():
    with smoke_server(successful_status()) as (url, _):
        result = run_smoke(url, SMOKE_NOT_BEFORE=(datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat())
    assert result.returncode != 0 and "fresh successful scan" in result.stderr


@LINUX_SHELL
@pytest.mark.parametrize("graph,expected", [
    ({"nodes": [{"id": "stale", "stale": True}], "edges": []}, "stale nodes"),
    ({"nodes": [], "edges": []}, "Graph is empty"),
    ({"nodes": [], "edges": {}}, "Invalid graph response"),
])
def test_smoke_rejects_stale_empty_and_malformed_graphs(graph, expected):
    with smoke_server(successful_status(), graph=graph) as (url, _):
        result = run_smoke(url)
    assert result.returncode != 0 and expected in result.stderr


@LINUX_SHELL
def test_smoke_allows_explicitly_empty_server():
    with smoke_server(successful_status(), graph={"nodes": [], "edges": []}) as (url, _):
        result = run_smoke(url, SMOKE_MIN_NODES="0")
    assert result.returncode == 0, result.stderr
