import json

from app.snapshots import canonical_graph, diff_graphs


def test_canonical_graph_hash_is_stable_for_input_order():
    nodes = [{"id": "b", "kind": "build"}, {"id": "a", "kind": "repository"}]
    edges = [{"source": "a", "target": "b", "relation": "ran_as"}]
    first_payload, first_hash = canonical_graph(nodes, edges)
    second_payload, second_hash = canonical_graph(list(reversed(nodes)), edges)
    assert first_payload == second_payload
    assert first_hash == second_hash
    assert json.loads(first_payload)["nodes"][0]["id"] == "a"


def test_diff_reports_added_removed_and_changed_nodes_and_edges():
    before = {"nodes": [{"id": "a", "label": "old"}, {"id": "gone"}], "edges": [{"source": "a", "target": "gone", "relation": "contains"}]}
    after = {"nodes": [{"id": "a", "label": "new"}, {"id": "added"}], "edges": [{"source": "a", "target": "added", "relation": "contains"}]}
    diff = diff_graphs(before, after)
    assert diff["nodes"] == {"added": ["added"], "removed": ["gone"], "changed": ["a"]}
    assert diff["edges"]["added"] == [["a", "added", "contains"]]
    assert diff["edges"]["removed"] == [["a", "gone", "contains"]]
    assert diff["details"]["added"][0]["id"] == "added"
    assert diff["details"]["removed"][0]["id"] == "gone"
    assert diff["details"]["changed"][0]["before"]["label"] == "old"


def test_diff_highlights_build_output_changes():
    before = {"nodes": [{"id": "build:1", "hasSbom": False, "pushedImages": []}]}
    after = {"nodes": [{"id": "build:1", "hasSbom": True, "pushedImages": [{"image": "repo:1"}]}]}
    change = diff_graphs(before, after)["outputChanges"][0]
    assert change["id"] == "build:1"
    assert change["before"]["hasSbom"] is False
    assert change["after"]["pushedImages"] == [{"image": "repo:1"}]
