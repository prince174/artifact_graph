from datetime import datetime, timedelta, timezone

from app.filters import filter_graph, parse_teamcity_date


def graph_fixture():
    old = (datetime.now(timezone.utc) - timedelta(days=40)).strftime("%Y%m%dT%H%M%S%z")
    nodes = [
        {"id": "bb", "kind": "bb_project", "label": "Demo"},
        {"id": "r1", "kind": "repository", "label": "API"},
        {"id": "r2", "kind": "repository", "label": "Infra"},
        {"id": "tc1", "kind": "tc_project", "label": "API Builds"},
        {"id": "tc2", "kind": "tc_project", "label": "Infra Builds"},
        {"id": "c1", "kind": "build_configuration", "label": "API build"},
        {"id": "c2", "kind": "build_configuration", "label": "Infra build"},
        {"id": "b1", "kind": "build", "label": "#1", "status": "SUCCESS", "hasImagePush": True, "hasSbom": True, "pushedImages": [{"engine": "docker", "image": "api:1"}], "finishDate": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%z")},
        {"id": "b2", "kind": "build", "label": "#2", "status": "FAILURE", "finishDate": old},
    ]
    edges = [
        {"source": "bb", "target": "r1", "relation": "contains"}, {"source": "bb", "target": "r2", "relation": "contains"},
        {"source": "r1", "target": "tc1", "relation": "maps_to"}, {"source": "r2", "target": "tc2", "relation": "maps_to"},
        {"source": "tc1", "target": "c1", "relation": "contains"}, {"source": "tc2", "target": "c2", "relation": "contains"},
        {"source": "c1", "target": "b1", "relation": "ran_as"}, {"source": "c2", "target": "b2", "relation": "ran_as"},
    ]
    return nodes, edges


def test_filters_configuration_by_image_engine_sbom_and_status():
    nodes, edges = graph_fixture()
    filtered, _ = filter_graph(nodes, edges, engine="docker", has_image=True, has_sbom=True, status="SUCCESS")
    ids = {node["id"] for node in filtered}
    assert ids == {"bb", "r1", "tc1", "c1", "b1"}


def test_filters_configuration_without_image_and_by_recent_build():
    nodes, edges = graph_fixture()
    no_image, _ = filter_graph(nodes, edges, has_image=False)
    assert {node["id"] for node in no_image} == {"bb", "r2", "tc2", "c2", "b2"}
    recent, _ = filter_graph(nodes, edges, since_days=7)
    assert "c1" in {node["id"] for node in recent}
    assert "c2" not in {node["id"] for node in recent}


def test_parses_teamcity_date_and_tolerates_unknown_format():
    assert parse_teamcity_date("20260722T120000+0500").year == 2026
    assert parse_teamcity_date("not-a-date") is None


def test_filters_only_target_configs_and_matches_running_state():
    nodes, edges = graph_fixture()
    next(node for node in nodes if node["id"] == "c1")["hasTargetOutput"] = True
    next(node for node in nodes if node["id"] == "b1")["state"] = "running"
    filtered, _ = filter_graph(nodes, edges, target_only=True, status="RUNNING")
    assert {node["id"] for node in filtered} == {"bb", "r1", "tc1", "c1", "b1"}
