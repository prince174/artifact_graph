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


def shared_layout_graph(*, disjoint=True):
    nodes = [
        {"id": "p", "kind": "bb_project", "label": "Project"},
        {"id": "ra", "kind": "repository", "label": "Alpha"},
        {"id": "rb", "kind": "repository", "label": "Beta"},
        {"id": "rc", "kind": "repository", "label": "Canary without builds"},
        {"id": "tc", "kind": "tc_project", "label": "Shared"},
        {"id": "cab", "kind": "build_configuration", "label": "Composite", "mappedRepositoryIds": ["ra", "rb"]},
        {"id": "bab", "kind": "build", "label": "#1"},
    ]
    links = [("p", "ra", "contains"), ("p", "rb", "contains"), ("p", "rc", "contains"),
             ("ra", "tc", "maps_to"), ("rb", "tc", "maps_to"), ("tc", "cab", "contains"), ("cab", "bab", "ran_as")]
    if disjoint:
        for suffix in ("a", "b"):
            nodes.extend([
                {"id": "c" + suffix, "kind": "build_configuration", "label": suffix, "mappedRepositoryIds": ["r" + suffix]},
                {"id": "b" + suffix, "kind": "build", "label": "#2"},
            ])
            links.extend([("tc", "c" + suffix, "contains"), ("c" + suffix, "b" + suffix, "ran_as")])
    return nodes, [{"source": source, "target": target, "relation": relation} for source, target, relation in links]


def test_shared_project_uses_separate_repository_rows_and_only_owned_config_centers():
    nodes, edges = shared_layout_graph()
    positions = layered_positions(nodes, edges)
    assert positions["ra"]["y"] < positions["rb"]["y"] < positions["rc"]["y"]
    assert positions["ra"]["y"] == (positions["ca"]["y"] + positions["cab"]["y"]) / 2
    assert positions["rb"]["y"] >= (positions["cb"]["y"] + positions["cab"]["y"]) / 2
    columns = {"bb_project": 80, "repository": 280, "tc_project": 520, "build_configuration": 760, "build": 1080}
    assert all(positions[node["id"]]["x"] == columns[node["kind"]] for node in nodes)
    assert len({(point["x"], point["y"]) for point in positions.values()}) == len(nodes)


def test_one_multi_root_config_does_not_stack_its_repositories_or_canary():
    nodes, edges = shared_layout_graph(disjoint=False)
    positions = layered_positions(nodes, edges)
    assert positions["rb"]["y"] - positions["ra"]["y"] >= 90
    assert positions["rc"]["y"] - positions["rb"]["y"] >= 90
    assert positions["cab"]["y"] == positions["bab"]["y"]
    assert len(positions) == len(nodes)


def test_shared_and_unconnected_layout_is_independent_of_api_iteration_order():
    nodes, edges = shared_layout_graph()
    nodes.extend([
        {"id": "empty-project", "kind": "tc_project", "label": "Unconnected TC"},
        {"id": "empty-config", "kind": "build_configuration", "label": "Unconnected config", "mappedRepositoryIds": []},
    ])
    forward = layered_positions(nodes, edges)
    assert layered_positions(list(reversed(nodes)), list(reversed(edges))) == forward
    assert len({(point["x"], point["y"]) for point in forward.values()}) == len(nodes)
