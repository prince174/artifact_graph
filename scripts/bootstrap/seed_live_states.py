#!/usr/bin/env python3
"""Create short-lived TeamCity states and verify that the graph sees them.

This is an integration utility, not part of the graph service. It requires an
administrative TeamCity token because it temporarily edits build steps. All
changed settings are restored and all builds started by the utility are
cancelled/dequeued in ``finally``.
"""

import argparse
import os
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.teamcity_setup import command_line_script_step


def get_steps(client, build_type_id: str) -> dict:
    response = client.get(
        f"/app/rest/buildTypes/id:{build_type_id}/steps",
        params={"fields": "step(id,name,type,disabled,properties(property(name,value)))"},
    )
    response.raise_for_status()
    return response.json()


def put_steps(client, build_type_id: str, steps: dict) -> None:
    response = client.put(f"/app/rest/buildTypes/id:{build_type_id}/steps", json=steps)
    response.raise_for_status()


def get_paused(client, build_type_id: str) -> bool:
    response = client.get(
        f"/app/rest/buildTypes/id:{build_type_id}",
        params={"fields": "paused"},
    )
    response.raise_for_status()
    return bool(response.json().get("paused"))


def set_paused(client, build_type_id: str, paused: bool) -> None:
    response = client.put(
        f"/app/rest/buildTypes/id:{build_type_id}/paused",
        content=str(paused).lower(),
        headers={"Content-Type": "text/plain", "Accept": "text/plain"},
    )
    response.raise_for_status()


def queue_build(client, build_type_id: str) -> int:
    response = client.post("/app/rest/buildQueue", json={"buildType": {"id": build_type_id}})
    response.raise_for_status()
    return int(response.json()["id"])


def build_state(client, build_id: int) -> dict:
    response = client.get(
        f"/app/rest/builds/id:{build_id}",
        params={"fields": "id,buildTypeId,state,status"},
    )
    response.raise_for_status()
    return response.json()


def wait_for(client, build_id: int, predicate, timeout: int, interval: float = 1.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        build = build_state(client, build_id)
        if predicate(build):
            return build
        time.sleep(interval)
    raise TimeoutError(f"Build {build_id} did not reach the expected state")


def cancel_build(client, build_id: int) -> None:
    build = build_state(client, build_id)
    if build.get("state") == "queued":
        response = client.delete(f"/app/rest/buildQueue/id:{build_id}")
    elif build.get("state") == "running":
        response = client.post(
            f"/app/rest/builds/id:{build_id}",
            json={"comment": "artifact-graph live-state cleanup", "readdIntoQueue": False},
        )
    else:
        return
    response.raise_for_status()


def trigger_refresh(graph_client) -> None:
    response = graph_client.post("/api/refresh")
    response.raise_for_status()


def wait_for_graph(graph_client, predicate, timeout: int, interval: float = 1.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        graph = graph_client.get("/api/graph").raise_for_status().json()
        if predicate(graph):
            return graph
        time.sleep(interval)
    raise TimeoutError("Graph did not expose the expected live states")


def build_nodes(graph: dict, build_type_id: str) -> list[dict]:
    return [node for node in graph.get("nodes", []) if node.get("kind") == "build" and node.get("buildTypeId") == build_type_id]


def seed_live_states(
    tc_client,
    graph_client,
    *,
    failed_config: str,
    paused_config: str,
    active_config: str,
    timeout: int = 180,
) -> dict:
    """Seed failed/paused/running/queued states, validate them, then restore.

    ``active_config`` temporarily receives a long-running step. Two builds are
    queued: with a single TeamCity agent this yields one running and one queued.
    """
    originals = {
        failed_config: get_steps(tc_client, failed_config),
        active_config: get_steps(tc_client, active_config),
    }
    original_paused = get_paused(tc_client, paused_config)
    started: list[int] = []
    try:
        put_steps(tc_client, failed_config, {"step": [command_line_script_step("echo intentional failure\nexit 1")]})
        failed_id = queue_build(tc_client, failed_config)
        started.append(failed_id)
        wait_for(
            tc_client,
            failed_id,
            lambda build: build.get("state") == "finished" and build.get("status") == "FAILURE",
            timeout,
        )

        set_paused(tc_client, paused_config, True)
        put_steps(tc_client, active_config, {"step": [command_line_script_step("echo live-state fixture\nsleep 300")]})
        running_id = queue_build(tc_client, active_config)
        started.append(running_id)
        wait_for(tc_client, running_id, lambda build: build.get("state") == "running", timeout)
        queued_id = queue_build(tc_client, active_config)
        started.append(queued_id)
        wait_for(tc_client, queued_id, lambda build: build.get("state") == "queued", timeout)

        trigger_refresh(graph_client)

        def expected(graph):
            failed = build_nodes(graph, failed_config)
            active = build_nodes(graph, active_config)
            paused = next(
                (node for node in graph.get("nodes", []) if node.get("id") == f"build-type:{paused_config}"),
                {},
            )
            return (
                any(node.get("status") == "FAILURE" for node in failed)
                and any(node.get("state") == "running" for node in active)
                and any(node.get("state") == "queued" for node in active)
                and paused.get("active") is False
            )

        graph = wait_for_graph(graph_client, expected, timeout)
        return {
            "failedBuild": failed_id,
            "runningBuild": running_id,
            "queuedBuild": queued_id,
            "graphNodes": len(graph.get("nodes", [])),
        }
    finally:
        for build_id in reversed(started):
            try:
                cancel_build(tc_client, build_id)
            except httpx.HTTPError:
                pass
        put_steps(tc_client, failed_config, originals[failed_config])
        put_steps(tc_client, active_config, originals[active_config])
        set_paused(tc_client, paused_config, original_paused)
        trigger_refresh(graph_client)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--failed-config", required=True)
    parser.add_argument("--paused-config", required=True)
    parser.add_argument("--active-config", required=True)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    tc_url = os.getenv("TEAMCITY_BOOTSTRAP_URL", "http://localhost:8111")
    graph_url = os.getenv("GRAPH_URL", "http://localhost:18081")
    token = os.environ["TC_ADMIN_TOKEN"]
    with (
        httpx.Client(
            base_url=tc_url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=30,
        ) as tc_client,
        httpx.Client(base_url=graph_url, timeout=30) as graph_client,
    ):
        result = seed_live_states(
            tc_client,
            graph_client,
            failed_config=args.failed_config,
            paused_config=args.paused_config,
            active_config=args.active_config,
            timeout=args.timeout,
        )
        print("live state profile verified: " + ", ".join(f"{key}={value}" for key, value in result.items()))


if __name__ == "__main__":
    main()
