import httpx

from app.teamcity_builds import queue_to_target, run_to_target


def test_queues_only_missing_builds_to_target():
    posts = []

    def handler(request):
        if request.url.path.endswith("/buildTypes"):
            return httpx.Response(200, json={"buildType": [{"id": "Demo_01"}, {"id": "Demo_02"}]})
        if request.method == "GET":
            build_type = "Demo_01" if "Demo_01" in request.url.params["locator"] else "Demo_02"
            active = request.url.path.endswith("/buildQueue") or "state:running" in request.url.params["locator"]
            count = 1 if active and request.url.path.endswith("/buildQueue") and build_type == "Demo_01" else (0 if active else (2 if build_type == "Demo_01" else 5))
            return httpx.Response(200, json={"count": count})
        posts.append(request.read().decode())
        return httpx.Response(200, json={"id": 1})

    with httpx.Client(base_url="http://teamcity", transport=httpx.MockTransport(handler)) as client:
        assert queue_to_target(client, "Demo", 5) == {"Demo_01": 2, "Demo_02": 0}
    assert len(posts) == 2
    assert all('"id":"Demo_01"' in body for body in posts)


def test_run_to_target_refills_after_teamcity_deduplicated_queue(monkeypatch):
    finished = 0
    active = 0
    posts = 0

    def handler(request):
        nonlocal finished, active, posts
        if request.url.path.endswith("/buildTypes"):
            return httpx.Response(200, json={"buildType": [{"id": "Demo_01"}]})
        if request.method == "POST":
            posts += 1
            active = 1
            return httpx.Response(200, json={"id": posts})
        if request.url.path.endswith("/buildQueue"):
            return httpx.Response(200, json={"count": active})
        if "state:running" in request.url.params["locator"]:
            return httpx.Response(200, json={"count": 0})
        if active:
            finished += 1
            active = 0
        return httpx.Response(200, json={"count": finished})

    monkeypatch.setattr("app.teamcity_builds.time.sleep", lambda _: None)
    with httpx.Client(base_url="http://teamcity", transport=httpx.MockTransport(handler)) as client:
        counts, scheduled = run_to_target(client, "Demo", 3, interval_seconds=0)
    assert counts == {"Demo_01": 3}
    assert scheduled == 3
    assert posts == 3


def test_run_to_target_waits_until_created_queue_is_idle(monkeypatch):
    queue_checks = 0

    def handler(request):
        nonlocal queue_checks
        if request.url.path.endswith("/buildTypes"):
            return httpx.Response(200, json={"buildType": [{"id": "Demo_01"}]})
        if request.url.path.endswith("/buildQueue"):
            queue_checks += 1
            return httpx.Response(200, json={"count": 1 if queue_checks == 1 else 0})
        if "state:running" in request.url.params["locator"]:
            return httpx.Response(200, json={"count": 0})
        return httpx.Response(200, json={"count": 3})

    monkeypatch.setattr("app.teamcity_builds.time.sleep", lambda _: None)
    with httpx.Client(base_url="http://teamcity", transport=httpx.MockTransport(handler)) as client:
        counts, scheduled = run_to_target(client, "Demo", 3, interval_seconds=0)
    assert counts == {"Demo_01": 3}
    assert scheduled == 0
    assert queue_checks == 2
