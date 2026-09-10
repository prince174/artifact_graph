import json

from app.snapshots import canonical_graph, diff_graphs, snapshot_timeline


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
    assert diff["details"]["changed"][0]["fields"] == ["label"]


def test_diff_highlights_build_output_changes():
    before = {"nodes": [{"id": "build:1", "hasSbom": False, "pushedImages": []}]}
    after = {"nodes": [{"id": "build:1", "hasSbom": True, "pushedImages": [{"image": "repo:1"}]}]}
    change = diff_graphs(before, after)["outputChanges"][0]
    assert change["id"] == "build:1"
    assert change["before"]["hasSbom"] is False
    assert change["after"]["pushedImages"] == [{"image": "repo:1"}]


def test_snapshot_timeline_summarizes_adjacent_changes_newest_first():
    rows = [
        {"id": 2, "scanId": 12, "createdAt": "later", "nodeCount": 2, "edgeCount": 1, "hash": "b", "graph": {"nodes": [{"id": "a", "hasSbom": True}, {"id": "b"}], "edges": [{"source": "a", "target": "b", "relation": "contains"}]}},
        {"id": 1, "scanId": 11, "createdAt": "earlier", "nodeCount": 1, "edgeCount": 0, "hash": "a", "graph": {"nodes": [{"id": "a", "hasSbom": False}], "edges": []}},
    ]
    timeline = snapshot_timeline(rows)
    assert [entry["id"] for entry in timeline] == [2, 1]
    assert timeline[0]["previousId"] == 1
    assert timeline[0]["changes"] == {
        "nodesAdded": 1, "nodesRemoved": 0, "nodesChanged": 1,
        "edgesAdded": 1, "edgesRemoved": 0, "edgesChanged": 0, "outputChanges": 1,
    }
    assert timeline[1]["previousId"] is None
