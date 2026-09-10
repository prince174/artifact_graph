import pytest

from app.graph_analysis import graph_insights, impact_report


def graph():
    nodes = [
        {"id": "p", "kind": "bb_project", "label": "P"},
        {"id": "r1", "kind": "repository", "label": "Owned"},
        {"id": "r2", "kind": "repository", "label": "Orphan"},
        {"id": "t", "kind": "tc_project", "label": "T"},
        {"id": "c1", "kind": "build_configuration", "label": "Owned config", "mappedRepositoryIds": ["r1"]},
        {"id": "c2", "kind": "build_configuration", "label": "Other config", "mappedRepositoryIds": ["r2"]},
        {"id": "c3", "kind": "build_configuration", "label": "Orphan config", "mappedRepositoryIds": [], "mappingStatus": "unknown"},
        {"id": "b1", "kind": "build", "label": "#1", "hasImagePush": True},
        {"id": "b2", "kind": "build", "label": "#2"},
    ]
    edges = [
        {"source": "p", "target": "r1", "relation": "contains"},
        {"source": "p", "target": "r2", "relation": "contains"},
        {"source": "r1", "target": "t", "relation": "maps_to"},
        {"source": "t", "target": "c1", "relation": "contains"},
        {"source": "t", "target": "c2", "relation": "contains"},
        {"source": "t", "target": "c3", "relation": "contains"},
        {"source": "c1", "target": "b1", "relation": "ran_as"},
        {"source": "c2", "target": "b2", "relation": "ran_as"},
    ]
    return nodes, edges


def test_insights_find_orphans_and_low_confidence_mappings():
    nodes, edges = graph()
    result = graph_insights(nodes, edges)
    assert result["counts"] == {"orphanRepositories": 1, "orphanConfigurations": 1, "lowConfidenceMappings": 1}
    assert [node["id"] for node in result["orphanRepositories"]] == ["r2"]
    assert [node["id"] for node in result["orphanConfigurations"]] == ["c3"]
    assert [node["id"] for node in result["lowConfidenceMappings"]] == ["c3"]


def test_repository_impact_respects_configuration_ownership_and_marks_target_builds():
    nodes, edges = graph()
    result = impact_report(nodes, edges, "r1")
    assert result["nodeIds"] == ["b1", "c1", "r1", "t"]
    assert result["counts"]["build_configuration"] == 1
    assert [node["id"] for node in result["targetBuilds"]] == ["b1"]


def test_project_and_configuration_impact_and_missing_node():
    nodes, edges = graph()
    assert set(impact_report(nodes, edges, "p")["nodeIds"]) == {"p", "r1", "r2", "t", "c1", "b1"}
    assert set(impact_report(nodes, edges, "c2")["nodeIds"]) == {"c2", "b2"}
    with pytest.raises(ValueError, match="not found"):
        impact_report(nodes, edges, "missing")
