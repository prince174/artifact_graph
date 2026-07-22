from collections import defaultdict


def select_visible(nodes: list[dict], edges: list[dict], query: str = "", project_limit: int = 10, repo_limit: int = 10):
    by_id = {node["id"]: node for node in nodes}
    outgoing = defaultdict(list)
    incoming = defaultdict(list)
    for edge in edges:
        outgoing[edge["source"]].append(edge)
        incoming[edge["target"]].append(edge)

    repositories = [node for node in nodes if node["kind"] == "repository"]
    if query:
        selected_repos = [node for node in repositories if query.casefold() in node.get("label", "").casefold()]
    else:
        projects = sorted((node for node in nodes if node["kind"] == "bb_project"), key=_sort_key)[:project_limit]
        selected_repos = []
        for project in projects:
            children = [by_id[e["target"]] for e in outgoing[project["id"]] if e["target"] in by_id and by_id[e["target"]]["kind"] == "repository"]
            selected_repos.extend(sorted(children, key=_sort_key)[:repo_limit])

    selected = {repo["id"] for repo in selected_repos}
    for repo in selected_repos:
        selected.update(e["source"] for e in incoming[repo["id"]] if e.get("relation") == "contains")
        selected.update(e["target"] for e in outgoing[repo["id"]] if e.get("relation") == "maps_to")
    tc_projects = [node_id for node_id in selected if by_id.get(node_id, {}).get("kind") == "tc_project"]
    for project_id in tc_projects:
        selected.update(e["target"] for e in outgoing[project_id] if e.get("relation") == "contains")
    configurations = [node_id for node_id in selected if by_id.get(node_id, {}).get("kind") == "build_configuration"]
    for config_id in configurations:
        selected.update(e["source"] for e in incoming[config_id] if e.get("relation") == "contains")
        selected.update(e["target"] for e in outgoing[config_id])

    visible_nodes = [node for node in nodes if node["id"] in selected]
    visible_edges = [edge for edge in edges if edge["source"] in selected and edge["target"] in selected]
    return visible_nodes, visible_edges


def _sort_key(node):
    return node.get("label", "").casefold(), node["id"]
