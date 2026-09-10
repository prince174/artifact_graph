import hashlib
import json


def canonical_graph(nodes: list[dict], edges: list[dict]) -> tuple[str, str]:
    document = {
        "nodes": sorted(nodes, key=lambda item: item["id"]),
        "edges": sorted(edges, key=lambda item: (item["source"], item["target"], item["relation"])),
    }
    payload = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return payload, hashlib.sha256(payload.encode()).hexdigest()


def diff_graphs(before: dict, after: dict) -> dict:
    before_nodes = {item["id"]: item for item in before.get("nodes", [])}
    after_nodes = {item["id"]: item for item in after.get("nodes", [])}
    edge_key = lambda item: (item["source"], item["target"], item["relation"])
    before_edges = {edge_key(item): item for item in before.get("edges", [])}
    after_edges = {edge_key(item): item for item in after.get("edges", [])}
    changed_nodes = sorted(key for key in before_nodes.keys() & after_nodes.keys() if before_nodes[key] != after_nodes[key])
    changed_edges = sorted(key for key in before_edges.keys() & after_edges.keys() if before_edges[key] != after_edges[key])
    output_fields = ("hasImagePush", "hasSbom", "pushedImages", "sbomArtifacts")
    changed_details = [
        {
            "id": key,
            "before": before_nodes[key],
            "after": after_nodes[key],
            "fields": sorted(
                field for field in (set(before_nodes[key]) | set(after_nodes[key])) - {"id"}
                if before_nodes[key].get(field) != after_nodes[key].get(field)
            ),
        }
        for key in changed_nodes
    ]
    output_changes = [
        {
            "id": key,
            "before": {field: before_nodes[key].get(field) for field in output_fields},
            "after": {field: after_nodes[key].get(field) for field in output_fields},
        }
        for key in changed_nodes
        if any(before_nodes[key].get(field) != after_nodes[key].get(field) for field in output_fields)
    ]
    return {
        "nodes": {"added": sorted(after_nodes.keys() - before_nodes.keys()), "removed": sorted(before_nodes.keys() - after_nodes.keys()), "changed": changed_nodes},
        "edges": {"added": [list(key) for key in sorted(after_edges.keys() - before_edges.keys())], "removed": [list(key) for key in sorted(before_edges.keys() - after_edges.keys())], "changed": [list(key) for key in changed_edges]},
        "details": {
            "added": [after_nodes[key] for key in sorted(after_nodes.keys() - before_nodes.keys())],
            "removed": [before_nodes[key] for key in sorted(before_nodes.keys() - after_nodes.keys())],
            "changed": changed_details,
        },
        "outputChanges": output_changes,
    }


def snapshot_timeline(snapshots: list[dict]) -> list[dict]:
    ordered = sorted(snapshots, key=lambda item: item["id"])
    timeline = []
    previous = None
    for snapshot in ordered:
        entry = {key: snapshot[key] for key in ("id", "scanId", "createdAt", "nodeCount", "edgeCount", "hash")}
        if previous is None:
            entry["previousId"] = None
            entry["changes"] = {"nodesAdded": 0, "nodesRemoved": 0, "nodesChanged": 0, "edgesAdded": 0, "edgesRemoved": 0, "edgesChanged": 0, "outputChanges": 0}
        else:
            diff = diff_graphs(previous["graph"], snapshot["graph"])
            entry["previousId"] = previous["id"]
            entry["changes"] = {
                "nodesAdded": len(diff["nodes"]["added"]),
                "nodesRemoved": len(diff["nodes"]["removed"]),
                "nodesChanged": len(diff["nodes"]["changed"]),
                "edgesAdded": len(diff["edges"]["added"]),
                "edgesRemoved": len(diff["edges"]["removed"]),
                "edgesChanged": len(diff["edges"]["changed"]),
                "outputChanges": len(diff["outputChanges"]),
            }
        timeline.append(entry)
        previous = snapshot
    return list(reversed(timeline))
