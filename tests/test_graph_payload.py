import json

from app.graph_payload import compact_node


def test_compact_payload_keeps_visual_and_hover_data_without_evidence():
    node = dict(id="build:1", kind="build", label="#1", status="SUCCESS", hasSbom=True,
                evidence="x" * 100000,
                sbomArtifacts=[dict(path="out/sbom.json", sbomStatus="valid", components=list(range(10000)))],
                pushedImages=[dict(image="registry.isb/image:1", sourcePaths=["build.sh"], evidence="secret")])
    compact = compact_node(node)
    assert compact["status"] == "SUCCESS" and compact["hasSbom"]
    assert compact["detailsDeferred"]
    assert compact["sbomArtifacts"] == [{"path": "out/sbom.json", "sbomStatus": "valid"}]
    assert compact["pushedImages"][0]["sourcePaths"] == ["build.sh"]
    assert len(json.dumps(compact)) < len(json.dumps(node)) / 100
    assert "components" in node["sbomArtifacts"][0]
