from app.service import deduplicate


def test_duplicate_edge_preserves_all_evidence_paths():
    result = deduplicate([
        {"source": "config", "target": "image", "relation": "pushes", "evidence": "pom.xml"},
        {"source": "config", "target": "image", "relation": "pushes", "evidence": "ci/build.sh"},
    ], ("source", "target", "relation"))
    assert result == [{
        "source": "config", "target": "image", "relation": "pushes", "evidence": "ci/build.sh",
        "evidencePaths": ["pom.xml", "ci/build.sh"],
    }]
