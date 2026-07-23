from datetime import datetime, timezone


def scan_duration_seconds(scan) -> float:
    if not scan or not scan.finished_at:
        return 0.0
    started, finished = scan.started_at, scan.finished_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=timezone.utc)
    return max(0.0, (finished - started).total_seconds())


def prometheus_metrics(scans, node_count: int, edge_count: int, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    latest = scans[0] if scans else None
    success = sum(scan.status == "success" for scan in scans)
    failed = sum(scan.status == "failed" for scan in scans)
    age = 0.0
    if latest and latest.finished_at:
        finished = latest.finished_at
        if finished.tzinfo is None:
            finished = finished.replace(tzinfo=timezone.utc)
        age = max(0.0, (now - finished).total_seconds())
    return "\n".join([
        "# TYPE artifact_graph_scans_total counter",
        f'artifact_graph_scans_total{{status="success"}} {success}',
        f'artifact_graph_scans_total{{status="failed"}} {failed}',
        "# TYPE artifact_graph_last_scan_duration_seconds gauge",
        f"artifact_graph_last_scan_duration_seconds {scan_duration_seconds(latest):.3f}",
        "# TYPE artifact_graph_last_scan_age_seconds gauge",
        f"artifact_graph_last_scan_age_seconds {age:.3f}",
        "# TYPE artifact_graph_nodes gauge",
        f"artifact_graph_nodes {node_count}",
        "# TYPE artifact_graph_edges gauge",
        f"artifact_graph_edges {edge_count}",
        "",
    ])
