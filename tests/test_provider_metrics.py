from app.provider_metrics import ProviderMetrics


def test_provider_metrics_export_low_cardinality_prometheus_series():
    metrics = ProviderMetrics()
    metrics.record("teamcity", "success", 0.2)
    metrics.record("teamcity", "retry", 0.1, retry=True)
    metrics.record("bitbucket", "error", 0.3)
    text = metrics.prometheus()
    assert 'artifact_graph_provider_requests_total{provider="teamcity",status="success"} 1' in text
    assert 'artifact_graph_provider_retries_total{provider="teamcity"} 1' in text
    assert 'artifact_graph_provider_failures_total{provider="bitbucket"} 1' in text
    assert "item_id" not in text
