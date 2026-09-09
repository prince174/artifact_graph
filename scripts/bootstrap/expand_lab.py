#!/usr/bin/env python3
"""Add only owned LabMatrix fixtures to the existing Cloud/local-TeamCity lab.

Default execution is a read-only plan. --apply provisions fixtures but never
queues builds, authorizes agents, modifies Demo, or removes existing entities.
"""

import argparse
import base64
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlsplit

import httpx
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.teamcity_setup import command_line_script_step


class ProvisionError(RuntimeError):
    """Safe-to-display error: never includes response bodies or credentials."""


def _call(client, method, path, *, missing=False, allowed=(), **kwargs):
    method = method.upper()
    for attempt in range(3):
        try:
            response = client.request(method, path, **kwargs)
        except httpx.HTTPError:
            if method == "GET" and attempt < 2:
                time.sleep(0.5 * 2 ** attempt)
                continue
            raise ProvisionError(f"{method} {path}: upstream connection failed") from None
        if method == "GET" and response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
            delay = 0.5 * 2 ** attempt
            try:
                retry_after = float(response.headers.get("Retry-After", ""))
                if math.isfinite(retry_after) and retry_after >= 0:
                    delay = min(retry_after, 2.0)
            except ValueError:
                pass
            time.sleep(delay)
            continue
        if missing and response.status_code == 404:
            return None
        if response.status_code not in (*range(200, 300), *allowed):
            raise ProvisionError(f"{method} {path}: HTTP {response.status_code}")
        return response


def _json(response):
    if response is None:
        return None
    try:
        return response.json()
    except ValueError:
        raise ProvisionError("Upstream returned invalid JSON") from None


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _marker(plan, kind, identity, value):
    return f"Managed by {plan['fixture_key']}; {kind}={identity}; sha256={_digest(value)}"


def vcs_id(slug):
    return "LabMatrix_Vcs_" + slug.replace("-", "_")


def _token(value, name):
    if not value or value.lower().startswith("replace") or any(char in value for char in "\r\n"):
        raise ProvisionError(f"{name} is missing or is a placeholder")


def _validate_plan(plan, workspace):
    if plan.get("fixture_key") != "artifact-graph-lab-v1":
        raise ProvisionError("Unknown fixture ownership key")
    if not re.fullmatch(r"[a-z0-9_-]+", workspace) or plan.get("workspace", workspace) != workspace:
        raise ProvisionError("Workspace does not match the lab plan")
    for collection, identity in (("projects", "key"), ("repositories", "slug"), ("tc_projects", "id"), ("configs", "id")):
        values = [item[identity] for item in plan[collection]]
        if len(values) != len(set(values)):
            raise ProvisionError(f"Duplicate {collection} identifiers")
    projects = {item["key"] for item in plan["projects"]}
    repos = {item["slug"] for item in plan["repositories"]}
    tc_projects = {item["id"] for item in plan["tc_projects"]}
    configs = {item["id"] for item in plan["configs"]}
    if any(not re.fullmatch(r"LAB[A-Z0-9_]*", item) for item in projects):
        raise ProvisionError("Only LAB-prefixed Bitbucket projects may be provisioned")
    if "LabMatrix" not in tc_projects or any(not re.fullmatch(r"LabMatrix(?:_[A-Za-z0-9_]+)?", item) for item in tc_projects | configs):
        raise ProvisionError("TeamCity entities must belong to the LabMatrix namespace")
    for project in plan["tc_projects"]:
        expected_parents = {"_Root"} if project["id"] == "LabMatrix" else tc_projects - {project["id"]}
        if project["parent_id"] not in expected_parents:
            raise ProvisionError("TeamCity parent is outside the managed project tree")
    for repo in plan["repositories"]:
        if not re.fullmatch(r"lab-[a-z0-9-]+", repo["slug"]):
            raise ProvisionError("Repository slug must use the lab- namespace")
        if repo["project_key"] not in projects | {"DEMO"}:
            raise ProvisionError("Repository project is outside the plan")
        branch = repo["branch"]
        if not branch or not re.fullmatch(r"[A-Za-z0-9_./-]+", branch) or ".." in branch or branch.startswith(("/", "-")) or branch.endswith(("/", ".", ".lock")):
            raise ProvisionError("Invalid fixture branch")
        for filename, content in repo["files"].items():
            path = PurePosixPath(filename)
            if not filename or path.is_absolute() or ".." in path.parts or "\\" in filename or ".git" in path.parts or not isinstance(content, str):
                raise ProvisionError("Invalid fixture file path or contents")
    for config in plan["configs"]:
        if config["project_id"] not in tc_projects or not set(config["repository_slugs"]) <= repos:
            raise ProvisionError("Configuration references an unmanaged project or repository")
        if not set(config["dependencies"]) <= configs - {config["id"]}:
            raise ProvisionError("Configuration dependency is outside the plan")
        if not set(config.get("checkout_rules", {})) <= set(config["repository_slugs"]):
            raise ProvisionError("Checkout rule references an unattached repository")
    _ordered(plan["tc_projects"], "parent_id", external={"_Root"})
    _ordered(plan["configs"], "dependencies")


def _ordered(items, dependency, external=frozenset()):
    remaining = list(items)
    known = set(external)
    ordered = []
    while remaining:
        ready = [item for item in remaining if set([item[dependency]] if isinstance(item[dependency], str) else item[dependency]) <= known]
        if not ready:
            raise ProvisionError("Cycle in the fixture plan")
        for item in ready:
            ordered.append(item)
            known.add(item["id"])
            remaining.remove(item)
    return ordered


def verify_git_read(workspace, slug, checkout_token, *, runner=subprocess.run, require_heads=True):
    """Validate only Git read access; token RO scopes must be set by its owner."""
    if not re.fullmatch(r"[a-z0-9_-]+", workspace) or not re.fullmatch(r"[a-z0-9_-]+", slug):
        raise ProvisionError("Invalid checkout probe repository")
    auth = base64.b64encode(f"x-bitbucket-api-token-auth:{checkout_token}".encode()).decode()
    environment = {key: value for key, value in os.environ.items() if not key.startswith(("GIT_", "BB_", "BITBUCKET_", "TEAMCITY_", "TC_", "WEB_", "DATABASE_", "POSTGRES_", "REGISTRY_"))}
    environment.update({"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_COUNT": "4"})
    for index, (name, value) in enumerate((
        ("http.https://bitbucket.org/.extraHeader", f"Authorization: Basic {auth}"),
        ("http.followRedirects", "false"), ("credential.helper", ""), ("credential.interactive", "false"),
    )):
        environment[f"GIT_CONFIG_KEY_{index}"] = name
        environment[f"GIT_CONFIG_VALUE_{index}"] = value
    try:
        with tempfile.TemporaryDirectory(prefix="lab-git-read-") as directory:
            result = runner(["git", "ls-remote", "--heads", f"https://bitbucket.org/{workspace}/{slug}.git"],
                            cwd=directory, env=environment, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        raise ProvisionError(f"Read-only Git probe failed for {slug}") from None
    if result.returncode or (require_heads and not re.search(r"^[0-9a-f]{40,64}\s+refs/heads/", result.stdout, re.MULTILINE)):
        raise ProvisionError(f"Checkout token cannot read Git repository {slug}")


def _owned(entity, marker, label):
    if entity is not None and entity.get("description") != marker:
        raise ProvisionError(f"Ownership or plan mismatch for {label}; existing entity will not be overwritten")


def _props(entity):
    return {item["name"]: item.get("value", "") for item in entity.get("properties", {}).get("property", [])}


def _entries(config):
    return {"vcs-root-entry": [{"id": vcs_id(slug), "vcs-root": {"id": vcs_id(slug)}, "checkout-rules": config.get("checkout_rules", {}).get(slug, "")}
                               for slug in config["repository_slugs"]]}


def _config_matches(existing, config):
    steps = existing.get("steps", {}).get("step", [])
    if len(steps) != 1 or steps[0].get("disabled") or steps[0].get("type") != "simpleRunner" or _props(steps[0]).get("script.content") != config["script"] or _props(steps[0]).get("use.custom.script") != "true":
        return False
    actual_roots = {item.get("vcs-root", {}).get("id", item.get("id")): item.get("checkout-rules", "") for item in existing.get("vcs-root-entries", {}).get("vcs-root-entry", [])}
    expected_roots = {vcs_id(slug): config.get("checkout_rules", {}).get(slug, "") for slug in config["repository_slugs"]}
    actual_dependencies = {item["source-buildType"]["id"] for item in existing.get("snapshot-dependencies", {}).get("snapshot-dependency", [])}
    artifact_rules = {item["name"]: item.get("value", "") for item in existing.get("settings", {}).get("property", [])}.get("artifactRules", "")
    return actual_roots == expected_roots and actual_dependencies == set(config["dependencies"]) and artifact_rules == config["artifacts"] and bool(existing.get("paused")) == bool(config.get("paused", False))


def provision_plan(plan, bb, tc, *, workspace, bootstrap_token, checkout_token, tc_admin_token,
                   reader_username="", checkout_probe_repo="java-maven-api", apply=False, git_runner=subprocess.run):
    """Preflight all ownership/capacity before writes, then provision owned fixtures.

    Clients are explicitly supplied so MockTransport tests cannot reach real APIs.
    Existing owned source content is verified and never overwritten. Existing
    owned TC configurations may be reconciled only to their marked plan digest.
    """
    _validate_plan(plan, workspace)
    for value, name in ((bootstrap_token, "BB_BOOTSTRAP_TOKEN"), (checkout_token, "checkout token"), (tc_admin_token, "TC_ADMIN_TOKEN")):
        _token(value, name)
    if checkout_token == bootstrap_token:
        raise ProvisionError("Checkout token must be separate from the bootstrap token")
    bb_url, tc_url = urlsplit(str(bb.base_url)), urlsplit(str(tc.base_url))
    if bb_url.scheme != "https" or bb_url.netloc != "api.bitbucket.org" or bb_url.path.rstrip("/") != "/2.0":
        raise ProvisionError("Bitbucket client must target https://api.bitbucket.org/2.0")
    if tc_url.scheme not in {"http", "https"} or tc_url.hostname not in {"localhost", "127.0.0.1", "::1", "teamcity"} or tc_url.username or tc_url.password or tc_url.query or tc_url.fragment:
        raise ProvisionError("This utility targets only the local TeamCity lab")
    verify_git_read(workspace, checkout_probe_repo, checkout_token, runner=git_runner)
    _call(bb, "GET", f"/workspaces/{workspace}")
    licensing = _json(_call(tc, "GET", "/app/rest/server/licensingData", params={"fields": "licenseUseExceeded,buildTypesLeft,unlimitedBuildTypes,maxBuildTypes,agentsLeft,maxAgents"}))
    if licensing.get("licenseUseExceeded"):
        raise ProvisionError("TeamCity license capacity is already exceeded")
    if reader_username:
        _call(tc, "GET", f"/app/rest/users/username:{quote(reader_username, safe='')}", params={"fields": "id,username"})

    state = {"projects": {}, "repositories": {}, "tc_projects": {}, "vcs": {}, "configs": {}}
    for project in plan["projects"]:
        item = _json(_call(bb, "GET", f"/workspaces/{workspace}/projects/{project['key']}", missing=True))
        _owned(item, _marker(plan, "bb-project", project["key"], project), project["key"])
        state["projects"][project["key"]] = item
    if any(repo["project_key"] == "DEMO" for repo in plan["repositories"]):
        _call(bb, "GET", f"/workspaces/{workspace}/projects/DEMO")
    for repo in plan["repositories"]:
        base = f"/repositories/{workspace}/{repo['slug']}"
        item = _json(_call(bb, "GET", base, missing=True))
        _owned(item, _marker(plan, "repository", repo["slug"], repo), repo["slug"])
        filled = False
        if item:
            if item.get("project", {}).get("key") != repo["project_key"] or item.get("is_private") is not True:
                raise ProvisionError(f"Repository location/privacy mismatch for {repo['slug']}")
            commits = _call(bb, "GET", f"{base}/commits", allowed=(409,), params={"pagelen": 1, "fields": "values(hash)"})
            filled = commits.status_code != 409 and bool(_json(commits).get("values"))
            if filled:
                branch = _json(_call(bb, "GET", f"{base}/refs/branches/{quote(repo['branch'], safe='')}"))
                revision = branch.get("target", {}).get("hash", "")
                if not re.fullmatch(r"[0-9a-f]{40,64}", revision):
                    raise ProvisionError(f"Cannot verify revision for {repo['slug']}")
                for filename, content in repo["files"].items():
                    actual = _call(bb, "GET", f"{base}/src/{revision}/{quote(filename, safe='/')}")
                    if actual.content != content.encode():
                        raise ProvisionError(f"Source contents drifted for {repo['slug']}; refusing overwrite")
        state["repositories"][repo["slug"]] = {"existing": item, "filled": filled}
    for project in _ordered(plan["tc_projects"], "parent_id", external={"_Root"}):
        item = _json(_call(tc, "GET", f"/app/rest/projects/id:{project['id']}", missing=True,
                          params={"fields": "id,name,description,parentProjectId,parentProject(id)"}))
        _owned(item, _marker(plan, "tc-project", project["id"], project), project["id"])
        if item and item.get("parentProjectId", item.get("parentProject", {}).get("id")) != project["parent_id"]:
            raise ProvisionError(f"TeamCity project parent mismatch for {project['id']}")
        state["tc_projects"][project["id"]] = item
    used_repos = {slug for config in plan["configs"] for slug in config["repository_slugs"]}
    for repo in plan["repositories"]:
        if repo["slug"] not in used_repos:
            continue
        identity = vcs_id(repo["slug"])
        item = _json(_call(tc, "GET", f"/app/rest/vcs-roots/id:{identity}", missing=True,
                          params={"fields": "id,name,project(id),properties(property(name,value))"}))
        if item and (item.get("name") != _marker(plan, "vcs", identity, {"slug": repo["slug"], "branch": repo["branch"]}) or item.get("project", {}).get("id") != "LabMatrix"
                     or _props(item).get("url") != f"https://bitbucket.org/{workspace}/{repo['slug']}.git"
                     or _props(item).get("branch") != f"refs/heads/{repo['branch']}"
                     or _props(item).get("authMethod") != "PASSWORD"
                     or _props(item).get("username") != "x-bitbucket-api-token-auth"):
            raise ProvisionError(f"VCS root ownership/checkout mismatch for {identity}")
        state["vcs"][identity] = item
    for config in plan["configs"]:
        item = _json(_call(tc, "GET", f"/app/rest/buildTypes/id:{config['id']}", missing=True,
                          params={"fields": "id,name,description,projectId,project(id),paused,steps(step(type,disabled,properties(property(name,value)))),vcs-root-entries(vcs-root-entry(id,checkout-rules,vcs-root(id))),snapshot-dependencies(snapshot-dependency(source-buildType(id))),settings(property(name,value))"}))
        _owned(item, _marker(plan, "config", config["id"], config), config["id"])
        if item and item.get("projectId", item.get("project", {}).get("id")) != config["project_id"]:
            raise ProvisionError(f"Configuration project mismatch for {config['id']}")
        matches = bool(item) and _config_matches(item, config)
        if item and not matches:
            for endpoint, locator in (("builds", f"buildType:(id:{config['id']}),state:running,defaultFilter:false"), ("buildQueue", f"buildType:(id:{config['id']})")):
                active = _json(_call(tc, "GET", f"/app/rest/{endpoint}", params={"locator": locator, "fields": "count"}))
                if active.get("count", 0):
                    raise ProvisionError(f"Managed configuration {config['id']} has active builds; finish them before reconciling")
        state["configs"][config["id"]] = {"existing": item, "matches": matches}
    missing_configs = sum(not value["existing"] for value in state["configs"].values())
    if not licensing.get("unlimitedBuildTypes") and (not isinstance(licensing.get("buildTypesLeft"), int) or licensing["buildTypesLeft"] < missing_configs):
        raise ProvisionError(f"Insufficient verified TeamCity capacity for {missing_configs} new configurations")
    report = {"mode": "apply" if apply else "plan", "workspace": workspace,
              "newProjects": sum(item is None for item in state["projects"].values()),
              "newRepositories": sum(item["existing"] is None for item in state["repositories"].values()),
              "newTcProjects": sum(item is None for item in state["tc_projects"].values()),
              "newConfigurations": missing_configs,
              "configure": [identity for identity, value in state["configs"].items() if not value["matches"]],
              "existingCheckoutTokensUnchanged": sorted(identity for identity, value in state["vcs"].items() if value is not None)}
    if report["existingCheckoutTokensUnchanged"]:
        report["warnings"] = ["Existing TeamCity VCS checkout tokens are not updated by this utility. Rotate their credentials in TeamCity separately; changing the env token only changes the read-access probe."]
    if not apply:
        return report

    for project in plan["projects"]:
        if state["projects"][project["key"]] is None:
            _call(bb, "POST", f"/workspaces/{workspace}/projects", json={**project, "description": _marker(plan, "bb-project", project["key"], project)})
    for repo in plan["repositories"]:
        base = f"/repositories/{workspace}/{repo['slug']}"
        previous = state["repositories"][repo["slug"]]
        if previous["existing"] is None:
            _call(bb, "POST", base, json={"scm": "git", "is_private": True, "project": {"key": repo["project_key"]}, "description": _marker(plan, "repository", repo["slug"], repo)})
        if not previous["filled"] and repo["files"]:
            # Absolute multipart part names keep file names separate from API form fields.
            _call(bb, "POST", f"{base}/src", data={"branch": repo["branch"], "message": "Add managed Artifact Graph lab fixtures"},
                  files={"/" + filename: (Path(filename).name, content.encode(), "application/octet-stream") for filename, content in repo["files"].items()})
        if repo["files"] and ((previous["existing"] or {}).get("mainbranch") or {}).get("name") != repo["branch"]:
            _call(bb, "PUT", base, json={"mainbranch": {"name": repo["branch"]}})
        if repo["slug"] in used_repos:
            verify_git_read(workspace, repo["slug"], checkout_token, runner=git_runner, require_heads=bool(repo["files"]))
    for project in _ordered(plan["tc_projects"], "parent_id", external={"_Root"}):
        if state["tc_projects"][project["id"]] is None:
            _call(tc, "POST", "/app/rest/projects", json={"id": project["id"], "name": project["name"], "parentProject": {"id": project["parent_id"]}, "description": _marker(plan, "tc-project", project["id"], project)})
            # TeamCity's project creation endpoint does not persist description.
            _call(tc, "PUT", f"/app/rest/projects/id:{project['id']}/description",
                  content=_marker(plan, "tc-project", project["id"], project),
                  headers={"Content-Type": "text/plain", "Accept": "text/plain"})
    for repo in plan["repositories"]:
        identity = vcs_id(repo["slug"])
        if repo["slug"] not in used_repos or state["vcs"][identity] is not None:
            continue
        properties = {"url": f"https://bitbucket.org/{workspace}/{repo['slug']}.git", "branch": f"refs/heads/{repo['branch']}",
                      "authMethod": "PASSWORD", "username": "x-bitbucket-api-token-auth", "secure:password": checkout_token}
        _call(tc, "POST", "/app/rest/vcs-roots", json={"id": identity, "name": _marker(plan, "vcs", identity, {"slug": repo["slug"], "branch": repo["branch"]}),
                                                       "vcsName": "jetbrains.git", "project": {"id": "LabMatrix"}, "properties": {"property": [{"name": name, "value": value} for name, value in properties.items()]}})
    for config in plan["configs"]:
        if state["configs"][config["id"]]["existing"] is None:
            _call(tc, "POST", "/app/rest/buildTypes", json={"id": config["id"], "name": config["name"], "project": {"id": config["project_id"]},
                                                            "description": _marker(plan, "config", config["id"], config), "paused": True})
    for config in _ordered(plan["configs"], "dependencies"):
        if state["configs"][config["id"]]["matches"]:
            continue
        base = f"/app/rest/buildTypes/id:{config['id']}"
        _call(tc, "PUT", f"{base}/paused", content="true", headers={"Content-Type": "text/plain", "Accept": "text/plain"})
        _call(tc, "PUT", f"{base}/vcs-root-entries", json=_entries(config))
        _call(tc, "PUT", f"{base}/steps", json={"step": [command_line_script_step(config["script"])]})
        _call(tc, "PUT", f"{base}/snapshot-dependencies", json={"snapshot-dependency": [{"type": "snapshot_dependency", "source-buildType": {"id": source}} for source in config["dependencies"]]})
        _call(tc, "PUT", f"{base}/settings/artifactRules", content=config["artifacts"], headers={"Content-Type": "text/plain", "Accept": "text/plain"})
        _call(tc, "PUT", f"{base}/paused", content=str(bool(config.get("paused", False))).lower(), headers={"Content-Type": "text/plain", "Accept": "text/plain"})
    if reader_username:
        _call(tc, "PUT", f"/app/rest/users/username:{quote(reader_username, safe='')}/roles/PROJECT_VIEWER/p:LabMatrix")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--plan", action="store_true", help="Read-only preflight (default)")
    action.add_argument("--apply", action="store_true", help="Create/reconcile only managed lab fixtures")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--checkout-token-env", default="BB_CHECKOUT_TOKEN", help="Explicitly select BITBUCKET_TOKEN to reuse a verified read-only collector token")
    parser.add_argument("--checkout-probe-repo", default="java-maven-api")
    args = parser.parse_args(argv)
    try:
        if args.checkout_token_env not in {"BB_CHECKOUT_TOKEN", "BITBUCKET_TOKEN"}:
            raise ProvisionError("Checkout token env must be BB_CHECKOUT_TOKEN or explicit BITBUCKET_TOKEN")
        env_path = Path(args.env_file)
        if not env_path.is_file():
            raise ProvisionError("Requested env file does not exist")
        if env_path.name == ".env.prod":
            raise ProvisionError("Lab expansion cannot use .env.prod")
        values = {**dotenv_values(env_path, interpolate=False), **os.environ}
        if values.get("DEPLOYMENT_MODE") == "production":
            raise ProvisionError("Lab expansion cannot use a production environment")
        workspace = values.get("BITBUCKET_WORKSPACE", "")
        email = values.get("BB_BOOTSTRAP_EMAIL", "")
        if not email or "@" not in email:
            raise ProvisionError("BB_BOOTSTRAP_EMAIL must be set")
        from app.lab_matrix import build_plan
        plan = build_plan(workspace)
        bootstrap_token = values.get("BB_BOOTSTRAP_TOKEN", "")
        tc_token = values.get("TC_ADMIN_TOKEN", "")
        with (httpx.Client(base_url="https://api.bitbucket.org/2.0", auth=(email, bootstrap_token), headers={"Accept": "application/json"}, timeout=120, follow_redirects=False) as bb,
              httpx.Client(base_url=values.get("TEAMCITY_BOOTSTRAP_URL", "http://localhost:8111"), headers={"Authorization": f"Bearer {tc_token}", "Accept": "application/json"}, timeout=60, follow_redirects=False) as tc):
            report = provision_plan(plan, bb, tc, workspace=workspace, bootstrap_token=bootstrap_token,
                                    checkout_token=values.get(args.checkout_token_env, ""), tc_admin_token=tc_token,
                                    reader_username=values.get("TEAMCITY_READER_USERNAME", ""), checkout_probe_repo=args.checkout_probe_repo, apply=args.apply)
        print(json.dumps(report, sort_keys=True))
        return 0
    except ProvisionError as error:
        print(str(error), file=sys.stderr)
        return 2
    except (KeyError, TypeError, ValueError, OSError):
        print("Invalid lab plan or environment; no credential values are logged", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
