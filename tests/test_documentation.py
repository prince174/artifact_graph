import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize("filename", ["README.md", "docs/production-runbook.md"])
def test_documentation_local_links_resolve(filename):
    document = ROOT / filename
    contents = document.read_text(encoding="utf-8")
    for target in re.findall(r"\]\(([^)]+)\)", contents):
        if "://" in target or target.startswith("#"):
            continue
        relative, _, anchor = target.partition("#")
        linked = document.parent / relative
        assert linked.is_file(), target
        if anchor == "bitbucket-data-center-production":
            assert f'id="{anchor}"' in linked.read_text(encoding="utf-8")


@pytest.mark.skipif(os.name == "nt" or not shutil.which("bash"), reason="Bash syntax validation on Linux")
@pytest.mark.parametrize("filename", ["README.md", "docs/production-runbook.md"])
def test_documentation_bash_examples_parse_without_execution(filename):
    contents = (ROOT / filename).read_text(encoding="utf-8")
    examples = re.findall(r"^```bash\n(.*?)^```", contents, flags=re.M | re.S)
    assert examples
    for example in examples:
        result = subprocess.run(["bash", "-n"], input=example, text=True, capture_output=True, timeout=5)
        assert result.returncode == 0, result.stderr


def test_dc_switch_guide_is_explicit_about_isolation_and_acceptance():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    production = readme.split('id="bitbucket-data-center-production"', 1)[1].split("## Развёртывание lab", 1)[0]
    for command in re.findall(r"^docker compose (.+)$", production, flags=re.M):
        assert "--project-name artefact-graph-prod" in command
        assert "--env-file .env.prod -f compose.prod.yaml" in command
    for required in ("BITBUCKET_PROVIDER=datacenter", "TLS_CA_FILE=/app/certs/company-ca.pem",
                     "TEAMCITY_BUILD_LIMIT=5", "lastScan.status=success", "--prod", "same-origin",
                     "backups/prod/", "не копируйте cloud dump"):
        assert required.lower() in production.lower(), required
