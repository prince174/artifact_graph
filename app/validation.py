from collections import Counter


def validate_fixture_graph(graph: dict) -> dict[str, int]:
    counts = Counter(node["kind"] for node in graph["nodes"])
    expected = {
        "bb_project": 1, "repository": 10, "tc_project": 10,
        "build_configuration": 30, "build": 90,
    }
    for kind, count in expected.items():
        if counts[kind] != count:
            raise AssertionError(f"Expected {count} {kind} nodes, got {counts[kind]}")
    build_nodes = [node for node in graph["nodes"] if node["kind"] == "build"]
    statuses = {node.get("status") for node in build_nodes}
    states = {node.get("state") for node in build_nodes}
    unexpected_statuses = statuses - {"SUCCESS", "FAILURE", "ERROR", "UNKNOWN"}
    unexpected_states = states - {"finished", "running", "queued"}
    if unexpected_statuses:
        raise AssertionError(f"Unexpected fixture build statuses: {sorted(unexpected_statuses, key=str)}")
    if unexpected_states:
        raise AssertionError(f"Unexpected fixture build states: {sorted(unexpected_states, key=str)}")
    if "SUCCESS" not in statuses:
        raise AssertionError("Fixture must contain a successful build")
    if not any(node.get("hasImagePush") for node in build_nodes):
        raise AssertionError("Fixture must contain a confirmed image push")
    if not any(node.get("hasSbom") for node in build_nodes):
        raise AssertionError("Fixture must contain an SBOM artifact")
    if len(graph.get("positions", {})) != len(graph["nodes"]):
        raise AssertionError("Every visible node must have a preset position")
    return dict(counts)


def validate_repository_search(graph: dict, repository_name: str):
    repositories = [node for node in graph["nodes"] if node["kind"] == "repository"]
    if len(repositories) != 1 or repositories[0]["label"].lower() != repository_name.lower():
        raise AssertionError(f"Search did not isolate repository {repository_name}")
    builds = [node for node in graph["nodes"] if node["kind"] == "build"]
    if len(builds) != 9:
        raise AssertionError(f"Expected last 3 builds for 3 stages in search result, got {len(builds)}")
