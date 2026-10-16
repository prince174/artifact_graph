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


def test_details_and_cached_graph_follow_saved_generation(monkeypatch):
    import pytest
    from fastapi import HTTPException
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app import main, service
    from app.models import Base
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine)
    monkeypatch.setattr(main, "SessionLocal", factory)
    monkeypatch.setattr(service, "SessionLocal", factory)
    try:
        node = dict(id="repo:one", kind="repository", label="Example", evidence="full evidence")
        service._save([node], [])
        result = main.graph(q="Example", compact=True, limit=10, build_limit=5)
        assert result["nodes"][0]["detailsDeferred"]
        assert "evidence" not in result["nodes"][0]
        assert main.node_details(node["id"])["evidence"] == "full evidence"
        assert main.graph(q="Example", compact=True, limit=10, build_limit=5) == result
        service._save([], [])
        assert main.graph(q="Example", compact=True, limit=10, build_limit=5)["nodes"] == []
        with pytest.raises(HTTPException) as exc:
            main.node_details(node["id"])
        assert exc.value.status_code == 404
    finally:
        main.graph_cache.invalidate()
        engine.dispose()
