"""Load a repository search page and its graph without materializing the full DB."""
import json

from sqlalchemy import func, or_

from .models import Node, Edge
from .subgraph import decode_cursor, encode_cursor, select_visible_page


def repository_search(db, query, cursor="", limit=10):
    limit = min(max(limit, 1), 100)
    offset = decode_cursor(cursor, "repositories")
    # Escaping preserves literal substring search for %, _ and backslashes.
    pattern = query.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    candidates = db.query(Node).filter(Node.kind == "repository", func.lower(Node.label).like(f"%{pattern}%", escape="\\"))
    total = candidates.count()
    if not total:
        return None  # Preserve the existing image/build/general search fallback.
    repos = candidates.order_by(func.lower(Node.label), Node.id).offset(offset).limit(limit).all()
    repo_ids = {row.id for row in repos}
    rows = {row.id: row for row in repos}
    if repo_ids:
        links = db.query(Edge).filter(or_(
            (Edge.target.in_(repo_ids)) & (Edge.relation == "contains"),
            (Edge.source.in_(repo_ids)) & (Edge.relation == "maps_to"),
        )).all()
        related = {edge.source for edge in links} | {edge.target for edge in links}
        rows.update((row.id, row) for row in db.query(Node).filter(Node.id.in_(related)).all())
        projects = [row.id for row in rows.values() if row.kind == "tc_project"]
        configs = []
        if projects:
            config_rows = db.query(Node).join(Edge, Edge.target == Node.id).filter(
                Edge.source.in_(projects), Edge.relation == "contains", Node.kind == "build_configuration").all()
            for row in config_rows:
                data = json.loads(row.data)
                if not data.get("mappingUnavailable") and (
                    "mappedRepositoryIds" not in data or repo_ids.intersection(data["mappedRepositoryIds"])
                ):
                    configs.append(row.id)
                    rows[row.id] = row
        if configs:
            build_rows = db.query(Node).join(Edge, Edge.target == Node.id).filter(
                Edge.source.in_(configs), Edge.relation == "ran_as", Node.kind == "build").all()
            rows.update((row.id, row) for row in build_rows)
    edges = db.query(Edge).filter(Edge.source.in_(rows), Edge.target.in_(rows)).all() if rows else []
    nodes = [{"id": row.id, "kind": row.kind, "label": row.label, **json.loads(row.data)} for row in rows.values()]
    links = [{"source": row.source, "target": row.target, "relation": row.relation, **json.loads(row.data)} for row in edges]
    nodes, links, _ = select_visible_page(nodes, links, query, limit=limit)
    pagination = dict(mode="repositories", offset=offset, limit=limit, total=total,
                      hasMore=offset + limit < total,
                      nextCursor=encode_cursor("repositories", offset + limit) if offset + limit < total else None)
    return nodes, links, pagination
