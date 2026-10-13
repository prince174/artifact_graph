import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(not shutil.which("docker"), reason="Docker Compose CLI required")
@pytest.mark.parametrize("base", ["compose.yaml", "compose.prod.yaml", "compose.prod.external-db.yaml"])
def test_https_overlay_merges_with_each_base(base, tmp_path):
    env_file = tmp_path / "empty.env"
    env_file.write_text("")
    env = {**os.environ, "DATABASE_URL": "postgresql://test:test@db/graph",
           "POSTGRES_PASSWORD": "test-password", "WEB_PASSWORD": "test-password",
           "BITBUCKET_URL": "https://bb.example", "BITBUCKET_TOKEN": "test-token",
           "TEAMCITY_URL": "https://tc.example", "TEAMCITY_TOKEN": "test-token",
           "HTTPS_CERT_DIR": str(tmp_path), "HTTPS_PORT": "443", "HTTPS_BIND": "0.0.0.0",
           "GRAPH_BIND": "127.0.0.1"}
    result = subprocess.run(["docker", "compose", "--env-file", str(env_file), "-f", base,
                             "-f", "compose.https.yaml", "config", "--format", "json"],
                            cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    services = json.loads(result.stdout)["services"]
    assert services["graph"]["environment"]["WEB_COOKIE_SECURE"] == "true"
    assert services["graph"]["ports"][0]["host_ip"] == "127.0.0.1"
    proxy = services["nginx"]
    assert proxy["ports"][0]["published"] == "443"
    assert proxy["ports"][0]["target"] == 8443
    assert proxy["read_only"] is True
    assert proxy["user"] == "101:101"
    assert all(volume["read_only"] for volume in proxy["volumes"])
    assert proxy["depends_on"]["graph"]["condition"] == "service_started"
