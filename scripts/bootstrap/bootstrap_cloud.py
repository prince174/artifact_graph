#!/usr/bin/env python3
"""Create 10 fixture repositories in Bitbucket Cloud and matching TeamCity configs."""
import base64
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

import httpx

from bootstrap import REPOS, files_for, request, tc_put

WORKSPACE = os.environ["BITBUCKET_WORKSPACE"]
EMAIL = os.environ["BB_BOOTSTRAP_EMAIL"]
TOKEN = os.environ["BB_BOOTSTRAP_TOKEN"]
TC_URL = os.getenv("TEAMCITY_BOOTSTRAP_URL", "http://localhost:8111").rstrip("/")
TC_TOKEN = os.getenv("TC_ADMIN_TOKEN", "")
TC_READER_USERNAME = os.getenv("TEAMCITY_READER_USERNAME", "")


def cloud_headers():
    encoded = base64.b64encode(f"{EMAIL}:{TOKEN}".encode()).decode()
    return {"Authorization": f"Basic {encoded}", "Accept": "application/json", "Content-Type": "application/json"}


def push(slug, files):
    auth = base64.b64encode(f"x-bitbucket-api-token-auth:{TOKEN}".encode()).decode()
    with tempfile.TemporaryDirectory(prefix=f"cloud-{slug}-") as temp:
        root = Path(temp)
        for name, content in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        commands = [
            ["git", "init", "-b", "main"], ["git", "config", "user.name", "Fixture Bot"],
            ["git", "config", "user.email", EMAIL], ["git", "add", "."],
            ["git", "commit", "-m", "Initial test project"],
            ["git", "remote", "add", "origin", f"https://bitbucket.org/{WORKSPACE}/{slug}.git"],
        ]
        for command in commands:
            subprocess.run(command, cwd=root, check=True, stdout=subprocess.DEVNULL)
        git_env = os.environ.copy()
        git_env.update({
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.extraHeader",
            "GIT_CONFIG_VALUE_0": f"Authorization: Basic {auth}",
        })
        for attempt in range(6):
            result = subprocess.run(
                ["git", "push", "-u", "origin", "main"], cwd=root, env=git_env,
                text=True, capture_output=True,
            )
            if result.returncode == 0:
                print(f"pushed {slug}")
                return
            if attempt < 5:
                time.sleep(2 ** attempt)
        raise RuntimeError(f"Git push failed for {slug}: {result.stderr.strip()}")


def has_commits(client, slug):
    response = client.get(f"/repositories/{WORKSPACE}/{slug}/commits", params={"pagelen": 1})
    if response.status_code == 409:  # Empty repository.
        return False
    response.raise_for_status()
    return bool(response.json().get("values"))


def bootstrap_cloud():
    with httpx.Client(base_url="https://api.bitbucket.org/2.0", headers=cloud_headers(), timeout=60) as client:
        project = client.post(f"/workspaces/{WORKSPACE}/projects", json={"key": "DEMO", "name": "Artefact Graph Demo"})
        project_exists = project.status_code == 400 and "already exists" in project.text.lower()
        if project.status_code not in (200, 201, 409) and not project_exists:
            raise RuntimeError(f"create project: {project.status_code} {project.text}")
        for slug, stack, command, sbom in REPOS:
            response = client.post(f"/repositories/{WORKSPACE}/{slug}", json={"scm": "git", "is_private": True, "project": {"key": "DEMO"}})
            if response.status_code not in (200, 201, 400):
                raise RuntimeError(f"create {slug}: {response.status_code} {response.text}")
            if response.status_code == 400 and "already exists" not in response.text.lower():
                raise RuntimeError(f"create {slug}: {response.status_code} {response.text}")
            if not has_commits(client, slug):
                push(slug, files_for(slug, stack, command, sbom))


def bootstrap_teamcity():
    if not TC_TOKEN:
        print("TC_ADMIN_TOKEN is empty; TeamCity bootstrap skipped")
        return
    headers = {"Authorization": f"Bearer {TC_TOKEN}", "Accept": "application/json", "Content-Type": "application/json"}
    with httpx.Client(base_url=TC_URL, headers=headers, timeout=60) as client:
        response = client.post("/app/rest/projects", json={"id": "Demo", "name": "Artefact Graph Demo"})
        if response.status_code not in (200, 201, 400):
            raise RuntimeError(response.text)
        for index, (slug, _stack, _command, sbom) in enumerate(REPOS, 1):
            vcs_id, build_id = f"Demo_{index:02d}_Vcs", f"Demo_{index:02d}"
            props = [
                {"name": "url", "value": f"https://bitbucket.org/{WORKSPACE}/{slug}.git"},
                {"name": "branch", "value": "refs/heads/main"},
                {"name": "authMethod", "value": "PASSWORD"},
                {"name": "username", "value": "x-bitbucket-api-token-auth"},
                {"name": "secure:password", "value": TOKEN},
            ]
            vcs = {"id": vcs_id, "name": slug, "vcsName": "jetbrains.git", "project": {"id": "Demo"}, "properties": {"property": props}}
            exists = client.get(f"/app/rest/vcs-roots/id:{vcs_id}")
            response = client.put(f"/app/rest/vcs-roots/id:{vcs_id}", json=vcs) if exists.status_code == 200 else client.post("/app/rest/vcs-roots", json=vcs)
            if response.status_code not in (200, 201): raise RuntimeError(response.text)
            build_payload = {"id": build_id, "name": f"{index:02d} Build {slug}", "project": {"id": "Demo"}}
            exists = client.get(f"/app/rest/buildTypes/id:{build_id}")
            response = client.put(f"/app/rest/buildTypes/id:{build_id}", json=build_payload) if exists.status_code == 200 else client.post("/app/rest/buildTypes", json=build_payload)
            if response.status_code not in (200, 201): raise RuntimeError(response.text)
            tc_put(client, f"/app/rest/buildTypes/id:{build_id}/vcs-root-entries", {"vcs-root-entry": [{"id": vcs_id, "vcs-root": {"id": vcs_id}}]})
            script = "set -eu\nchmod +x ci/build.sh\n./ci/build.sh" + ("\nchmod +x ci/sbom.sh\n./ci/sbom.sh" if sbom else "")
            tc_put(client, f"/app/rest/buildTypes/id:{build_id}/steps", {"step": [{"name": "Build and publish", "type": "simpleRunner", "properties": {"property": [{"name": "script.content", "value": script}]}}]})
            if sbom:
                response = client.put(
                    f"/app/rest/buildTypes/id:{build_id}/settings/artifactRules",
                    content="**/sbom.json => artifacts",
                    headers={"Content-Type": "text/plain", "Accept": "text/plain"},
                )
                if response.status_code not in (200, 201, 204):
                    raise RuntimeError(f"set artifact rules for {build_id}: {response.status_code} {response.text[:500]}")
        if TC_READER_USERNAME:
            request(client, "PUT", f"/app/rest/users/username:{TC_READER_USERNAME}/roles/PROJECT_VIEWER/p:Demo", ok=(200, 204))
            print(f"granted PROJECT_VIEWER on Demo to {TC_READER_USERNAME}")


if __name__ == "__main__":
    bootstrap_cloud()
    bootstrap_teamcity()
    print("Bootstrap complete. Do not use BB_BOOTSTRAP_TOKEN in the graph service.")
    print("Create a separate read-only API token with read:repository:bitbucket scope.")
