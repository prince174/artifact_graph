import pytest

from app.demo import dataset
from app.layout import layered_positions
from app.subgraph import select_visible
from app.validation import validate_fixture_graph, validate_repository_search


def fixture_graph():
    nodes, edges = dataset()
    return {"nodes": nodes, "edges": edges, "positions": layered_positions(nodes, edges)}


def test_fixture_graph_contract_and_search_contract():
    graph = fixture_graph()
    counts = validate_fixture_graph(graph)
    assert counts["repository"] == 10
    nodes, edges = select_visible(graph["nodes"], graph["edges"], "java-maven-api")
    validate_repository_search({"nodes": nodes, "edges": edges}, "java-maven-api")


def test_validation_reports_missing_build_history():
    graph = fixture_graph()
    graph["nodes"] = [node for node in graph["nodes"] if node["id"] != "build:build-type:Demo_01_Test/100"]
    graph["positions"].pop("build:build-type:Demo_01_Test/100")
    with pytest.raises(AssertionError, match="Expected 90 build"):
        validate_fixture_graph(graph)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [("status", "BROKEN", "Unexpected fixture build statuses"), ("state", "stuck", "Unexpected fixture build states")],
)
def test_validation_rejects_unknown_build_contract_values(field, value, message):
    graph = fixture_graph()
    next(node for node in graph["nodes"] if node["kind"] == "build")[field] = value
    with pytest.raises(AssertionError, match=message):
        validate_fixture_graph(graph)


@pytest.mark.parametrize(("field", "message"), [("hasImagePush", "confirmed image push"), ("hasSbom", "SBOM artifact")])
def test_validation_requires_target_output_examples(field, message):
    graph = fixture_graph()
    for node in graph["nodes"]:
        if node["kind"] == "build":
            node[field] = False
    with pytest.raises(AssertionError, match=message):
        validate_fixture_graph(graph)
