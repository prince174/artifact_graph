import time


def build_type_ids(client, project_id: str) -> list[str]:
    response = client.get(
        "/app/rest/buildTypes",
        params={"locator": f"affectedProject:(id:{project_id})", "fields": "buildType(id)"},
    )
    response.raise_for_status()
    return sorted(item["id"] for item in response.json().get("buildType", []))


def finished_count(client, build_type_id: str) -> int:
    response = client.get(
        "/app/rest/builds",
        params={"locator": f"buildType:(id:{build_type_id}),state:finished,defaultFilter:false", "fields": "count"},
    )
    response.raise_for_status()
    return int(response.json().get("count", 0))


def active_count(client, build_type_id: str) -> int:
    running = client.get(
        "/app/rest/builds",
        params={"locator": f"buildType:(id:{build_type_id}),state:running,defaultFilter:false", "fields": "count"},
    )
    running.raise_for_status()
    queued = client.get(
        "/app/rest/buildQueue",
        params={"locator": f"buildType:(id:{build_type_id})", "fields": "count"},
    )
    queued.raise_for_status()
    return int(running.json().get("count", 0)) + int(queued.json().get("count", 0))


def queue_to_target(client, project_id: str, target: int) -> dict[str, int]:
    queued = {}
    for build_type_id in build_type_ids(client, project_id):
        missing = max(0, target - finished_count(client, build_type_id) - active_count(client, build_type_id))
        for _ in range(missing):
            response = client.post("/app/rest/buildQueue", json={"buildType": {"id": build_type_id}})
            response.raise_for_status()
        queued[build_type_id] = missing
    return queued


def wait_for_target(client, project_id: str, target: int, timeout_seconds: int = 1800, interval_seconds: int = 5) -> dict[str, int]:
    deadline = time.monotonic() + timeout_seconds
    while True:
        counts = {build_type_id: finished_count(client, build_type_id) for build_type_id in build_type_ids(client, project_id)}
        if counts and all(count >= target for count in counts.values()):
            return counts
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Build target not reached: {counts}")
        time.sleep(interval_seconds)


def run_to_target(client, project_id: str, target: int, timeout_seconds: int = 1800, interval_seconds: int = 5) -> tuple[dict[str, int], int]:
    """Keep one build per configuration active until every target is reached.

    TeamCity can deduplicate identical queued builds, so posting all missing builds
    at once is not sufficient for reproducible fixture history.
    """
    deadline = time.monotonic() + timeout_seconds
    scheduled = 0
    build_types = build_type_ids(client, project_id)
    while True:
        counts = {build_type_id: finished_count(client, build_type_id) for build_type_id in build_types}
        if counts and all(count >= target for count in counts.values()):
            if all(active_count(client, build_type_id) == 0 for build_type_id in build_types):
                return counts, scheduled
        for build_type_id in build_types:
            if counts[build_type_id] < target and active_count(client, build_type_id) == 0:
                response = client.post("/app/rest/buildQueue", json={"buildType": {"id": build_type_id}})
                response.raise_for_status()
                scheduled += 1
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Build target not reached: {counts}")
        time.sleep(interval_seconds)
