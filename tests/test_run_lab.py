import copy
import json

import httpx
import pytest

from app.lab_matrix import build_plan
from scripts.bootstrap import run_lab as runner
from scripts.bootstrap.expand_lab import _marker, vcs_id


class Clock:
    def __init__(self):
        self.now = 0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def detail(plan, config):
    return {
        "id": config["id"], "projectId": config["project_id"], "paused": False,
        "description": _marker(plan, "config", config["id"], config),
        "steps": {"step": [{"type": "simpleRunner", "properties": {"property": [
            {"name": "script.content", "value": config["script"]},
            {"name": "use.custom.script", "value": "true"},
        ]}}]},
        "vcs-root-entries": {"vcs-root-entry": [{"vcs-root": {"id": vcs_id(slug)}, "checkout-rules": config["checkout_rules"].get(slug, "")} for slug in config["repository_slugs"]]},
        "snapshot-dependencies": {"snapshot-dependency": [{"source-buildType": {"id": identity}} for identity in config["dependencies"]]},
        "settings": {"property": [{"name": "artifactRules", "value": config["artifacts"]}]},
    }


class TeamCity:
    def __init__(self, plan, histories=None):
        self.plan = plan
        self.configs = {item["id"]: item for item in plan["configs"]}
        self.history = {identity: list(statuses) for identity, statuses in (histories or {}).items()}
        self.details = {identity: detail(plan, config) for identity, config in self.configs.items()}
        self.calls, self.posts = [], []
        self.active = {}
        self.return_status = {}
        self.queue_id = None

    def __call__(self, request):
        self.calls.append(request)
        assert request.method in {"GET", "POST"}
        if request.method == "POST":
            assert request.url.path.endswith("/app/rest/buildQueue")
            payload = json.loads(request.content)
            identity = payload["buildType"]["id"]
            assert identity in self.configs and identity.startswith("LabMatrix_")
            self.posts.append(identity)
            status = self.return_status.get(identity, self.configs[identity]["expected"]["status"])
            self.history.setdefault(identity, []).insert(0, status)
            return httpx.Response(200, json={"id": self.queue_id or len(self.posts)})
        if "/buildTypes/id:" in request.url.path:
            return httpx.Response(200, json=self.details[request.url.path.split("id:", 1)[1]])
        locator = request.url.params["locator"]
        identity = locator.split("id:", 1)[1].split(")", 1)[0]
        if request.url.path.endswith("/buildQueue"):
            return httpx.Response(200, json={"count": self.active.get(identity, 0)})
        assert "defaultFilter:false" in locator
        if "state:running" in locator:
            return httpx.Response(200, json={"count": 0})
        assert "count:5" in locator and "state:finished" in locator
        return httpx.Response(200, json={"build": [{"id": index + 1, "buildTypeId": identity, "status": status, "state": "finished"} for index, status in enumerate(self.history.get(identity, [])[:5])]})


def run(server, **kwargs):
    clock = Clock()
    with httpx.Client(base_url="http://localhost:18111/tc", transport=httpx.MockTransport(server)) as client:
        return runner.run_lab(client, server.plan, clock=clock.time, sleep=clock.sleep, **kwargs)


def test_default_plan_is_read_only_even_when_history_is_missing():
    plan = build_plan("fixture-space")
    server = TeamCity(plan)
    report = run(server, config_ids=["LabMatrix_Npm_Test"])
    assert report["mode"] == "plan" and report["complete"] is False
    assert report["queueRequests"] == 0 and not server.posts
    assert all(request.method == "GET" for request in server.calls)
    assert all(request.url.path.startswith("/tc/app/rest/") for request in server.calls)


def test_apply_keeps_one_per_config_until_three_completed_builds():
    server = TeamCity(build_plan("fixture-space"))
    report = run(server, apply=True, config_ids=["LabMatrix_Npm_Test"])
    assert report["complete"] is True and report["queueRequests"] == 3
    assert report["configurations"][0]["matching"] == 3
    assert server.posts == ["LabMatrix_Npm_Test"] * 3


def test_fixture_override_requires_five_maven_builds_and_checks_dependencies():
    server = TeamCity(build_plan("fixture-space"))
    report = run(server, apply=True, config_ids=["LabMatrix_Maven_Build"])
    assert report["queueRequests"] == 5
    detail_paths = [request.url.path for request in server.calls if "/buildTypes/id:" in request.url.path]
    assert any(path.endswith("id:LabMatrix_Maven_Test") for path in detail_paths)
    assert all(identity == "LabMatrix_Maven_Build" for identity in server.posts)


def test_old_wrong_status_must_leave_entire_latest_five_window():
    identity = "LabMatrix_Npm_Test"
    server = TeamCity(build_plan("fixture-space"), {identity: ["FAILURE", "SUCCESS", "SUCCESS", "SUCCESS", "SUCCESS"]})
    report = run(server, apply=True, config_ids=[identity])
    assert report["queueRequests"] == 5 and report["configurations"][0]["matching"] == 5


def test_intentional_failure_is_required_success_is_not_accepted():
    identity = "LabMatrix_Negative_FailedPush"
    server = TeamCity(build_plan("fixture-space"), {identity: ["SUCCESS"]})
    report = run(server, apply=True, config_ids=[identity])
    assert report["complete"] is True and report["queueRequests"] == 5
    assert report["configurations"][0]["expectedStatus"] == "FAILURE"


def test_existing_correct_idle_history_is_not_requeued():
    identity = "LabMatrix_Npm_Test"
    server = TeamCity(build_plan("fixture-space"), {identity: ["SUCCESS"] * 5})
    report = run(server, apply=True, config_ids=[identity])
    assert report["complete"] is True and not server.posts


def test_active_queue_is_never_duplicated_or_cancelled_and_times_out():
    identity = "LabMatrix_Npm_Test"
    server = TeamCity(build_plan("fixture-space"))
    server.active[identity] = 1
    with pytest.raises(runner.RunError, match="timeout"):
        run(server, apply=True, config_ids=[identity], timeout=2, interval=1)
    assert not server.posts
    assert all(request.method == "GET" for request in server.calls)


def test_queue_budget_stops_bad_fixture_even_with_deduplicated_ids():
    identity = "LabMatrix_Npm_Test"
    server = TeamCity(build_plan("fixture-space"))
    server.return_status[identity] = "FAILURE"
    server.queue_id = 123
    with pytest.raises(runner.RunError, match="safety limit"):
        run(server, apply=True, config_ids=[identity], max_new_builds=3)
    assert len(server.posts) == 3


def test_repeated_queue_ids_are_reported_once_but_attempts_are_counted():
    identity = "LabMatrix_Npm_Test"
    server = TeamCity(build_plan("fixture-space"))
    server.queue_id = 123
    report = run(server, apply=True, config_ids=[identity])
    assert report["queueRequests"] == 3 and report["queuedBuildIds"] == ["123"]


@pytest.mark.parametrize("field,value", [("description", "unowned"), ("projectId", "Demo"), ("paused", True), ("steps", {"step": []})])
def test_all_selected_ownership_is_checked_before_first_queue(field, value):
    server = TeamCity(build_plan("fixture-space"))
    server.details["LabMatrix_Npm_Test"][field] = value
    with pytest.raises(runner.RunError, match="mismatch|paused"):
        run(server, apply=True, config_ids=["LabMatrix_Negative_Echo", "LabMatrix_Npm_Test"])
    assert not server.posts


def test_changed_snapshot_dependency_blocks_queueing_parent():
    server = TeamCity(build_plan("fixture-space"))
    server.details["LabMatrix_Maven_Test"]["description"] = "some other owner"
    with pytest.raises(runner.RunError, match="LabMatrix_Maven_Test"):
        run(server, apply=True, config_ids=["LabMatrix_Maven_Build"])
    assert not server.posts


@pytest.mark.parametrize("identity", ["Demo_01_Build", "Unknown", "LabMatrix_Fake"])
def test_unknown_or_baseline_configuration_never_reaches_teamcity(identity):
    server = TeamCity(build_plan("fixture-space"))
    with pytest.raises(runner.RunError, match="Only configuration IDs"):
        run(server, apply=True, config_ids=[identity])
    assert not server.calls


@pytest.mark.parametrize("url", ["https://teamcity.company.example", "http://localhost.evil.example", "http://user:password@localhost", "http://localhost/?token=secret"])
def test_nonlocal_or_credential_url_rejected_without_requests(url):
    server = TeamCity(build_plan("fixture-space"))
    with httpx.Client(base_url=url, transport=httpx.MockTransport(server)) as client:
        with pytest.raises(runner.RunError, match="local TeamCity"):
            runner.run_lab(client, server.plan, apply=True)
    assert not server.calls


@pytest.mark.parametrize("kwargs", [{"max_new_builds": 201}, {"max_new_builds": 0}, {"min_builds": 6}, {"timeout": 0}, {"interval": 61}])
def test_unsafe_limits_rejected_without_requests(kwargs):
    server = TeamCity(build_plan("fixture-space"))
    with pytest.raises(runner.RunError):
        run(server, apply=True, **kwargs)
    assert not server.calls


def test_cli_does_not_fall_back_to_reader_token(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TC_ADMIN_TOKEN", raising=False)
    env = tmp_path / ".env"
    env.write_text("BITBUCKET_WORKSPACE=fixture-space\nTEAMCITY_TOKEN=reader-secret\n", encoding="utf-8")
    assert runner.main(["--env-file", str(env), "--apply"]) == 1
    output = capsys.readouterr()
    assert "TC_ADMIN_TOKEN is required" in output.err
    assert "reader-secret" not in output.err


def test_cli_apply_is_explicit_and_tokens_never_enter_reports(tmp_path, monkeypatch, capsys):
    env = tmp_path / ".env"
    env.write_text("BITBUCKET_WORKSPACE=fixture-space\nTC_ADMIN_TOKEN=admin-secret\nTEAMCITY_BOOTSTRAP_URL=http://localhost:18111\n", encoding="utf-8")
    for name in ("BITBUCKET_WORKSPACE", "TC_ADMIN_TOKEN", "TEAMCITY_BOOTSTRAP_URL"):
        monkeypatch.delenv(name, raising=False)
    observed = []
    def fake_run(client, plan, **kwargs):
        assert client.headers["authorization"] == "Bearer admin-secret"
        observed.append(kwargs["apply"])
        return {"complete": False, "queueRequests": 0}
    monkeypatch.setattr(runner, "run_lab", fake_run)
    assert runner.main(["--env-file", str(env)]) == 0
    assert runner.main(["--env-file", str(env), "--apply"]) == 0
    assert observed == [False, True]
    output = capsys.readouterr()
    assert "admin-secret" not in output.out + output.err
