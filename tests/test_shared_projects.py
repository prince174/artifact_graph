import pytest

from app.filters import filter_graph
from app.service import annotate_visual_state
from app.subgraph import select_visible_page


def shared_graph():
    nodes = [
        {"id": "pa", "kind": "bb_project", "label": "Project A"},
        {"id": "pb", "kind": "bb_project", "label": "Project B"},
        {"id": "a", "kind": "repository", "label": "Alpha"},
        {"id": "b", "kind": "repository", "label": "Beta"},
        {"id": "shared", "kind": "tc_project", "label": "Shared"},
        {"id": "release", "kind": "tc_project", "label": "Release"},
        {"id": "ca", "kind": "build_configuration", "label": "Publish A", "mappedRepositoryIds": ["a"]},
        {"id": "cb", "kind": "build_configuration", "label": "Test B", "mappedRepositoryIds": ["b"]},
        {"id": "cab", "kind": "build_configuration", "label": "Test A and B", "mappedRepositoryIds": ["a", "b"]},
        {"id": "cr", "kind": "build_configuration", "label": "Release A", "mappedRepositoryIds": ["a"]},
        {"id": "ba", "kind": "build", "label": "#1", "status": "SUCCESS", "hasImagePush": True},
        {"id": "bb", "kind": "build", "label": "#2", "status": "FAILURE"},
        {"id": "bab", "kind": "build", "label": "#3", "status": "SUCCESS"},
        {"id": "br", "kind": "build", "label": "#4", "status": "SUCCESS", "hasSbom": True},
    ]
    edges = [{"source": source, "target": target, "relation": relation} for source, target, relation in [
        ("pa", "a", "contains"), ("pb", "b", "contains"),
        ("a", "shared", "maps_to"), ("b", "shared", "maps_to"), ("a", "release", "maps_to"),
        ("shared", "ca", "contains"), ("shared", "cb", "contains"), ("shared", "cab", "contains"), ("release", "cr", "contains"),
        ("ca", "ba", "ran_as"), ("cb", "bb", "ran_as"), ("cab", "bab", "ran_as"), ("cr", "br", "ran_as"),
    ]]
    return nodes, edges


def ids(nodes):
    return {node["id"] for node in nodes}


@pytest.mark.parametrize(("query", "expected"), [
    ("Alpha", {"pa", "a", "shared", "release", "ca", "cab", "cr", "ba", "bab", "br"}),
    ("Beta", {"pb", "b", "shared", "cb", "cab", "bb", "bab"}),
])
def test_repository_search_scopes_shared_project_configs_and_keeps_multiple_projects(query, expected):
    nodes, edges = shared_graph()
    selected, links, _ = select_visible_page(nodes, edges, query)
    assert ids(selected) == expected
    assert len(ids(selected)) == len(selected)
    assert all(link["source"] in expected and link["target"] in expected for link in links)


def test_project_pagination_does_not_pull_configs_of_unselected_repositories():
    nodes, edges = shared_graph()
    first, _, page = select_visible_page(nodes, edges, limit=1)
    second, _, _ = select_visible_page(nodes, edges, limit=1, cursor=page["nextCursor"])
    assert ids(first) == {"pa", "a", "shared", "release", "ca", "cab", "cr", "ba", "bab", "br"}
    assert ids(second) == {"pb", "b", "shared", "cb", "cab", "bb", "bab"}


def test_shared_project_retains_aggregate_color_without_coloring_unrelated_repository():
    nodes, edges = shared_graph()
    annotate_visual_state(nodes, edges)
    by_id = {node["id"]: node for node in nodes}
    assert all(by_id[key]["hasTargetOutput"] for key in ("pa", "a", "shared", "release", "ca", "cr"))
    assert all(not by_id[key]["hasTargetOutput"] for key in ("pb", "b", "cb", "cab"))
    selected, _, _ = select_visible_page(nodes, edges, "Beta")
    assert next(node for node in selected if node["id"] == "shared")["hasTargetOutput"] is True
    assert by_id["b"]["visualReason"] == "active"


def test_multi_root_config_propagates_outputs_to_both_repositories():
    nodes, edges = shared_graph()
    next(node for node in nodes if node["id"] == "bab")["hasSbom"] = True
    annotate_visual_state(nodes, edges)
    by_id = {node["id"]: node for node in nodes}
    assert all(by_id[key]["hasTargetOutput"] for key in ("pa", "pb", "a", "b", "shared", "cab"))
    assert by_id["cb"]["hasTargetOutput"] is False


@pytest.mark.parametrize(("filters", "expected"), [
    ({"bb_project": "Project B"}, {"pb", "b", "shared", "cb", "cab", "bb", "bab"}),
    ({"tc_project": "Release"}, {"pa", "a", "release", "cr", "br"}),
    ({"tc_project": "Shared", "bb_project": "Project B"}, {"pb", "b", "shared", "cb", "cab", "bb", "bab"}),
    ({"has_image": True}, {"pa", "a", "shared", "ca", "ba"}),
    ({"status": "FAILURE"}, {"pb", "b", "shared", "cb", "bb"}),
    ({"target_only": True}, {"pa", "a", "shared", "release", "ca", "cr", "ba", "br"}),
])
def test_filters_use_config_mapping_for_selection_and_repository_ancestors(filters, expected):
    nodes, edges = shared_graph()
    annotate_visual_state(nodes, edges)
    selected, links = filter_graph(nodes, edges, **filters)
    assert ids(selected) == expected
    assert all(link["source"] in expected and link["target"] in expected for link in links)


def test_search_and_project_filter_do_not_restore_a_hidden_repository():
    nodes, edges = shared_graph()
    selected, links, _ = select_visible_page(nodes, edges, "Beta")
    selected, _ = filter_graph(selected, links, tc_project="Shared")
    assert ids(selected) == {"pb", "b", "shared", "cb", "cab", "bb", "bab"}


@pytest.mark.parametrize("mapping", [{"mappedRepositoryIds": []}, {"mappingUnavailable": "HTTPStatusError"}])
def test_explicit_unmapped_or_unavailable_config_does_not_borrow_sibling_repositories(mapping):
    nodes, edges = shared_graph()
    nodes.extend([
        {"id": "unknown", "kind": "build_configuration", "label": "Unmapped", **mapping},
        {"id": "unknown-build", "kind": "build", "label": "#5", "hasImagePush": True},
    ])
    edges.extend([
        {"source": "shared", "target": "unknown", "relation": "contains"},
        {"source": "unknown", "target": "unknown-build", "relation": "ran_as"},
    ])
    annotate_visual_state(nodes, edges)
    selected, _, _ = select_visible_page(nodes, edges, "Beta")
    assert "unknown" not in ids(selected)
    assert next(node for node in nodes if node["id"] == "b")["hasTargetOutput"] is False


def test_paused_containers_have_consistent_activity_and_visual_reason():
    nodes, edges = shared_graph()
    for node in nodes:
        if node["kind"] == "build_configuration":
            node["active"] = False
    annotate_visual_state(nodes, edges)
    for node in nodes:
        if node["kind"] in {"build_configuration", "tc_project"}:
            assert node["active"] is False
            assert node["visualReason"] == "inactive"
