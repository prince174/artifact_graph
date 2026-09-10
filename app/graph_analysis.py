from collections import Counter, defaultdict

from .subgraph import configuration_repository_ids


KINDS = ("bb_project", "repository", "tc_project", "build_configuration", "build")


def graph_insights(nodes: list[dict], edges: list[dict]) -> dict:
    by_id, outgoing, incoming = _indexes(nodes, edges)
    orphan_repositories = [
        node for node in nodes if node["kind"] == "repository"
        and not any(edge.get("relation") == "maps_to" for edge in outgoing[node["id"]])
    ]
    orphan_configurations = [
        node for node in nodes if node["kind"] == "build_configuration"
        and not configuration_repository_ids(node, by_id, incoming)
    ]
    low_confidence = [
        node for node in nodes if node["kind"] in {"repository", "build_configuration"}
        and (node.get("mappingStatus") in {"partial", "unknown"} or node.get("mappingConfidence") == "mixed")
    ]
    return {
        "counts": {
            "orphanRepositories": len(orphan_repositories),
            "orphanConfigurations": len(orphan_configurations),
            "lowConfidenceMappings": len(low_confidence),
        },
        "orphanRepositories": [_summary(node) for node in sorted(orphan_repositories, key=_sort_key)],
        "orphanConfigurations": [_summary(node) for node in sorted(orphan_configurations, key=_sort_key)],
        "lowConfidenceMappings": [_summary(node) for node in sorted(low_confidence, key=_sort_key)],
    }


def impact_report(nodes: list[dict], edges: list[dict], node_id: str) -> dict:
    by_id, outgoing, incoming = _indexes(nodes, edges)
    if node_id not in by_id:
        raise ValueError("Graph node not found")
    root = by_id[node_id]
    affected = {node_id}
    if root["kind"] == "bb_project":
        repos = [edge["target"] for edge in outgoing[node_id] if by_id.get(edge["target"], {}).get("kind") == "repository"]
        for repo_id in repos:
            affected.update(_repository_impact(repo_id, by_id, outgoing, incoming))
    elif root["kind"] == "repository":
        affected.update(_repository_impact(node_id, by_id, outgoing, incoming))
    elif root["kind"] == "tc_project":
        affected.update(_descendants(node_id, by_id, outgoing))
    elif root["kind"] == "build_configuration":
        affected.update(_descendants(node_id, by_id, outgoing))
    selected = [by_id[item] for item in affected]
    counts = Counter(node["kind"] for node in selected)
    outputs = [node for node in selected if node["kind"] == "build" and (node.get("hasImagePush") or node.get("hasSbom"))]
    return {
        "root": _summary(root),
        "nodeIds": sorted(affected),
        "counts": {kind: counts.get(kind, 0) for kind in KINDS},
        "configurations": [_summary(node) for node in sorted(selected, key=_sort_key) if node["kind"] == "build_configuration"],
        "builds": [_summary(node) for node in sorted(selected, key=_sort_key) if node["kind"] == "build"],
        "targetBuilds": [_summary(node) for node in sorted(outputs, key=_sort_key)],
    }


def _repository_impact(repo_id, by_id, outgoing, incoming):
    selected = {repo_id}
    for mapping in outgoing[repo_id]:
        tc_project = by_id.get(mapping["target"])
        if mapping.get("relation") != "maps_to" or not tc_project:
            continue
        selected.add(tc_project["id"])
        for edge in outgoing[tc_project["id"]]:
            config = by_id.get(edge["target"])
            if edge.get("relation") != "contains" or not config or config.get("kind") != "build_configuration":
                continue
            if repo_id not in configuration_repository_ids(config, by_id, incoming):
                continue
            selected.add(config["id"])
            selected.update(_descendants(config["id"], by_id, outgoing))
    return selected


def _descendants(root, by_id, outgoing):
    selected, queue = set(), [root]
    while queue:
        current = queue.pop()
        for edge in outgoing[current]:
            if edge.get("relation") not in {"contains", "ran_as"} or edge["target"] not in by_id or edge["target"] in selected:
                continue
            selected.add(edge["target"])
            queue.append(edge["target"])
    return selected


def _indexes(nodes, edges):
    by_id = {node["id"]: node for node in nodes}
    outgoing, incoming = defaultdict(list), defaultdict(list)
    for edge in edges:
        outgoing[edge["source"]].append(edge)
        incoming[edge["target"]].append(edge)
    return by_id, outgoing, incoming


def _summary(node):
    return {key: node.get(key) for key in ("id", "kind", "label", "mappingStatus", "mappingConfidence", "hasImagePush", "hasSbom") if node.get(key) is not None}


def _sort_key(node):
    return KINDS.index(node["kind"]), node.get("label", "").casefold(), node["id"]
