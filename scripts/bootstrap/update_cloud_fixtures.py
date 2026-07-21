#!/usr/bin/env python3
import base64
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.fixtures import REPOS, files_for

WORKSPACE = os.environ["BITBUCKET_WORKSPACE"]
EMAIL = os.environ["BB_BOOTSTRAP_EMAIL"]
TOKEN = os.environ["BB_BOOTSTRAP_TOKEN"]


def sync_repo(slug, files):
    auth = base64.b64encode(f"x-bitbucket-api-token-auth:{TOKEN}".encode()).decode()
    git_env = os.environ.copy()
    git_env.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader", "GIT_CONFIG_VALUE_0": f"Authorization: Basic {auth}"})
    with tempfile.TemporaryDirectory(prefix=f"update-{slug}-") as temp:
        root = Path(temp) / "repo"
        subprocess.run(["git", "clone", "--quiet", f"https://bitbucket.org/{WORKSPACE}/{slug}.git", str(root)], env=git_env, check=True)
        for name, content in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        changed = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=root).returncode != 0
        if not changed:
            print(f"unchanged {slug}")
            return
        subprocess.run(["git", "config", "user.name", "Fixture Bot"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", EMAIL], cwd=root, check=True)
        subprocess.run(["git", "commit", "--quiet", "-m", "Make build fixture executable"], cwd=root, check=True)
        subprocess.run(["git", "push", "--quiet", "origin", "main"], cwd=root, env=git_env, check=True)
        print(f"updated {slug}")


if __name__ == "__main__":
    for repo in REPOS:
        sync_repo(repo[0], files_for(*repo))
