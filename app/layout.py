from collections import defaultdict


def layered_positions(nodes: list[dict], edges: list[dict]) -> dict[str, dict[str, float]]:
    """Place the strict BB project -> repo -> TC project -> config -> build chain in lanes."""
    by_id = {node["id"]: node for node in nodes}
    outgoing: dict[str, list[dict]] = defaultdict(list)
    for edge in edges:
        outgoing[edge["source"]].append(edge)

    bb_projects = sorted((n for n in nodes if n["kind"] == "bb_project"), key=_sort_key)
    repositories: list[dict] = []
    for project in bb_projects:
        repositories.extend(sorted(_children(project["id"], "repository", "contains", by_id, outgoing), key=_sort_key))
    known_repos = {repo["id"] for repo in repositories}
    repositories.extend(sorted((n for n in nodes if n["kind"] == "repository" and n["id"] not in known_repos), key=_sort_key))

    positions: dict[str, dict[str, float]] = {}
    repo_rows: dict[str, list[float]] = {}
    tc_rows: dict[str, list[float]] = defaultdict(list)
    config_rows: dict[str, float] = {}
    cursor = 110.0

    for repo in repositories:
        tc_projects = sorted(_children(repo["id"], "tc_project", "maps_to", by_id, outgoing), key=_sort_key)
        rows = []
        for tc_project in tc_projects:
            configs = sorted(_children(tc_project["id"], "build_configuration", "contains", by_id, outgoing), key=_sort_key)
            for config in configs:
                if config["id"] in config_rows:
                    rows.append(config_rows[config["id"]])
                    tc_rows[tc_project["id"]].append(config_rows[config["id"]])
                    continue
                outputs = [n for n in _targets(config["id"], by_id, outgoing) if n["kind"] in {"build", "container_image", "sbom"}]
                lane_height = max(140, 55 * max(1, len(outputs)) + 45)
                row = cursor + lane_height / 2
                config_rows[config["id"]] = row
                tc_rows[tc_project["id"]].append(row)
                rows.append(row)
                cursor += lane_height
        repo_rows[repo["id"]] = rows or [cursor]
        if not rows:
            cursor += 140

    for repo in repositories:
        positions[repo["id"]] = _point(280, _average(repo_rows[repo["id"]]))
    for project in bb_projects:
        children = [e["target"] for e in outgoing[project["id"]] if e["target"] in positions]
        if children:
            positions[project["id"]] = _point(80, _average([positions[c]["y"] for c in children]))
    for project_id, rows in tc_rows.items():
        positions[project_id] = _point(520, _average(rows))

    for config_id, row in config_rows.items():
        positions[config_id] = _point(760, row)
        targets = sorted(
            (n for n in _targets(config_id, by_id, outgoing) if n["kind"] in {"build", "container_image", "sbom"}),
            key=lambda n: ({"container_image": 0, "sbom": 1, "build": 2}.get(n["kind"], 9), n.get("label", "")),
        )
        start = row - (len(targets) - 1) * 27.5
        for index, target in enumerate(targets):
            positions[target["id"]] = _point(1080, start + index * 55)

    fallback_y = cursor + 80
    for node in nodes:
        if node["id"] not in positions:
            x = {"bb_project": 80, "repository": 280, "tc_project": 520, "build_configuration": 760}.get(node["kind"], 1080)
            positions[node["id"]] = _point(x, fallback_y)
            fallback_y += 60
    return positions


def _children(node_id, kind, relation, by_id, outgoing):
    return [by_id[e["target"]] for e in outgoing[node_id] if e.get("relation") == relation and e["target"] in by_id and by_id[e["target"]]["kind"] == kind]


def _targets(node_id, by_id, outgoing):
    return [by_id[e["target"]] for e in outgoing[node_id] if e["target"] in by_id]


def _average(values):
    return sum(values) / len(values)


def _sort_key(node):
    return node.get("label", "").casefold(), node["id"]


def _point(x, y):
    return {"x": float(x), "y": float(y)}
