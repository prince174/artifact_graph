from app.mapping_quality import annotate_mapping_quality, coverage_report
from app.demo import dataset


def test_mapping_quality_marks_exact_and_unmapped_chains():
    nodes = [
        {"id": "bb", "kind": "bb_project", "label": "BB"}, {"id": "r1", "kind": "repository", "label": "Mapped"},
        {"id": "r2", "kind": "repository", "label": "Orphan"}, {"id": "tc1", "kind": "tc_project", "label": "TC1"},
        {"id": "tc2", "kind": "tc_project", "label": "TC2"}, {"id": "c1", "kind": "build_configuration", "label": "Build", "mappedRepositoryIds": ["r1"]},
        {"id": "c2", "kind": "build_configuration", "label": "Orphan build"},
    ]
    edges = [
        {"source": "bb", "target": "r1", "relation": "contains"}, {"source": "bb", "target": "r2", "relation": "contains"},
        {"source": "r1", "target": "tc1", "relation": "maps_to", "confidence": "exact_vcs_url"},
        {"source": "tc1", "target": "c1", "relation": "contains"}, {"source": "tc2", "target": "c2", "relation": "contains"},
    ]
    annotate_mapping_quality(nodes, edges)
    by_id = {node["id"]: node for node in nodes}
    assert by_id["r1"]["mappingConfidence"] == "exact_vcs_url"
    assert by_id["r2"]["mappingStatus"] == "unmapped"
    assert by_id["c1"]["mappingStatus"] == "mapped"
    assert by_id["c2"]["mappingReason"] == "no_vcs_root"
    report = coverage_report(nodes)
    assert report["counts"]["repository"] == {"total": 2, "mapped": 1, "unmapped": 1, "unknown": 0}
    assert {item["id"] for item in report["issues"]} == {"r2", "c2"}


def test_demo_mapping_coverage_is_complete():
    nodes, edges = dataset()
    annotate_mapping_quality(nodes, edges)
    report = coverage_report(nodes)
    assert report["counts"]["repository"]["mapped"] == 10
    assert report["counts"]["build_configuration"]["mapped"] == 30
    assert report["issues"] == []
