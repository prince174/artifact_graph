from collections import defaultdict


def layered_positions(nodes: list[dict], edges: list[dict]) -> dict[str, dict[str, float]]:
    """Place independent repository build chains into non-overlapping horizontal lanes."""
    by_id = {node["id"]: node for node in nodes}
    outgoing: dict[str, list[dict]] = defaultdict(list)
    incoming: dict[str, list[dict]] = defaultdict(list)
    for edge in edges:
        outgoing[edge["source"]].append(edge)
        incoming[edge["target"]].append(edge)

    projects = sorted((n for n in nodes if n["kind"] == "bb_project"), key=_sort_key)
    repositories: list[dict] = []
    for project in projects:
        children = [by_id[e["target"]] for e in outgoing[project["id"]] if e["target"] in by_id and by_id[e["target"]]["kind"] == "repository"]
        repositories.extend(sorted(children, key=_sort_key))
    known = {repo["id"] for repo in repositories}
    repositories.extend(sorted((n for n in nodes if n["kind"] == "repository" and n["id"] not in known), key=_sort_key))

    positions: dict[str, dict[str, float]] = {}
    repo_rows: dict[str, list[float]] = {}
    config_rows: dict[str, float] = {}
    cursor = 110.0
    for repo in repositories:
        configs = sorted(
            (by_id[e["target"]] for e in outgoing[repo["id"]] if e.get("relation") == "built_by" and e["target"] in by_id),
            key=_sort_key,
        )
        if not configs:
            repo_rows[repo["id"]] = [cursor]
            cursor += 140
            continue
        rows = []
        for config in configs:
            right_nodes = [by_id[e["target"]] for e in outgoing[config["id"]] if e["target"] in by_id and by_id[e["target"]]["kind"] in {"build", "container_image", "sbom"}]
            lane_height = max(140, 55 * max(1, len(right_nodes)) + 45)
            row = cursor + lane_height / 2
            config_rows[config["id"]] = row
            rows.append(row)
            cursor += lane_height
        repo_rows[repo["id"]] = rows

    for repo in repositories:
        rows = repo_rows[repo["id"]]
        positions[repo["id"]] = _point(280, sum(rows) / len(rows))
    for project in projects:
        children = [e["target"] for e in outgoing[project["id"]] if e["target"] in positions]
        if children:
            positions[project["id"]] = _point(80, sum(positions[c]["y"] for c in children) / len(children))

    for config_id, row in config_rows.items():
        positions[config_id] = _point(720, row)
        targets = sorted(
            (by_id[e["target"]] for e in outgoing[config_id] if e["target"] in by_id and by_id[e["target"]]["kind"] in {"build", "container_image", "sbom"}),
            key=lambda n: ({"container_image": 0, "sbom": 1, "build": 2}.get(n["kind"], 9), n.get("label", "")),
        )
        start = row - (len(targets) - 1) * 27.5
        for index, target in enumerate(targets):
            positions[target["id"]] = _point(1080, start + index * 55)

    tc_projects = sorted((n for n in nodes if n["kind"] == "tc_project"), key=_sort_key)
    for project in tc_projects:
        children = [e["target"] for e in outgoing[project["id"]] if e["target"] in config_rows]
        y = min((config_rows[c] for c in children), default=60) - 55
        positions[project["id"]] = _point(720, max(35, y))

    fallback_y = cursor + 80
    for node in nodes:
        if node["id"] not in positions:
            positions[node["id"]] = _point(1080, fallback_y)
            fallback_y += 60
    return positions


def _sort_key(node):
    return node.get("label", "").casefold(), node["id"]


def _point(x, y):
    return {"x": float(x), "y": float(y)}
