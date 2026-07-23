from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.operations import prometheus_metrics, scan_duration_seconds


def test_scan_duration_and_prometheus_metrics():
    now = datetime(2026, 7, 23, tzinfo=timezone.utc)
    scan = SimpleNamespace(status="success", started_at=now - timedelta(seconds=12), finished_at=now - timedelta(seconds=2))
    failed = SimpleNamespace(status="failed", started_at=now, finished_at=now)
    assert scan_duration_seconds(scan) == 10
    result = prometheus_metrics([scan, failed], 154, 196, now)
    assert 'artifact_graph_scans_total{status="success"} 1' in result
    assert 'artifact_graph_scans_total{status="failed"} 1' in result
    assert "artifact_graph_last_scan_duration_seconds 10.000" in result
    assert "artifact_graph_last_scan_age_seconds 2.000" in result
    assert "artifact_graph_nodes 154" in result
    assert "artifact_graph_edges 196" in result
