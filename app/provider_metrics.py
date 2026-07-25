from collections import Counter, defaultdict


class ProviderMetrics:
    def __init__(self):
        self.requests = Counter()
        self.retries = Counter()
        self.failures = Counter()
        self.duration = defaultdict(float)

    def record(self, provider: str, status: str, duration: float, *, retry: bool = False):
        self.requests[(provider, status)] += 1
        self.duration[provider] += duration
        if retry:
            self.retries[provider] += 1
        if status == "error":
            self.failures[provider] += 1

    def prometheus(self) -> str:
        lines = ["# TYPE artifact_graph_provider_requests_total counter"]
        for (provider, status), count in sorted(self.requests.items()):
            lines.append(f'artifact_graph_provider_requests_total{{provider="{provider}",status="{status}"}} {count}')
        lines.append("# TYPE artifact_graph_provider_retries_total counter")
        for provider, count in sorted(self.retries.items()):
            lines.append(f'artifact_graph_provider_retries_total{{provider="{provider}"}} {count}')
        lines.append("# TYPE artifact_graph_provider_failures_total counter")
        for provider, count in sorted(self.failures.items()):
            lines.append(f'artifact_graph_provider_failures_total{{provider="{provider}"}} {count}')
        lines.append("# TYPE artifact_graph_provider_request_duration_seconds_total counter")
        for provider, duration in sorted(self.duration.items()):
            lines.append(f'artifact_graph_provider_request_duration_seconds_total{{provider="{provider}"}} {duration:.6f}')
        return "\n".join(lines) + "\n"


provider_metrics = ProviderMetrics()
