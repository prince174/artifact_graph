import importlib.util
from pathlib import Path

import httpx


SPEC = importlib.util.spec_from_file_location(
    "seed_live_states",
    Path(__file__).parents[1] / "scripts" / "bootstrap" / "seed_live_states.py",
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_seed_live_states_verifies_graph_and_restores_everything(monkeypatch):
    states = {}
    queued = []
    steps = {"Fail": {"step": [{"name": "original failure"}]}, "Active": {"step": [{"name": "original active"}]}}
    paused = {"Paused": False}
    refreshes = 0

    def tc_handler(request):
        path = request.url.path
        if path.endswith("/steps"):
            config = path.split("id:", 1)[1].split("/", 1)[0]
            if request.method == "GET":
                return httpx.Response(200, json=steps[config])
            steps[config] = __import__("json").loads(request.content)
            return httpx.Response(200, json={})
        if path.endswith("/paused"):
            config = path.split("id:", 1)[1].split("/", 1)[0]
            paused[config] = request.content == b"true"
            return httpx.Response(200, text=str(paused[config]).lower())
        if "/buildTypes/id:" in path:
            config = path.split("id:", 1)[1]
            return httpx.Response(200, json={"paused": paused.get(config, False)})
        if path.endswith("/buildQueue") and request.method == "POST":
            config = __import__("json").loads(request.content)["buildType"]["id"]
            build_id = len(states) + 1
            states[build_id] = {"id": build_id, "buildTypeId": config, "state": "queued"}
            queued.append(build_id)
            return httpx.Response(200, json={"id": build_id})
        if "/buildQueue/id:" in path:
            states[int(path.rsplit(":", 1)[1])]["state"] = "finished"
            return httpx.Response(204)
        if "/builds/id:" in path:
            build_id = int(path.rsplit(":", 1)[1])
            if request.method == "POST":
                states[build_id]["state"] = "finished"
                return httpx.Response(200, json={})
            state = states[build_id]
            if state["buildTypeId"] == "Fail":
                state.update(state="finished", status="FAILURE")
            elif build_id == 2:
                state["state"] = "running"
            return httpx.Response(200, json=state)
        raise AssertionError(f"Unexpected request: {request.method} {path}")

    def graph_handler(request):
        nonlocal refreshes
        if request.url.path == "/api/refresh":
            refreshes += 1
            return httpx.Response(202)
        return httpx.Response(200, json={"nodes": [
            {"id": "build:1", "kind": "build", "buildTypeId": "Fail", "status": "FAILURE", "state": "finished"},
            {"id": "build:2", "kind": "build", "buildTypeId": "Active", "state": "running"},
            {"id": "build:3", "kind": "build", "buildTypeId": "Active", "state": "queued"},
            {"id": "build-type:Paused", "kind": "build_configuration", "active": False},
        ]})

    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    with (
        httpx.Client(transport=httpx.MockTransport(tc_handler), base_url="http://tc") as tc,
        httpx.Client(transport=httpx.MockTransport(graph_handler), base_url="http://graph") as graph,
    ):
        result = module.seed_live_states(
            tc,
            graph,
            failed_config="Fail",
            paused_config="Paused",
            active_config="Active",
            timeout=1,
        )

    assert result["failedBuild"] == 1
    assert steps["Fail"] == {"step": [{"name": "original failure"}]}
    assert steps["Active"] == {"step": [{"name": "original active"}]}
    assert paused["Paused"] is False
    assert all(state["state"] == "finished" for state in states.values())
    assert refreshes == 2


def test_build_nodes_only_returns_selected_configuration():
    graph = {"nodes": [
        {"id": "build:1", "kind": "build", "buildTypeId": "A"},
        {"id": "build:2", "kind": "build", "buildTypeId": "B"},
        {"id": "build-type:A", "kind": "build_configuration"},
    ]}
    assert module.build_nodes(graph, "A") == [graph["nodes"][0]]
