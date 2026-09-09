#!/usr/bin/env python3
"""Read-only, manifest-driven validation of the expanded Cloud/TeamCity lab."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import ssl
import sys
import time

import httpx
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.upstream_urls import resolve_api_url
from scripts.validate_live import ensure_authenticated


class LabValidationError(AssertionError):
    """An expected fixture invariant does not match the observed graph."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise LabValidationError(message)


def edge_set(graph: dict) -> set[tuple[str, str, str]]:
    return {(edge["source"], edge["target"], edge["relation"]) for edge in graph["edges"]}


def validate_graph_shape(graph: dict) -> None:
    nodes = graph["nodes"]
    ids = {node["id"] for node in nodes}
    require(len(ids) == len(nodes), "Duplicate graph node IDs")
    require(all(node["kind"] in {"bb_project", "repository", "tc_project", "build_configuration", "build"} for node in nodes), "Graph contains an output or service entity")
    require(not ids.intersection({"tc-project:_Root", "tc-project:Root"}), "Graph contains TeamCity root")
    require(all(not node.get("stale") and not node.get("collectionError") and not node.get("mappingUnavailable") for node in nodes), "Graph contains stale or incomplete entities")
    require(set(graph.get("positions", {})) == ids, "Every graph node must have a position")
    require(all(edge["source"] in ids and edge["target"] in ids for edge in graph["edges"]), "Graph contains a dangling edge")
    require(len(edge_set(graph)) == len(graph["edges"]), "Graph contains duplicate edges")


def _target(expected: dict) -> bool:
    return bool(expected.get("push_tags") or expected.get("sbom_paths"))


def validate_matrix(graph_by_repo: dict[str, dict], tc_builds_by_config: dict[str, list[dict]], plan: dict, min_builds: int = 3) -> dict:
    """Compare all fixture searches to independent TeamCity build records."""
    require(1 <= min_builds <= 100, "min_builds must be between 1 and 100")
    workspace = plan["workspace"]
    configs = plan["configs"]
    configurations = {config["id"]: config for config in configs}
    repositories = {repo["slug"]: repo for repo in plan["repositories"]}
    require(len(configurations) == len(configs), "Manifest contains duplicate configuration IDs")
    require(set(graph_by_repo) == set(repositories), "Search results do not cover exactly the fixture repositories")
    require(set(tc_builds_by_config) == set(configurations), "Direct TeamCity results do not cover the fixture configurations")
    observed_builds: set[str] = set()
    observed_pushes: set[tuple[str, str]] = set()
    observed_sboms: set[tuple[str, str]] = set()
    observed_sources: set[str] = set()
    observed_projects: set[str] = set()
    for slug, repo in repositories.items():
        graph = graph_by_repo[slug]
        validate_graph_shape(graph)
        by_id = {node["id"]: node for node in graph["nodes"]}
        edges = edge_set(graph)
        rid = f"repo:{workspace}/{slug}"
        bpid = f"bb-project:{workspace}/{repo['project_key']}"
        wanted_configs = [config for config in configs if slug in config["repository_slugs"]]
        wanted_projects = {f"tc-project:{config['project_id']}" for config in wanted_configs}
        require({node["id"] for node in by_id.values() if node["kind"] == "repository"} == {rid}, f"Repository search did not isolate {slug}")
        require({node["id"] for node in by_id.values() if node["kind"] == "bb_project"} == {bpid}, f"Incorrect Bitbucket project for {slug}")
        require({node["id"] for node in by_id.values() if node["kind"] == "tc_project"} == wanted_projects, f"Incorrect TeamCity projects for {slug}")
        require({node["id"] for node in by_id.values() if node["kind"] == "build_configuration"} == {f"build-type:{config['id']}" for config in wanted_configs}, f"Search leaked or lost configurations for {slug}")
        repo_target = any(_target(config["expected"]) for config in wanted_configs)
        require(by_id[rid].get("hasTargetOutput") is repo_target, f"Incorrect target-output highlight for {slug}")
        expected_edges = {(bpid, rid, "contains")} | {(rid, pid, "maps_to") for pid in wanted_projects}
        wanted_build_ids: set[str] = set()
        for config in wanted_configs:
            config_id = config["id"]
            cid = f"build-type:{config_id}"
            pid = f"tc-project:{config['project_id']}"
            expected = config["expected"]
            config_node = by_id[cid]
            require(set(config_node.get("mappedRepositoryIds", [])) == {f"repo:{workspace}/{item}" for item in config["repository_slugs"]}, f"Incorrect exact repository mapping for {config_id}")
            require(config_node.get("hasTargetOutput") is _target(expected), f"Incorrect configuration highlight for {config_id}")
            expected_edges.add((pid, cid, "contains"))
            builds = tc_builds_by_config[config_id]
            require(len(builds) >= max(min_builds, expected.get("min_builds", 0)), f"Too few actual TeamCity builds for {config_id}")
            require(len({str(build['id']) for build in builds}) == len(builds), f"Duplicate direct TeamCity builds for {config_id}")
            for build in builds:
                build_id = f"build:{build['id']}"
                wanted_build_ids.add(build_id)
                expected_edges.add((cid, build_id, "ran_as"))
                require(build_id in by_id, f"Graph is missing a current TeamCity build for {config_id}")
                node = by_id[build_id]
                require(build.get("buildTypeId") == config_id, f"TeamCity build belongs to another configuration: {config_id}")
                require(build.get("state") == node.get("state") == "finished", f"Unfinished fixture build for {config_id}")
                require(build.get("status") == node.get("status") == expected["status"], f"Unexpected actual build status for {config_id}")
                actual_images = node.get("pushedImages", [])
                image_tags = {item["image"] for item in actual_images}
                sboms = node.get("sbomArtifacts", [])
                sbom_paths = {item["path"] for item in sboms}
                require(image_tags == set(expected.get("push_tags", [])), f"Incorrect confirmed push tags for {config_id}")
                require(sbom_paths == set(expected.get("sbom_paths", [])), f"Incorrect SBOM artifact paths for {config_id}")
                if expected.get("sbom_statuses"):
                    require({item["path"]: item.get("sbomStatus") for item in sboms} == expected["sbom_statuses"], f"Incorrect SBOM parsing status for {config_id}")
                require(node.get("hasImagePush") is bool(image_tags), f"Incorrect hasImagePush flag for {config_id}")
                require(node.get("hasSbom") is bool(sbom_paths), f"Incorrect hasSbom flag for {config_id}")
                require(all(item.get("evidence") == "teamcity_build_log" for item in actual_images), f"Push lacks build-log evidence for {config_id}")
                sources = {path for item in actual_images for path in ([item["sourcePath"]] if item.get("sourcePath") else []) + (item.get("sourcePaths") or [])}
                if expected.get("source_paths"):
                    require(all(any(path == suffix or path.endswith("/" + suffix) for path in sources) for suffix in expected["source_paths"]), f"Missing command source evidence for {config_id}")
                expected_visual = "failed" if expected["status"] in {"FAILURE", "ERROR"} else "push_and_sbom" if image_tags and sbom_paths else "push" if image_tags else "sbom" if sbom_paths else "success"
                require(node.get("visualReason") == expected_visual, f"Incorrect build highlight for {config_id}")
                observed_builds.add(build_id)
                observed_pushes.update((build_id, tag) for tag in image_tags)
                observed_sboms.update((build_id, path) for path in sbom_paths)
                observed_sources.update(sources)
        require({node["id"] for node in by_id.values() if node["kind"] == "build"} == wanted_build_ids, f"Search contains old or unrelated builds for {slug}")
        require(edges == expected_edges, f"Incorrect graph links for {slug}")
        observed_projects.update(wanted_projects)
    return {
        "fixtureKey": plan.get("fixture_key", ""), "repositories": len(repositories),
        "bbProjects": len({repo["project_key"] for repo in repositories.values()}),
        "tcProjects": len(observed_projects), "configurations": len(configurations),
        "builds": len(observed_builds), "confirmedPushes": len(observed_pushes),
        "sbomArtifacts": len(observed_sboms), "commandSourcePaths": sorted(observed_sources),
    }


def fetch_graph_pages(client: httpx.Client, query: str = "", limit: int = 10) -> list[dict]:
    pages, cursors, cursor, seen_roots = [], set(), "", set()
    for _ in range(1000):
        page = client.get("/api/graph", params={"q": query, "limit": limit, "cursor": cursor}).raise_for_status().json()
        validate_graph_shape(page)
        pages.append(page)
        pagination = page.get("pagination", {})
        require(pagination.get("offset") == (len(pages) - 1) * limit, "Graph pagination skipped an offset")
        require(pagination.get("mode") == ("repositories" if query else "projects"), "Incorrect pagination mode")
        root_kind = "repository" if query else "bb_project"
        roots = {node["id"] for node in page["nodes"] if node["kind"] == root_kind}
        require(not roots.intersection(seen_roots), "Graph pagination repeated selected entities")
        require(len(roots) <= limit, "Graph page exceeds requested limit")
        seen_roots.update(roots)
        if not query:
            require(sum(node["kind"] == "bb_project" for node in page["nodes"]) <= limit, "Default page exceeds project limit")
            kinds = {node["id"]: node["kind"] for node in page["nodes"]}
            children = Counter(edge["source"] for edge in page["edges"] if kinds[edge["source"]] == "bb_project" and kinds[edge["target"]] == "repository")
            require(all(count <= 10 for count in children.values()), "Default page exceeds ten repositories per project")
        if not pagination.get("hasMore"):
            require(not pagination.get("nextCursor"), "Final page unexpectedly has a cursor")
            return pages
        cursor = pagination.get("nextCursor")
        require(isinstance(cursor, str) and cursor and cursor not in cursors, "Graph pagination loop or missing cursor")
        cursors.add(cursor)
    raise LabValidationError("Graph pagination exceeded safety limit")


def merge_pages(pages: list[dict]) -> dict:
    nodes, edges, positions = {}, {}, {}
    for page in pages:
        nodes.update((node["id"], node) for node in page["nodes"])
        edges.update(((edge["source"], edge["target"], edge["relation"]), edge) for edge in page["edges"])
        positions.update(page["positions"])
    return {"nodes": list(nodes.values()), "edges": list(edges.values()), "positions": positions}


def fetch_tc_builds(client: httpx.Client, config_id: str, limit: int) -> list[dict]:
    require(1 <= limit <= 100, "build_limit must be between 1 and 100")
    url = resolve_api_url(str(client.base_url), "/app/rest/builds")
    response = client.get(url, params={"locator": f"buildType:(id:{config_id}),state:finished,count:{limit},defaultFilter:false", "fields": "build(id,number,status,state,finishDate,buildTypeId)"}).raise_for_status().json()
    return response.get("build", [])


def request_refresh(client: httpx.Client, timeout: float = 300, poll_seconds: float = 2) -> None:
    previous = client.get("/api/status").raise_for_status().json().get("lastScan") or {}
    session = client.get("/api/session").raise_for_status().json()
    response = client.post("/api/refresh", headers={"X-CSRF-Token": session["csrf"]})
    require(response.status_code == 202, "Manual refresh was not accepted")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        scan = client.get("/api/status").raise_for_status().json().get("lastScan") or {}
        changed = scan.get("finishedAt") and scan["finishedAt"] != previous.get("finishedAt")
        if changed and scan.get("status") in {"failed", "degraded"}:
            raise LabValidationError("New scan failed or is degraded")
        if changed and scan.get("status") == "success":
            return
        time.sleep(poll_seconds)
    raise LabValidationError("Timed out waiting for a new successful scan")


def validate_live_lab(graph_client: httpx.Client, tc_client: httpx.Client, plan: dict, *, min_builds: int = 3, build_limit: int = 5) -> dict:
    status = graph_client.get("/api/status").raise_for_status().json()
    require(status.get("mode") == "live" and (status.get("lastScan") or {}).get("status") == "success", "Graph must have a successful live scan")
    try:
        finished = datetime.fromisoformat(status["lastScan"]["finishedAt"].replace("Z", "+00:00"))
        finished = finished.replace(tzinfo=timezone.utc) if finished.tzinfo is None else finished
        age = (datetime.now(timezone.utc) - finished).total_seconds()
    except (TypeError, AttributeError, ValueError, KeyError):
        raise LabValidationError("Graph scan freshness cannot be verified") from None
    require(-60 <= age <= max(int(status.get("refreshMinutes", 60)) * 120, 300), "Graph successful scan is too old")
    pages = fetch_graph_pages(graph_client)
    searches = {repo["slug"]: merge_pages(fetch_graph_pages(graph_client, repo["slug"], limit=1)) for repo in plan["repositories"]}
    tc_builds = {config["id"]: fetch_tc_builds(tc_client, config["id"], build_limit) for config in plan["configs"]}
    report = validate_matrix(searches, tc_builds, plan, min_builds)
    default_nodes = merge_pages(pages)["nodes"]
    report.update({"defaultPages": len(pages), "defaultVisibleRepositories": sum(node["kind"] == "repository" for node in default_nodes), "repositorySearches": len(searches), "scanFinishedAt": status["lastScan"].get("finishedAt")})
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--url", default="http://localhost:18082")
    parser.add_argument("--tc-url", help="Host-accessible TeamCity base URL, including context path")
    parser.add_argument("--min-builds", type=int, default=3)
    parser.add_argument("--build-limit", type=int, default=5, help="Must match TEAMCITY_BUILD_LIMIT used by the graph")
    parser.add_argument("--refresh", action="store_true", help="Explicitly request one CSRF-protected graph scan")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--ca-file", help="Optional host path to a corporate CA PEM bundle")
    parser.add_argument("--output", type=Path, help="Create a new local JSON report; existing files are not overwritten")
    args = parser.parse_args(argv)
    try:
        from app.lab_matrix import build_plan

        require(Path(args.env_file).is_file(), "Environment file is missing")
        env = {**dotenv_values(args.env_file, interpolate=False), **os.environ}
        token = env.get("TEAMCITY_TOKEN")
        require(bool(token), "TEAMCITY_TOKEN is required; bootstrap/admin tokens are not used")
        require(bool(env.get("BITBUCKET_WORKSPACE")), "BITBUCKET_WORKSPACE is required")
        require(1 <= args.min_builds <= args.build_limit <= 100, "Require 1 <= min-builds <= build-limit <= 100")
        tc_url = args.tc_url or env.get("TEAMCITY_BOOTSTRAP_URL") or env.get("TEAMCITY_URL")
        require(bool(tc_url), "A host-accessible TeamCity URL is required")
        tls = ssl.create_default_context()
        if args.ca_file:
            tls.load_verify_locations(args.ca_file)
        with httpx.Client(base_url=args.url, timeout=60, verify=tls, follow_redirects=False) as graph_client, httpx.Client(base_url=tc_url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, timeout=60, verify=tls, follow_redirects=False) as tc_client:
            ensure_authenticated(graph_client, env.get("WEB_USERNAME", "root"), env.get("WEB_PASSWORD"))
            if args.refresh:
                request_refresh(graph_client, args.timeout)
            report = validate_live_lab(graph_client, tc_client, build_plan(env["BITBUCKET_WORKSPACE"]), min_builds=args.min_builds, build_limit=args.build_limit)
        report = {"status": "success", "checkedAt": datetime.now(timezone.utc).isoformat(), **report}
        content = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(content)
        print(content, end="")
        return 0
    except (httpx.HTTPError, LabValidationError, OSError, ValueError, KeyError, RuntimeError) as exc:
        # Never dump upstream response bodies, authentication values, or a traceback.
        message = str(exc) if isinstance(exc, LabValidationError) else type(exc).__name__
        print(json.dumps({"status": "failed", "error": message}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
