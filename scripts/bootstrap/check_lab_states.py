#!/usr/bin/env python3
"""Verify temporary running/queued/paused states only in the owned LabMatrix lab.

Default mode checks prerequisites without changing TeamCity. --apply temporarily
changes Composite/Beta, validates graph searches, then restores all settings and
cancels only builds created by this invocation. Cancelled history remains.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.teamcity_setup import command_line_script_step
from scripts.bootstrap.expand_lab import ProvisionError, _call, _config_matches, _json, _marker, _token, _validate_plan


ACTIVE = "LabMatrix_Shared_Composite"
PAUSED = "LabMatrix_Shared_Beta"
FAILED = "LabMatrix_Negative_FailedPush"
QUERIES = ("lab-maven", "lab-empty", "lab-negative")
TEMPORARY_SCRIPT = "set -eu\necho 'Artifact Graph temporary live-state fixture'\nsleep 300"
TEXT_HEADERS = {"Content-Type": "text/plain", "Accept": "text/plain"}


class StateCheckError(RuntimeError):
    pass


def _safe_error(error):
    return str(error) if isinstance(error, (ProvisionError, StateCheckError)) else type(error).__name__


def _local(client, label, service):
    url = urlsplit(str(client.base_url))
    if url.scheme not in {"http", "https"} or url.hostname not in {"localhost", "127.0.0.1", "::1", service} or url.username or url.password or url.query or url.fragment:
        raise StateCheckError(f"{label} must target the local lab")


def _authenticate(graph, username, password):
    _token(username, "WEB_USERNAME")
    _token(password, "WEB_PASSWORD")
    probe = _call(graph, "GET", "/api/version", allowed=(401,))
    if probe.status_code == 401:
        _call(graph, "POST", "/login", allowed=(302, 303), data={"username": username, "password": password}, follow_redirects=False)
    session = _json(_call(graph, "GET", "/api/session"))
    csrf = session.get("csrf")
    if not isinstance(csrf, str) or not csrf:
        raise StateCheckError("Graph did not provide an authenticated CSRF session")
    graph.headers["X-CSRF-Token"] = csrf


def _put_steps(tc, steps):
    _call(tc, "PUT", f"/app/rest/buildTypes/id:{ACTIVE}/steps", json=steps)


def _set_paused(tc, paused):
    _call(tc, "PUT", f"/app/rest/buildTypes/id:{PAUSED}/paused", content=str(paused).lower(), headers=TEXT_HEADERS)


def _state(tc, build_id):
    fields = {"fields": "id,buildTypeId,state,status"}
    queued = _call(tc, "GET", f"/app/rest/buildQueue/id:{build_id}", missing=True, params=fields)
    if queued is not None:
        # TeamCity can still resolve a queue locator after the build starts or
        # finishes. The response state, not the endpoint, is authoritative.
        current = _json(queued)
    else:
        build = _call(tc, "GET", f"/app/rest/builds/id:{build_id}", missing=True, params=fields)
        if build is None:
            return {"id": build_id, "state": "removed"}
        current = _json(build)
    if not isinstance(current, dict) or current.get("state") not in ("queued", "running", "finished"):
        raise StateCheckError(f"Build {build_id} returned an invalid state; refusing changes")
    if str(current.get("id")) != str(build_id) or current.get("buildTypeId") != ACTIVE:
        raise StateCheckError(f"Build {build_id} no longer belongs to the temporary fixture; refusing changes")
    return current


def _wait_build(tc, build_id, predicate, timeout, interval):
    deadline = time.monotonic() + timeout
    while True:
        state = _state(tc, build_id)
        if predicate(state):
            return state
        if time.monotonic() >= deadline:
            raise StateCheckError(f"Build {build_id} did not reach the expected state")
        time.sleep(interval)


def _queue(tc):
    result = _json(_call(tc, "POST", "/app/rest/buildQueue", params={"fields": "id,buildTypeId"}, json={"buildType": {"id": ACTIVE}, "comment": {"text": "Artifact Graph temporary live-state acceptance"}}))
    identity = result.get("id")
    if isinstance(identity, bool) or not str(identity).isdigit() or int(identity) <= 0 or result.get("buildTypeId") != ACTIVE:
        raise StateCheckError("TeamCity did not identify the queued fixture build; inspect its queue manually")
    return int(identity)


def _cancel_created(tc, build_id, timeout, interval):
    current = _state(tc, build_id)
    if current["state"] == "queued":
        response = _call(tc, "DELETE", f"/app/rest/buildQueue/id:{build_id}", missing=True)
        if response is not None:
            _wait_build(tc, build_id, lambda item: item["state"] not in {"running", "queued"}, min(timeout, 60), interval)
            return
        # The queue item may have started between GET and DELETE.
        current = _state(tc, build_id)
    if current["state"] == "running":
        _call(tc, "POST", f"/app/rest/builds/id:{build_id}", json={"comment": "Artifact Graph live-state cleanup", "readdIntoQueue": False})
        _wait_build(tc, build_id, lambda item: item["state"] not in {"running", "queued"}, min(timeout, 60), interval)


def _refresh(graph, timeout, interval):
    """Wait for a newly requested successful scan, including cleanup refreshes."""
    deadline = time.monotonic() + timeout
    while True:
        previous = _json(_call(graph, "GET", "/api/status")).get("lastScan") or {}
        response = _call(graph, "POST", "/api/refresh", allowed=(409,))
        while True:
            status = _json(_call(graph, "GET", "/api/status"))
            scan = status.get("lastScan") or {}
            changed = scan.get("finishedAt") and scan["finishedAt"] != previous.get("finishedAt")
            if changed and scan.get("status") in {"failed", "degraded"}:
                raise StateCheckError("Graph refresh failed or is degraded")
            if changed and status.get("mode") == "live" and scan.get("status") == "success":
                break
            if time.monotonic() >= deadline:
                raise StateCheckError("Graph refresh did not complete successfully")
            time.sleep(interval)
        if response.status_code != 409:
            return
        # A scan already in progress might precede our TC changes. Request our
        # own fresh scan after it finishes instead of accepting old snapshots.
        if time.monotonic() >= deadline:
            raise StateCheckError("Graph remained busy during refresh")


def _graph_states(graph, running_id, queued_id, failed_id):
    nodes = {}
    for query in QUERIES:
        result = _json(_call(graph, "GET", "/api/graph", params={"q": query}))
        if any(item.get("stale") or item.get("collectionError") or item.get("mappingUnavailable") for item in result.get("nodes", [])):
            return False
        nodes.update({item["id"]: item for item in result.get("nodes", [])})
    running = nodes.get(f"build:{running_id}", {})
    queued = nodes.get(f"build:{queued_id}", {})
    failed = nodes.get(f"build:{failed_id}", {})
    paused = nodes.get(f"build-type:{PAUSED}", {})
    return (
        running.get("buildTypeId") == ACTIVE and running.get("state") == "running"
        and running.get("visualReason") == "running"
        and not running.get("hasImagePush") and not running.get("hasSbom")
        and queued.get("buildTypeId") == ACTIVE and queued.get("state") == "queued"
        and queued.get("visualReason") == "queued"
        and not queued.get("hasImagePush") and not queued.get("hasSbom")
        and failed.get("buildTypeId") == FAILED and failed.get("status") == "FAILURE"
        and failed.get("state") == "finished" and failed.get("visualReason") == "failed"
        and not failed.get("hasImagePush") and not failed.get("hasSbom")
        and paused.get("active") is False and paused.get("visualReason") == "inactive"
    )


def _verify_restoration(tc, original):
    for identity in (ACTIVE, PAUSED):
        current = _json(_call(tc, "GET", f"/app/rest/buildTypes/id:{identity}", params={"fields": "id,paused,steps(step(id,name,type,disabled,properties(property(name,value))))"}))
        if bool(current.get("paused")) != bool(original[identity].get("paused")):
            raise StateCheckError(f"Paused state restoration could not be verified for {identity}")
        # TeamCity may assign new step IDs when replacing a collection.
        def settings(steps):
            return [{"type": step.get("type"), "name": step.get("name"), "disabled": bool(step.get("disabled")),
                     "properties": {item["name"]: item.get("value", "") for item in step.get("properties", {}).get("property", [])}}
                    for step in steps.get("step", [])]
        if settings(current.get("steps", {})) != settings(original[identity].get("steps", {})):
            raise StateCheckError(f"Step restoration could not be verified for {identity}")


def check_lab_states(plan, tc, graph, *, username, password, apply=False, timeout=180, interval=1.0):
    """Return a prerequisite/verification report; always attempt every cleanup."""
    if timeout < 1 or not 0 <= interval <= 5:
        raise StateCheckError("Timeout must be positive; polling interval must be between 0 and 5 seconds")
    _validate_plan(plan, plan.get("workspace", ""))
    _local(tc, "TeamCity", "teamcity")
    _local(graph, "Graph", "graph")
    _authenticate(graph, username, password)
    by_id = {config["id"]: config for config in plan["configs"]}
    original = {}
    for identity in (ACTIVE, PAUSED, FAILED):
        expected = by_id.get(identity)
        if expected is None:
            raise StateCheckError(f"Plan does not contain required fixture {identity}")
        current = _json(_call(tc, "GET", f"/app/rest/buildTypes/id:{identity}", params={"fields": "id,description,projectId,project(id),paused,steps(step(id,name,type,disabled,properties(property(name,value)))),vcs-root-entries(vcs-root-entry(id,checkout-rules,vcs-root(id))),snapshot-dependencies(snapshot-dependency(source-buildType(id))),settings(property(name,value))"}))
        if (current.get("id") != identity or current.get("description") != _marker(plan, "config", identity, expected)
                or current.get("projectId", current.get("project", {}).get("id")) != expected["project_id"]
                or not _config_matches(current, expected)):
            raise StateCheckError(f"Ownership or settings mismatch for {identity}; refusing edits")
        original[identity] = current
    for endpoint, locator in (("builds", "state:running,defaultFilter:false"), ("buildQueue", "count:1")):
        active = _json(_call(tc, "GET", f"/app/rest/{endpoint}", params={"locator": locator, "fields": "count"}))
        count = active.get("count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise StateCheckError("TeamCity returned an invalid active build count")
        if count:
            raise StateCheckError("TeamCity must have no running or queued builds before the state check")
    agents = _json(_call(tc, "GET", "/app/rest/agents", params={"locator": "authorized:true,connected:true", "fields": "agent(id,authorized,connected,enabled)"})).get("agent", [])
    if len(agents) != 1 or not all(agents[0].get(field) is True for field in ("authorized", "connected", "enabled")):
        raise StateCheckError("State check requires exactly one connected, authorized and enabled agent")
    failures = _json(_call(tc, "GET", "/app/rest/builds", params={"locator": f"buildType:(id:{FAILED}),state:finished,status:FAILURE,defaultFilter:false,count:1", "fields": "build(id,buildTypeId,state,status)"})).get("build", [])
    if not failures or failures[0].get("status") != "FAILURE" or failures[0].get("state") != "finished" or failures[0].get("buildTypeId") != FAILED:
        raise StateCheckError("Seed a completed Negative_FailedPush failure before the live-state check")
    failed_id = int(failures[0]["id"])
    report = {"mode": "apply" if apply else "plan", "agentId": agents[0]["id"], "failedBuildId": failed_id,
              "activeConfiguration": ACTIVE, "pausedConfiguration": PAUSED, "graphQueries": list(QUERIES)}
    if not apply:
        return report

    created = []
    primary_error = None
    cleanup_errors = []
    try:
        _set_paused(tc, True)
        _put_steps(tc, {"step": [command_line_script_step(TEMPORARY_SCRIPT)]})
        running_id = _queue(tc)
        created.append(running_id)
        _wait_build(tc, running_id, lambda item: item.get("state") == "running", timeout, interval)
        queued_id = _queue(tc)
        if queued_id in created:
            raise StateCheckError("TeamCity reused a temporary build ID instead of creating a second build")
        created.append(queued_id)
        _wait_build(tc, queued_id, lambda item: item.get("state") == "queued", timeout, interval)
        _refresh(graph, timeout, interval)
        deadline = time.monotonic() + timeout
        while not _graph_states(graph, running_id, queued_id, failed_id):
            if time.monotonic() >= deadline:
                raise StateCheckError("Graph searches did not expose the expected running/queued/paused/failed states")
            time.sleep(interval)
        report.update(runningBuildId=running_id, queuedBuildId=queued_id, verified=True,
                      visualReasons=["running", "queued", "failed", "inactive"],
                      expectedColors={"running": "#f2d675", "queued": "#f2d675", "failed": "#e99a95", "inactive": "#7d8590"})
    except (Exception, KeyboardInterrupt) as error:
        primary_error = error
    finally:
        cleanup_actions = [(f"cancel build {build_id}", lambda build_id=build_id: _cancel_created(tc, build_id, timeout, interval)) for build_id in reversed(created)]
        cleanup_actions.extend([
            ("restore Composite steps", lambda: _put_steps(tc, original[ACTIVE]["steps"])),
            ("restore Beta paused state", lambda: _set_paused(tc, bool(original[PAUSED].get("paused")))),
            ("verify restored settings", lambda: _verify_restoration(tc, original)),
            ("refresh graph after cleanup", lambda: _refresh(graph, timeout, interval)),
        ])
        for action, operation in cleanup_actions:
            try:
                operation()
            except Exception as error:
                cleanup_errors.append(f"{action}: {_safe_error(error)}")
    if primary_error or cleanup_errors:
        details = [_safe_error(primary_error)] if primary_error else []
        details.extend(cleanup_errors)
        raise StateCheckError("; ".join(details) + f"; created build IDs: {created}") from None
    report["cleanup"] = "completed"
    report["cancelledHistoryRemains"] = True
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--plan", action="store_true", help="Read-only prerequisite check (default)")
    modes.add_argument("--apply", action="store_true", help="Temporarily modify only managed state-check fixtures")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args(argv)
    try:
        env_path = Path(args.env_file)
        if not env_path.is_file() or env_path.name == ".env.prod":
            raise StateCheckError("An existing non-production lab env file is required")
        values = {**dotenv_values(env_path, interpolate=False), **os.environ}
        if values.get("DEPLOYMENT_MODE") == "production":
            raise StateCheckError("State checks cannot use a production environment")
        admin_token = values.get("TC_ADMIN_TOKEN", "")
        _token(admin_token, "TC_ADMIN_TOKEN")
        from app.lab_matrix import build_plan
        plan = build_plan(values.get("BITBUCKET_WORKSPACE", "artifact_graph"))
        with (httpx.Client(base_url=values.get("TEAMCITY_BOOTSTRAP_URL", "http://localhost:8111"), headers={"Authorization": f"Bearer {admin_token}", "Accept": "application/json"}, timeout=30, follow_redirects=False) as tc,
              httpx.Client(base_url=values.get("GRAPH_URL", f"http://localhost:{values.get('GRAPH_PORT', '18082')}"), timeout=30, follow_redirects=False) as graph):
            report = check_lab_states(plan, tc, graph, username=values.get("WEB_USERNAME", "root"), password=values.get("WEB_PASSWORD", ""), apply=args.apply, timeout=args.timeout)
        print(json.dumps(report, sort_keys=True))
        return 0
    except Exception as error:
        print(_safe_error(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
