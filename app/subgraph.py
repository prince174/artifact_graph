from collections import defaultdict
import base64
import binascii
import json
import re


def select_visible(nodes: list[dict], edges: list[dict], query: str = "", project_limit: int = 10, repo_limit: int = 10):
    visible_nodes, visible_edges, _ = select_visible_page(nodes, edges, query, limit=project_limit, repo_limit=repo_limit)
    return visible_nodes, visible_edges


def limit_builds_per_configuration(nodes: list[dict], edges: list[dict], limit: int):
    """Keep the newest N build leaves per configuration without changing its ancestry."""
    by_id = {node["id"]: node for node in nodes}
    builds_by_config: dict[str, list[dict]] = defaultdict(list)
    for edge in edges:
        target = by_id.get(edge["target"])
        if edge.get("relation") == "ran_as" and target and target.get("kind") == "build":
            builds_by_config[edge["source"]].append(target)
    keep = {node["id"] for node in nodes if node.get("kind") != "build"}
    for builds in builds_by_config.values():
        keep.update(node["id"] for node in sorted(builds, key=_build_recency_key, reverse=True)[:limit])
    return (
        [node for node in nodes if node["id"] in keep],
        [edge for edge in edges if edge["source"] in keep and edge["target"] in keep],
    )


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

    if query:
        needle = query.casefold()
        repository_candidates = sorted(
            (node for node in nodes if node["kind"] == "repository" and needle in node.get("label", "").casefold()),
            key=_sort_key,
        )
        if repository_candidates:
            candidates = repository_candidates
            selected_repos = candidates[offset:offset + limit]
            total = len(candidates)
        else:
            candidates = sorted((node for node in nodes if needle in _search_text(node)), key=lambda node: (node["kind"], *_sort_key(node)))
            page_matches = candidates[offset:offset + limit]
            total = len(candidates)
            match_ids = {node["id"] for node in page_matches}
            selected = _connected_lineage(match_ids, by_id, incoming, outgoing)
            visible_nodes = [dict(node, searchMatch=node["id"] in match_ids) for node in nodes if node["id"] in selected]
            visible_edges = [edge for edge in edges if edge["source"] in selected and edge["target"] in selected]
            next_offset = offset + limit
            pagination = {"mode": mode, "offset": offset, "limit": limit, "total": total, "hasMore": next_offset < total, "nextCursor": encode_cursor(mode, next_offset) if next_offset < total else None}
            return visible_nodes, visible_edges, pagination
    else:
        candidates = sorted((node for node in nodes if node["kind"] == "bb_project"), key=_sort_key)
        projects = candidates[offset:offset + limit]
        total = len(candidates)
        selected_repos = []
        for project in projects:
            children = [by_id[e["target"]] for e in outgoing[project["id"]] if e["target"] in by_id and by_id[e["target"]]["kind"] == "repository"]
            selected_repos.extend(sorted(children, key=_sort_key)[:repo_limit])

    selected_repo_ids = {repo["id"] for repo in selected_repos}
    selected = set(selected_repo_ids)
    for repo in selected_repos:
        selected.update(e["source"] for e in incoming[repo["id"]] if e.get("relation") == "contains")
        selected.update(e["target"] for e in outgoing[repo["id"]] if e.get("relation") == "maps_to")
    tc_projects = [node_id for node_id in selected if by_id.get(node_id, {}).get("kind") == "tc_project"]
    for project_id in tc_projects:
        selected.update(
            e["target"] for e in outgoing[project_id]
            if e.get("relation") == "contains"
            and by_id.get(e["target"], {}).get("kind") == "build_configuration"
            and configuration_repository_ids(by_id[e["target"]], by_id, incoming) & selected_repo_ids
        )
    configurations = [node_id for node_id in selected if by_id.get(node_id, {}).get("kind") == "build_configuration"]
    for config_id in configurations:
        selected.update(e["source"] for e in incoming[config_id] if e.get("relation") == "contains")
        selected.update(e["target"] for e in outgoing[config_id] if e.get("relation") == "ran_as" and by_id.get(e["target"], {}).get("kind") == "build")

    visible_nodes = [dict(node, searchMatch=True) if query and node["id"] in selected_repo_ids else node for node in nodes if node["id"] in selected]
    visible_edges = [edge for edge in edges if edge["source"] in selected and edge["target"] in selected]
    next_offset = offset + limit
    pagination = {"mode": mode, "offset": offset, "limit": limit, "total": total, "hasMore": next_offset < total, "nextCursor": encode_cursor(mode, next_offset) if next_offset < total else None}
    return visible_nodes, visible_edges, pagination


def _connected_lineage(roots, by_id, incoming, outgoing):
    allowed = {"contains", "maps_to", "ran_as"}
    selected = set(roots)
    for direction in (incoming, outgoing):
        queue = list(roots)
        while queue:
            current = queue.pop()
            for edge in direction[current]:
                if edge.get("relation") not in allowed:
                    continue
                adjacent = edge["source"] if direction is incoming else edge["target"]
                if adjacent in by_id and adjacent not in selected:
                    selected.add(adjacent)
                    queue.append(adjacent)
    return selected


def _search_text(node):
    values = [node.get("id", ""), node.get("label", ""), node.get("projectKey", ""), node.get("buildTypeId", "")]
    values.extend(f"{item.get('engine', '')} {item.get('image', '')}" for item in node.get("pushedImages", []))
    values.extend(item.get("path", "") for item in node.get("sbomArtifacts", []))
    return " ".join(str(value) for value in values).casefold()


def configuration_repository_ids(configuration: dict, by_id: dict, incoming: dict) -> set[str]:
    """Resolve config ownership without assuming every repo owns its TC project.

    Old saved graphs did not include explicit mappings, so they retain their
    project-level association until their next scan. An explicit empty mapping
    or unavailable config must never borrow a sibling's repositories.
    """
    if configuration.get("mappingUnavailable"):
        return set()
    if "mappedRepositoryIds" in configuration:
        return {repo_id for repo_id in configuration.get("mappedRepositoryIds", []) if by_id.get(repo_id, {}).get("kind") == "repository"}
    projects = {
        edge["source"] for edge in incoming.get(configuration["id"], [])
        if edge.get("relation") == "contains" and by_id.get(edge["source"], {}).get("kind") == "tc_project"
    }
    return {
        edge["source"] for project_id in projects for edge in incoming.get(project_id, [])
        if edge.get("relation") == "maps_to" and by_id.get(edge["source"], {}).get("kind") == "repository"
    }


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


def _build_recency_key(node):
    match = re.search(r"\d+", node.get("label", ""))
    return node.get("finishDate", ""), int(match.group()) if match else -1, node["id"]
