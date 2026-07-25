from app.layout import layered_positions


def test_repository_chains_are_ordered_and_column_aligned():
    nodes = [
        {"id": "p", "kind": "bb_project", "label": "Project"},
        {"id": "r-b", "kind": "repository", "label": "B repo"},
        {"id": "r-a", "kind": "repository", "label": "A repo"},
        {"id": "tc-b", "kind": "tc_project", "label": "B product"},
        {"id": "tc-a", "kind": "tc_project", "label": "A product"},
        {"id": "c-b", "kind": "build_configuration", "label": "B config"},
        {"id": "c-a", "kind": "build_configuration", "label": "A config"},
        {"id": "b-b", "kind": "build", "label": "#2"},
        {"id": "b-a", "kind": "build", "label": "#1"},
    ]
    edges = [
        {"source": "p", "target": "r-b", "relation": "contains"},
        {"source": "p", "target": "r-a", "relation": "contains"},
        {"source": "r-b", "target": "tc-b", "relation": "maps_to"},
        {"source": "r-a", "target": "tc-a", "relation": "maps_to"},
        {"source": "tc-b", "target": "c-b", "relation": "contains"},
        {"source": "tc-a", "target": "c-a", "relation": "contains"},
        {"source": "c-b", "target": "b-b", "relation": "ran_as"},
        {"source": "c-a", "target": "b-a", "relation": "ran_as"},
    ]
    result = layered_positions(nodes, edges)
    assert result["r-a"]["y"] < result["r-b"]["y"]
    assert result["r-a"]["y"] == result["tc-a"]["y"] == result["c-a"]["y"] == result["b-a"]["y"]
    assert result["r-b"]["y"] == result["tc-b"]["y"] == result["c-b"]["y"] == result["b-b"]["y"]
    assert result["p"]["x"] < result["r-a"]["x"] < result["tc-a"]["x"] < result["c-a"]["x"] < result["b-a"]["x"]


def test_builds_are_ordered_by_numeric_number_not_lexically():
    nodes = [
        {"id": "p", "kind": "bb_project", "label": "P"}, {"id": "r", "kind": "repository", "label": "R"},
        {"id": "t", "kind": "tc_project", "label": "T"}, {"id": "c", "kind": "build_configuration", "label": "C"},
        {"id": "b10", "kind": "build", "label": "#10"}, {"id": "b2", "kind": "build", "label": "#2"},
    ]
    edges = [
        {"source": "p", "target": "r", "relation": "contains"}, {"source": "r", "target": "t", "relation": "maps_to"},
        {"source": "t", "target": "c", "relation": "contains"}, {"source": "c", "target": "b10", "relation": "ran_as"},
        {"source": "c", "target": "b2", "relation": "ran_as"},
    ]
    result = layered_positions(nodes, edges)
    assert result["b2"]["y"] < result["b10"]["y"]
