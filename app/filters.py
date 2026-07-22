from datetime import datetime, timedelta, timezone


def filter_graph(nodes, edges, *, bb_project="", tc_project="", status="", engine="", has_image=None, has_sbom=None, since_days=0):
    if not any((bb_project, tc_project, status, engine, has_image is not None, has_sbom is not None, since_days)):
        return nodes, edges
    by_id = {node["id"]: node for node in nodes}
    outgoing = {}
    incoming = {}
    for edge in edges:
        outgoing.setdefault(edge["source"], []).append(edge)
        incoming.setdefault(edge["target"], []).append(edge)
    configurations = {node["id"] for node in nodes if node["kind"] == "build_configuration"}

    if bb_project:
        projects = {node["id"] for node in nodes if node["kind"] == "bb_project" and node["label"] == bb_project}
        repos = {edge["target"] for project in projects for edge in outgoing.get(project, []) if edge["relation"] == "contains"}
        configurations &= {edge["target"] for repo in repos for edge in outgoing.get(repo, []) if edge["relation"] == "built_by"}
    if tc_project:
        projects = {node["id"] for node in nodes if node["kind"] == "tc_project" and node["label"] == tc_project}
        configurations &= {edge["target"] for project in projects for edge in outgoing.get(project, []) if edge["relation"] == "contains"}

    def targets(config_id, relation, kind=None):
        return [by_id[edge["target"]] for edge in outgoing.get(config_id, [])
                if edge["relation"] == relation and edge["target"] in by_id and (kind is None or by_id[edge["target"]]["kind"] == kind)]

    if engine:
        configurations = {config for config in configurations if any(node.get("engine") == engine for node in targets(config, "pushes", "container_image"))}
    if has_image is not None:
        configurations = {config for config in configurations if bool(targets(config, "pushes", "container_image")) == has_image}
    if has_sbom is not None:
        configurations = {config for config in configurations if bool(targets(config, "publishes", "sbom")) == has_sbom}

    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days) if since_days else None

    def visible_build(node):
        if status and node.get("status") != status:
            return False
        if cutoff and (finished := parse_teamcity_date(node.get("finishDate"))) and finished < cutoff:
            return False
        return True

    if status or cutoff:
        configurations = {config for config in configurations if any(visible_build(node) for node in targets(config, "ran_as", "build"))}

    selected = set(configurations)
    for config in configurations:
        for edge in incoming.get(config, []):
            if edge["relation"] in {"built_by", "contains"}:
                selected.add(edge["source"])
        for edge in outgoing.get(config, []):
            target = by_id.get(edge["target"])
            if target and (target["kind"] != "build" or visible_build(target)):
                selected.add(edge["target"])
    for node_id in list(selected):
        if by_id.get(node_id, {}).get("kind") == "repository":
            selected.update(edge["source"] for edge in incoming.get(node_id, []) if edge["relation"] == "contains")
    filtered_nodes = [node for node in nodes if node["id"] in selected]
    filtered_edges = [edge for edge in edges if edge["source"] in selected and edge["target"] in selected]
    return filtered_nodes, filtered_edges


def parse_teamcity_date(value):
    if not value:
        return None
    for pattern in ("%Y%m%dT%H%M%S%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(value, pattern)
        except ValueError:
            pass
    return None
