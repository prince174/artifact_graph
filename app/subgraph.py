from collections import defaultdict
import base64
import binascii
import json


def select_visible(nodes: list[dict], edges: list[dict], query: str = "", project_limit: int = 10, repo_limit: int = 10):
    visible_nodes, visible_edges, _ = select_visible_page(nodes, edges, query, limit=project_limit, repo_limit=repo_limit)
    return visible_nodes, visible_edges


def select_visible_page(nodes: list[dict], edges: list[dict], query: str = "", *, cursor: str = "", limit: int = 10, repo_limit: int = 10):
    limit = min(max(limit, 1), 100)
    mode = "repositories" if query else "projects"
    offset = decode_cursor(cursor, mode)
    by_id = {node["id"]: node for node in nodes}
    outgoing = defaultdict(list)
    incoming = defaultdict(list)
    for edge in edges:
        outgoing[edge["source"]].append(edge)
        incoming[edge["target"]].append(edge)

    repositories = [node for node in nodes if node["kind"] == "repository"]
    if query:
        candidates = sorted((node for node in repositories if query.casefold() in node.get("label", "").casefold()), key=_sort_key)
        selected_repos = candidates[offset:offset + limit]
        total = len(candidates)
    else:
        candidates = sorted((node for node in nodes if node["kind"] == "bb_project"), key=_sort_key)
        projects = candidates[offset:offset + limit]
        total = len(candidates)
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
    next_offset = offset + limit
    pagination = {"mode": mode, "offset": offset, "limit": limit, "total": total, "hasMore": next_offset < total, "nextCursor": encode_cursor(mode, next_offset) if next_offset < total else None}
    return visible_nodes, visible_edges, pagination


def paginate_mapping_issues(nodes: list[dict], edges: list[dict], *, cursor: str = "", limit: int = 10):
    limit = min(max(limit, 1), 100)
    offset = decode_cursor(cursor, "mapping_issues")
    roots = sorted((node for node in nodes if node.get("mappingStatus") != "mapped" and node.get("kind") in {"repository", "build_configuration"}), key=_sort_key)
    page_ids = {node["id"] for node in roots[offset:offset + limit]}
    selected = set(page_ids)
    selected.update(edge["source"] for edge in edges if edge["target"] in page_ids and edge["relation"] == "contains")
    visible_nodes = [node for node in nodes if node["id"] in selected]
    visible_edges = [edge for edge in edges if edge["source"] in selected and edge["target"] in selected]
    next_offset = offset + limit
    return visible_nodes, visible_edges, {"mode": "mapping_issues", "offset": offset, "limit": limit, "total": len(roots), "hasMore": next_offset < len(roots), "nextCursor": encode_cursor("mapping_issues", next_offset) if next_offset < len(roots) else None}


def encode_cursor(mode: str, offset: int) -> str:
    payload = json.dumps({"mode": mode, "offset": offset}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_cursor(cursor: str, expected_mode: str) -> int:
    if not cursor:
        return 0
    try:
        payload = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        data = json.loads(payload)
        if set(data) != {"mode", "offset"} or data["mode"] != expected_mode or not isinstance(data["offset"], int) or data["offset"] < 0:
            raise ValueError
        return data["offset"]
    except (ValueError, TypeError, json.JSONDecodeError, binascii.Error):
        raise ValueError("Invalid pagination cursor")


def _sort_key(node):
    return node.get("label", "").casefold(), node["id"]
