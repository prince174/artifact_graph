#!/usr/bin/env python3
"""Create the disposable Bitbucket/TeamCity integration fixture.

Prerequisites: finish both products' browser setup, then export BB_ADMIN_TOKEN and
TC_ADMIN_TOKEN. This script is intentionally idempotent where the APIs permit it.
"""
import json
import os
import subprocess
import tempfile
from pathlib import Path

import httpx

BB = os.getenv("BITBUCKET_URL", "http://localhost:7990").rstrip("/")
TC = os.getenv("TEAMCITY_URL", "http://localhost:8111").rstrip("/")
BB_TOKEN = os.getenv("BB_ADMIN_TOKEN", "")
TC_TOKEN = os.getenv("TC_ADMIN_TOKEN", "")
PROJECT = "DEMO"

REPOS = [
    ("java-maven-api", "maven", "docker push localhost:5000/java-maven-api:1.0", True),
    ("java-gradle-worker", "gradle", "podman push localhost:5000/java-gradle-worker:1.0", False),
    ("npm-frontend", "npm", "docker push localhost:5000/npm-frontend:1.0", True),
    ("python-service", "python", "docker push localhost:5000/python-service:1.0", False),
    ("terraform-infra", "terraform", "", True),
    ("ansible-deploy", "ansible", "", False),
    ("java-maven-orders", "maven", "docker push localhost:5000/orders:1.0", True),
    ("java-gradle-billing", "gradle", "podman push localhost:5000/billing:1.0", False),
    ("npm-admin", "npm", "docker push localhost:5000/npm-admin:1.0", True),
    ("python-jobs", "python", "docker push localhost:5000/python-jobs:1.0", False),
]


def headers(token):
    return {"Authorization": f"Bearer {token}", "Accept": "application/json", "Content-Type": "application/json"}


def request(client, method, path, *, ok=(200, 201, 204), **kwargs):
    response = client.request(method, path, **kwargs)
    if response.status_code not in ok:
        raise RuntimeError(f"{method} {path}: {response.status_code} {response.text[:500]}")
    return response


def files_for(slug, stack, command, sbom):
    common = {
        "README.md": f"# {slug}\n\nIntegration fixture for Artefact Graph.\n",
        "Dockerfile": "FROM alpine:3.20\nCMD [\"echo\", \"fixture\"]\n",
        "ci/build.sh": f"#!/bin/sh\nset -eu\necho build {slug}\n{command or 'echo no image for this build'}\n",
    }
    if sbom:
        common["ci/sbom.sh"] = "#!/bin/sh\nmkdir -p build\nprintf '{\"bomFormat\":\"CycloneDX\"}' > build/sbom.json\n"
    additions = {
        "maven": {"pom.xml": "<project><modelVersion>4.0.0</modelVersion><groupId>demo</groupId><artifactId>app</artifactId><version>1</version></project>\n"},
        "gradle": {"settings.gradle": f"rootProject.name='{slug}'\n", "build.gradle": "plugins { id 'java' }\n"},
        "npm": {"package.json": json.dumps({"name": slug, "version": "1.0.0", "scripts": {"build": "echo built"}}, indent=2)},
        "python": {"pyproject.toml": "[project]\nname='demo-app'\nversion='1.0.0'\n", "src/main.py": "print('fixture')\n"},
        "terraform": {"main.tf": 'terraform { required_version = \">= 1.5\" }\nresource "null_resource" "fixture" {}\n'},
        "ansible": {"playbook.yml": "- hosts: localhost\n  gather_facts: false\n  tasks:\n    - debug: msg=fixture\n"},
    }
    common.update(additions[stack])
    if int(slug.encode().hex(), 16) % 2 == 0:
        common[".teamcity/settings.kts"] = "import jetbrains.buildServer.configs.kotlin.*\nversion = \"2024.12\"\nproject { }\n"
    return common


def git_push(slug, files):
    with tempfile.TemporaryDirectory(prefix=f"fixture-{slug}-") as temp:
        root = Path(temp)
        for name, content in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        commands = [
            ["git", "init", "-b", "main"], ["git", "config", "user.name", "Fixture Bot"],
            ["git", "config", "user.email", "fixture@example.invalid"], ["git", "add", "."],
            ["git", "commit", "-m", "Initial test project"],
            ["git", "remote", "add", "origin", f"http://x-token-auth:{BB_TOKEN}@localhost:7990/scm/{PROJECT.lower()}/{slug}.git"],
            ["git", "push", "-u", "origin", "main"],
        ]
        for command in commands:
            subprocess.run(command, cwd=root, check=True, stdout=subprocess.DEVNULL)


def bootstrap_bitbucket():
    with httpx.Client(base_url=BB, headers=headers(BB_TOKEN), timeout=60) as client:
        response = client.post("/rest/api/1.0/projects", json={"key": PROJECT, "name": "Artefact Graph Demo"})
        if response.status_code not in (201, 409):
            raise RuntimeError(response.text)
        for slug, stack, command, sbom in REPOS:
            response = client.post(f"/rest/api/1.0/projects/{PROJECT}/repos", json={"name": slug, "scmId": "git", "forkable": True})
            if response.status_code == 201:
                git_push(slug, files_for(slug, stack, command, sbom))
            elif response.status_code != 409:
                raise RuntimeError(response.text)
        token = request(client, "PUT", f"/rest/access-tokens/1.0/projects/{PROJECT}", json={"name": "artefact-graph-ro", "permissions": ["PROJECT_READ"]}).json()
        return token.get("token", "token already existed; recreate it in Bitbucket UI")


def tc_put(client, path, payload):
    return request(client, "PUT", path, ok=(200, 201, 204), content=json.dumps(payload))


def bootstrap_teamcity():
    with httpx.Client(base_url=TC, headers=headers(TC_TOKEN), timeout=60) as client:
        project = client.post("/app/rest/projects", json={"id": "Demo", "name": "Artefact Graph Demo"})
        if project.status_code not in (200, 201, 400):
            raise RuntimeError(project.text)
        for index, (slug, _stack, command, sbom) in enumerate(REPOS, 1):
            vcs_id, build_id = f"Demo_{index:02d}_Vcs", f"Demo_{index:02d}"
            vcs_payload = {"id": vcs_id, "name": slug, "vcsName": "jetbrains.git", "project": {"id": "Demo"}, "properties": {"property": [
                {"name": "url", "value": f"http://bitbucket:7990/scm/demo/{slug}.git"},
                {"name": "branch", "value": "refs/heads/main"}, {"name": "authMethod", "value": "ANONYMOUS"}]}}
            response = client.post("/app/rest/vcs-roots", json=vcs_payload)
            if response.status_code not in (200, 201, 400): raise RuntimeError(response.text)
            build_payload = {"id": build_id, "name": f"{index:02d} Build {slug}", "project": {"id": "Demo"}}
            response = client.post("/app/rest/buildTypes", json=build_payload)
            if response.status_code not in (200, 201, 400): raise RuntimeError(response.text)
            tc_put(client, f"/app/rest/buildTypes/id:{build_id}/vcs-root-entries", {"vcs-root-entry": [{"id": vcs_id, "vcs-root": {"id": vcs_id}}]})
            script = "set -eu\nchmod +x ci/build.sh\n./ci/build.sh" + ("\nchmod +x ci/sbom.sh\n./ci/sbom.sh" if sbom else "")
            tc_put(client, f"/app/rest/buildTypes/id:{build_id}/steps", {"step": [{"name": "Build and publish", "type": "simpleRunner", "properties": {"property": [{"name": "script.content", "value": script}, {"name": "teamcity.step.mode", "value": "default"}]}}]})
            if sbom:
                tc_put(client, f"/app/rest/buildTypes/id:{build_id}/settings/artifactRules", "**/sbom.json => artifacts")
        user = client.post("/app/rest/users", json={"username": "artefact-graph-ro", "name": "Artefact Graph Reader", "password": os.urandom(24).hex()})
        if user.status_code not in (200, 201, 400): raise RuntimeError(user.text)
        request(client, "PUT", "/app/rest/users/username:artefact-graph-ro/roles/PROJECT_VIEWER/p:Demo", ok=(200, 204))
        token = request(client, "POST", "/app/rest/users/username:artefact-graph-ro/tokens/artefact-graph", ok=(200, 201)).json()
        return token.get("value", "token already existed; recreate it in TeamCity UI")


if __name__ == "__main__":
    bb_token = bootstrap_bitbucket()
    tc_token = bootstrap_teamcity()
    print("\nStore these in .env (values are shown only once):")
    print(f"BITBUCKET_TOKEN={bb_token}")
    print(f"TEAMCITY_TOKEN={tc_token}")
