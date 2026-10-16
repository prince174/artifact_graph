"""Load a repository search page and its graph without materializing the full DB."""
import json
import re

from sqlalchemy import JSON, case, cast, func, or_, select

from .models import Node, Edge
from .subgraph import decode_cursor, encode_cursor, select_visible_page


def repository_search(db, query, cursor="", limit=10, build_limit=None):
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
            query_builds = db.query(Node).join(Edge, Edge.target == Node.id).filter(
                Edge.source.in_(configs), Edge.relation == "ran_as", Node.kind == "build")
            if build_limit is not None:
                finished = func.coalesce(cast(Node.data, JSON)["finishDate"].as_string(), "")
                if db.bind.dialect.name == "sqlite":
                    # SQLite CAST(... AS JSON) does not preserve JSON text.
                    finished = func.coalesce(func.json_extract(Node.data, "$.finishDate"), "")
                    def number(label):
                        match = re.search(r"\d+", label or "")
                        return (match.group().lstrip("0") or "0") if match else ""
                    db.connection().connection.driver_connection.create_function("build_number", 1, number)
                    sequence = func.build_number(Node.label)
                else:
                    digits = func.substring(Node.label, r"[0-9]+")
                    sequence = case((digits.is_(None), ""), else_=func.coalesce(func.nullif(func.ltrim(digits, "0"), ""), "0"))
                ranked = query_builds.with_entities(
                    Node.id.label("node_id"),
                    func.row_number().over(partition_by=Edge.source,
                                           order_by=(finished.desc(), func.length(sequence).desc(), sequence.desc(), Node.id.desc())).label("rank"),
                ).subquery()
                build_rows = db.query(Node).filter(Node.id.in_(select(ranked.c.node_id).where(ranked.c.rank <= build_limit))).all()
            else:
                build_rows = query_builds.all()
            rows.update((row.id, row) for row in build_rows)
    edges = db.query(Edge).filter(Edge.source.in_(rows), Edge.target.in_(rows)).all() if rows else []
    nodes = [{"id": row.id, "kind": row.kind, "label": row.label, **json.loads(row.data)} for row in rows.values()]
    links = [{"source": row.source, "target": row.target, "relation": row.relation, **json.loads(row.data)} for row in edges]
    nodes, links, _ = select_visible_page(nodes, links, query, limit=limit)
    pagination = dict(mode="repositories", offset=offset, limit=limit, total=total,
                      hasMore=offset + limit < total,
                      nextCursor=encode_cursor("repositories", offset + limit) if offset + limit < total else None)
    return nodes, links, pagination
