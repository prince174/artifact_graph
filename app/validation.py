from collections import Counter


def validate_fixture_graph(graph: dict) -> dict[str, int]:
    counts = Counter(node["kind"] for node in graph["nodes"])
    expected = {
        "bb_project": 1, "repository": 10, "tc_project": 10,
        "build_configuration": 40, "build": 200, "container_image": 8, "sbom": 5,
    }
    for kind, count in expected.items():
        if counts[kind] != count:
            raise AssertionError(f"Expected {count} {kind} nodes, got {counts[kind]}")
    build_nodes = [node for node in graph["nodes"] if node["kind"] == "build"]
    if any(node.get("status") != "SUCCESS" for node in build_nodes):
        raise AssertionError("All fixture builds must be successful")
    if len(graph.get("positions", {})) != len(graph["nodes"]):
        raise AssertionError("Every visible node must have a preset position")
    return dict(counts)


def validate_repository_search(graph: dict, repository_name: str):
    repositories = [node for node in graph["nodes"] if node["kind"] == "repository"]
    if len(repositories) != 1 or repositories[0]["label"].lower() != repository_name.lower():
        raise AssertionError(f"Search did not isolate repository {repository_name}")
    builds = [node for node in graph["nodes"] if node["kind"] == "build"]
    if len(builds) != 20:
        raise AssertionError(f"Expected last 5 builds for 4 stages in search result, got {len(builds)}")
