#!/usr/bin/env python3
"""Fill only owned LabMatrix build histories; default --plan never queues builds."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

import httpx
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.lab_matrix import build_plan
from app.upstream_urls import resolve_api_url
from scripts.bootstrap.expand_lab import _config_matches, _marker, _validate_plan, ProvisionError


class RunError(RuntimeError):
    """A safe operational error which never includes credentials or response bodies."""


DETAIL_FIELDS = "id,name,description,projectId,paused,steps(step(type,properties(property(name,value)))),vcs-root-entries(vcs-root-entry(id,checkout-rules,vcs-root(id))),snapshot-dependencies(snapshot-dependency(source-buildType(id))),settings(property(name,value))"


def request_json(client, method, path, **kwargs):
    response = client.request(method, resolve_api_url(str(client.base_url), path), **kwargs)
    if response.status_code not in range(200, 300):
        raise RunError(f"TeamCity {method} request failed: HTTP {response.status_code}")
    try:
        value = response.json()
    except ValueError:
        raise RunError("TeamCity returned invalid JSON") from None
    if not isinstance(value, dict):
        raise RunError("TeamCity returned an unexpected response type")
    return value


def selected_configs(plan, config_ids=None):
    _validate_plan(plan, plan["workspace"])
    by_id = {config["id"]: config for config in plan["configs"]}
    chosen = set(config_ids or by_id)
    if not chosen or not chosen <= by_id.keys():
        raise RunError("Only configuration IDs present in the LabMatrix plan are allowed")
    return [config for config in plan["configs"] if config["id"] in chosen]


def preflight(client, plan, configs):
    url = urlsplit(str(client.base_url))
    if url.scheme not in {"http", "https"} or url.hostname not in {"localhost", "127.0.0.1", "::1", "teamcity"} or url.username or url.password or url.query or url.fragment:
        raise RunError("Build scheduling is restricted to the local TeamCity lab")
    # TeamCity may schedule snapshot dependencies automatically. Verify their
    # ownership and exact configuration too, before issuing the first POST.
    by_id = {config["id"]: config for config in plan["configs"]}
    required, pending = set(), [config["id"] for config in configs]
    while pending:
        identity = pending.pop()
        if identity not in required:
            required.add(identity)
            pending.extend(by_id[identity]["dependencies"])
    for identity in sorted(required):
        config = by_id[identity]
        existing = request_json(client, "GET", f"/app/rest/buildTypes/id:{identity}", params={"fields": DETAIL_FIELDS})
        if existing.get("id") != identity or existing.get("projectId") != config["project_id"] or existing.get("description") != _marker(plan, "config", identity, config) or not _config_matches(existing, config):
            raise RunError(f"Ownership or configuration mismatch for {identity}; no builds were queued")
        if existing.get("paused"):
            raise RunError(f"Configuration {identity} is paused; it will not be activated by this utility")


def active_count(client, identity):
    running = request_json(client, "GET", "/app/rest/builds", params={"locator": f"buildType:(id:{identity}),state:running,defaultFilter:false", "fields": "count"})
    queued = request_json(client, "GET", "/app/rest/buildQueue", params={"locator": f"buildType:(id:{identity})", "fields": "count"})
    counts = [running.get("count"), queued.get("count")]
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in counts):
        raise RunError("TeamCity returned an invalid active build count")
    return sum(counts)


def latest_builds(client, identity):
    result = request_json(client, "GET", "/app/rest/builds", params={"locator": f"buildType:(id:{identity}),state:finished,count:5,defaultFilter:false", "fields": "build(id,buildTypeId,status,state)"})
    builds = result.get("build", [])
    if not isinstance(builds, list) or len(builds) > 5 or any(not isinstance(build, dict) or not build.get("id") or build.get("buildTypeId") != identity or build.get("state") != "finished" for build in builds):
        raise RunError(f"Invalid finished-build response for {identity}")
    if len({str(build["id"]) for build in builds}) != len(builds):
        raise RunError(f"Duplicate finished build IDs for {identity}")
    return builds


def run_lab(client, plan, *, apply=False, config_ids=None, min_builds=3, timeout=1800, interval=5, max_new_builds=200, clock=None, sleep=None, progress=None):
    if not 1 <= min_builds <= 5 or timeout <= 0 or not 0 <= interval <= 60 or not 1 <= max_new_builds <= 200:
        raise RunError("Require min-builds 1..5, positive timeout, interval 0..60 and max-new-builds 1..200")
    clock, sleep = clock or time.monotonic, sleep or time.sleep
    configs = selected_configs(plan, config_ids)
    for config in configs:
        expected = config["expected"]
        if expected.get("status") not in {"SUCCESS", "FAILURE"} or not 1 <= max(min_builds, expected.get("min_builds", 0)) <= 5:
            raise RunError("Each fixture requires a SUCCESS/FAILURE status and a target of at most five builds")
    deadline = clock() + timeout
    preflight(client, plan, configs)
    scheduled_ids, attempts = set(), 0
    while True:
        summary = []
        for config in configs:
            if apply and clock() >= deadline:
                raise RunError(f"Build-history timeout; queue requests={attempts}. Existing builds were not cancelled")
            identity = config["id"]
            target = max(min_builds, config["expected"].get("min_builds", 0))
            status = config["expected"]["status"]
            active = active_count(client, identity)
            builds = latest_builds(client, identity)
            matching = sum(build.get("status") == status for build in builds)
            complete = len(builds) >= target and matching == len(builds) and active == 0
            item = {"id": identity, "expectedStatus": status, "target": target, "latestFinished": len(builds), "matching": matching, "active": active, "complete": complete}
            summary.append(item)
            if apply and not complete and active == 0:
                if attempts >= max_new_builds:
                    raise RunError(f"Queue safety limit reached ({max_new_builds}); inspect failing fixtures. Existing builds were not cancelled")
                # Do not mutate settings, authorize agents, or cancel any builds.
                queued = request_json(client, "POST", "/app/rest/buildQueue", json={"buildType": {"id": identity}})
                attempts += 1
                if not queued.get("id"):
                    raise RunError("TeamCity queue response lacks a build ID; inspect the queue before retrying")
                scheduled_ids.add(str(queued["id"]))
                item["queuedThisPass"] = True
        report = {"mode": "apply" if apply else "plan", "fixtureKey": plan["fixture_key"], "complete": all(item["complete"] for item in summary), "queueRequests": attempts, "queuedBuildIds": sorted(scheduled_ids), "configurations": summary}
        if progress:
            progress({"completeConfigurations": sum(item["complete"] for item in summary), "totalConfigurations": len(summary), "queueRequests": attempts})
        if not apply or report["complete"]:
            return report
        if clock() >= deadline:
            raise RunError(f"Build-history timeout; queue requests={attempts}. Existing builds were not cancelled")
        sleep(min(interval, max(deadline - clock(), 0)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true", help="Read-only ownership/history report (default)")
    mode.add_argument("--apply", action="store_true", help="Explicitly allow queueing owned fixture builds")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--config", action="append", help="Select a plan configuration; repeat for several")
    parser.add_argument("--min-builds", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--interval", type=float, default=5)
    parser.add_argument("--max-new-builds", type=int, default=200)
    args = parser.parse_args(argv)
    try:
        if not Path(args.env_file).is_file():
            raise RunError("Environment file is missing")
        values = {**dotenv_values(args.env_file, interpolate=False), **os.environ}
        token = values.get("TC_ADMIN_TOKEN", "")
        if not token or token.lower().startswith("replace") or any(char in token for char in "\r\n"):
            raise RunError("TC_ADMIN_TOKEN is required; the runtime reader token is never used as fallback")
        if args.apply and token == values.get("TEAMCITY_TOKEN"):
            raise RunError("TC_ADMIN_TOKEN must be separate from the runtime reader token")
        workspace = values.get("BITBUCKET_WORKSPACE")
        if not workspace:
            raise RunError("BITBUCKET_WORKSPACE is required")
        url = values.get("TEAMCITY_BOOTSTRAP_URL", "http://localhost:8111")
        with httpx.Client(base_url=url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, timeout=30, follow_redirects=False) as client:
            report = run_lab(client, build_plan(workspace), apply=args.apply, config_ids=args.config, min_builds=args.min_builds, timeout=args.timeout, interval=args.interval, max_new_builds=args.max_new_builds,
                             progress=(lambda value: print(json.dumps(value), file=sys.stderr, flush=True)) if args.apply else None)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (RunError, ProvisionError, httpx.HTTPError, OSError, ValueError, KeyError) as exc:
        message = str(exc) if isinstance(exc, (RunError, ProvisionError)) else type(exc).__name__
        print(json.dumps({"status": "failed", "error": message}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
