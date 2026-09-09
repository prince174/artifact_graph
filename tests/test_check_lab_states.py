import copy
import json

import httpx
import pytest

from app.lab_matrix import build_plan
from app.teamcity_setup import command_line_script_step
from scripts.bootstrap import check_lab_states as checker
from scripts.bootstrap.expand_lab import ProvisionError, _marker, vcs_id


class Clock:
    def __init__(self):
        self.now = 0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(seconds, 0.1)


class Lab:
    def __init__(self):
        self.plan = build_plan("fixture-space")
        self.calls = []
        self.details = {}
        for config in self.plan["configs"]:
            self.details[config["id"]] = {
                "id": config["id"], "name": config["name"], "projectId": config["project_id"], "paused": False,
                "description": _marker(self.plan, "config", config["id"], config),
                "steps": {"step": [command_line_script_step(config["script"])]},
                "vcs-root-entries": {"vcs-root-entry": [{"vcs-root": {"id": vcs_id(slug)}, "checkout-rules": config["checkout_rules"].get(slug, "")} for slug in config["repository_slugs"]]},
                "snapshot-dependencies": {"snapshot-dependency": [{"source-buildType": {"id": identity}} for identity in config["dependencies"]]},
                "settings": {"property": [{"name": "artifactRules", "value": config["artifacts"]}]},
            }
        self.original = copy.deepcopy(self.details)
        self.active_count = 0
        self.queue_count = 0
        self.agents = [{"id": 11, "authorized": True, "connected": True, "enabled": True}]
        self.failure = {"id": 77, "buildTypeId": checker.FAILED, "state": "finished", "status": "FAILURE"}
        self.builds = {}
        self.queued = []
        self.authenticated = False
        self.csrf = "safe-csrf"
        self.scan = 0
        self.refresh_busy = 0
        self.scan_status = "success"
        self.no_scan_progress = False
        self.bad_graph = ""
        self.fault = None
        self.queue_response = None
        self.foreign_build = False
        self.cancel_race = False
        self.queue_returns_all_states = False

    @property
    def writes(self):
        return [request for request in self.calls if request.url.host == "teamcity" and request.method != "GET"]

    def graph_nodes(self):
        nodes = [{"id": "build:77", **self.failure, "visualReason": "failed"}]
        nodes[0]["id"] = "build:77"
        for identity, state in self.builds.items():
            nodes.append({"id": f"build:{identity}", "buildTypeId": checker.ACTIVE, "state": state, "visualReason": state})
        nodes.append({"id": f"build-type:{checker.PAUSED}", "active": not self.details[checker.PAUSED]["paused"], "visualReason": "inactive"})
        if self.bad_graph == "visual":
            nodes[0]["visualReason"] = "success"
        if self.bad_graph == "stale":
            nodes[0]["stale"] = True
        if self.bad_graph == "missing":
            nodes = []
        return nodes

    def __call__(self, request):
        self.calls.append(request)
        path = request.url.path
        if self.fault:
            result = self.fault(request)
            if result is not None:
                return result
        if request.url.host == "graph":
            if path == "/api/version":
                return httpx.Response(200 if self.authenticated else 401, json={})
            if path == "/login":
                assert request.method == "POST"
                self.authenticated = True
                return httpx.Response(303, headers={"Location": "/"})
            if path == "/api/session":
                return httpx.Response(200, json={"csrf": self.csrf})
            if path == "/api/status":
                return httpx.Response(200, json={"mode": "live", "lastScan": {"status": self.scan_status, "finishedAt": str(self.scan)}})
            if path == "/api/refresh":
                assert request.method == "POST" and request.headers["X-CSRF-Token"] == self.csrf
                if not self.no_scan_progress:
                    self.scan += 1
                if self.refresh_busy:
                    self.refresh_busy -= 1
                    return httpx.Response(409)
                return httpx.Response(202)
            if path == "/api/graph":
                assert request.url.params["q"] in checker.QUERIES
                return httpx.Response(200, json={"nodes": self.graph_nodes()})
            raise AssertionError(f"Unexpected graph path {path}")
        if path.startswith("/app/rest/buildTypes/id:"):
            identity, _, field = path.split("id:", 1)[1].partition("/")
            assert identity in {checker.ACTIVE, checker.PAUSED, checker.FAILED}
            if request.method == "GET":
                return httpx.Response(200, json=self.details[identity])
            assert request.method == "PUT"
            if field == "paused":
                assert identity == checker.PAUSED
                assert request.headers["accept"] == request.headers["content-type"] == "text/plain"
                self.details[identity][field] = request.content == b"true"
            else:
                assert identity == checker.ACTIVE and field == "steps"
                self.details[identity][field] = json.loads(request.content)
            return httpx.Response(200, json={})
        if path == "/app/rest/agents":
            assert request.url.params["locator"] == "authorized:true,connected:true"
            return httpx.Response(200, json={"agent": self.agents})
        if path == "/app/rest/builds":
            if "status:FAILURE" in request.url.params["locator"]:
                return httpx.Response(200, json={"build": [self.failure] if self.failure else []})
            return httpx.Response(200, json={"count": self.active_count})
        if path == "/app/rest/buildQueue":
            if request.method == "GET":
                return httpx.Response(200, json={"count": self.queue_count})
            assert request.method == "POST"
            assert json.loads(request.content)["buildType"]["id"] == checker.ACTIVE
            assert request.url.params["fields"] == "id,buildTypeId"
            identity = 101 + len(self.queued)
            self.queued.append(identity)
            self.builds[identity] = "running" if len(self.queued) == 1 else "queued"
            return httpx.Response(200, json=self.queue_response or {"id": identity, "buildTypeId": checker.ACTIVE})
        if path.startswith(("/app/rest/buildQueue/id:", "/app/rest/builds/id:")):
            identity = int(path.split("id:")[1])
            assert identity in self.queued
            in_queue = "/buildQueue/" in path
            state = self.builds[identity]
            if request.method == "DELETE":
                assert in_queue
                if self.cancel_race:
                    self.cancel_race = False
                    self.builds[identity] = "running"
                    return httpx.Response(404)
                self.builds[identity] = "removed"
                return httpx.Response(204)
            if request.method == "POST":
                assert not in_queue and json.loads(request.content)["readdIntoQueue"] is False
                self.builds[identity] = "finished"
                return httpx.Response(200, json={})
            if (in_queue and state != "queued" and not self.queue_returns_all_states) or state == "removed":
                return httpx.Response(404)
            return httpx.Response(200, json={"id": identity, "state": state, "buildTypeId": "Demo_Foreign" if self.foreign_build else checker.ACTIVE})
        raise AssertionError(f"Unexpected request {request.method} {path}")


@pytest.fixture
def lab(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(checker.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(checker.time, "sleep", clock.sleep)
    return Lab()


def run(lab, **kwargs):
    with (httpx.Client(base_url="http://teamcity:8111", transport=httpx.MockTransport(lab), headers={"Accept": "application/json"}) as tc,
          httpx.Client(base_url="http://graph:8080", transport=httpx.MockTransport(lab)) as graph):
        return checker.check_lab_states(lab.plan, tc, graph, username="root", password="private-password", timeout=2, **kwargs)


def restored(lab):
    assert lab.details[checker.ACTIVE]["steps"] == lab.original[checker.ACTIVE]["steps"]
    assert lab.details[checker.PAUSED]["paused"] is False
    assert any(request.url.path == "/api/refresh" for request in lab.calls)


def test_default_mode_only_reads_teamcity_and_does_not_refresh(lab):
    report = run(lab)
    assert report["mode"] == "plan" and report["failedBuildId"] == 77
    assert not lab.writes and not lab.queued
    assert not any(request.url.path == "/api/refresh" for request in lab.calls)


def test_apply_checks_all_states_and_restores_everything(lab):
    report = run(lab, apply=True)
    assert report["verified"] and report["cleanup"] == "completed"
    assert report["cancelledHistoryRemains"] and report["runningBuildId"] == 101 and report["queuedBuildId"] == 102
    assert report["visualReasons"] == ["running", "queued", "failed", "inactive"]
    assert report["expectedColors"]["running"] == report["expectedColors"]["queued"] == "#f2d675"
    assert report["expectedColors"]["failed"] == "#e99a95" and report["expectedColors"]["inactive"] == "#7d8590"
    assert lab.builds == {101: "finished", 102: "removed"}
    assert {request.url.params["q"] for request in lab.calls if request.url.path == "/api/graph"} == set(checker.QUERIES)
    assert lab.scan == 2
    restored(lab)


@pytest.mark.parametrize("identity", [checker.ACTIVE, checker.PAUSED, checker.FAILED])
@pytest.mark.parametrize("field,value", [("id", "foreign"), ("description", "foreign"), ("projectId", "Demo"), ("paused", True), ("steps", {"step": []})])
def test_ownership_or_settings_mismatch_never_writes(lab, identity, field, value):
    lab.details[identity][field] = value
    with pytest.raises(checker.StateCheckError, match="mismatch"):
        run(lab, apply=True)
    assert not lab.writes


@pytest.mark.parametrize("field,value", [("active_count", 1), ("queue_count", 1), ("active_count", None), ("active_count", True), ("active_count", -1)])
def test_busy_or_malformed_active_counts_block_all_mutations(lab, field, value):
    setattr(lab, field, value)
    with pytest.raises(checker.StateCheckError, match="running or queued|invalid active"):
        run(lab, apply=True)
    assert not lab.writes


@pytest.mark.parametrize("agents", [[], [{"id": 1, "authorized": True, "connected": True, "enabled": False}], [{"id": 1}, {"id": 2}]])
def test_exactly_one_enabled_authorized_connected_agent_is_required(lab, agents):
    lab.agents = agents
    with pytest.raises(checker.StateCheckError, match="exactly one"):
        run(lab, apply=True)
    assert not lab.writes


@pytest.mark.parametrize("failure", [None, {"id": 77, "status": "SUCCESS"}, {"id": 77, "status": "FAILURE", "state": "running", "buildTypeId": checker.FAILED}])
def test_failed_fixture_must_already_exist_and_be_finished(lab, failure):
    lab.failure = failure
    with pytest.raises(checker.StateCheckError, match="completed"):
        run(lab, apply=True)
    assert not lab.writes


@pytest.mark.parametrize("url", ["https://production.example", "http://localhost.evil.example", "http://secret:token@localhost", "http://localhost/?token=secret"])
def test_nonlocal_or_credential_urls_rejected_before_requests(lab, url):
    with (httpx.Client(base_url=url, transport=httpx.MockTransport(lab)) as tc,
          httpx.Client(base_url="http://graph:8080", transport=httpx.MockTransport(lab)) as graph):
        with pytest.raises(checker.StateCheckError, match="local lab"):
            checker.check_lab_states(lab.plan, tc, graph, username="root", password="private-password", apply=True)
    assert not lab.calls


@pytest.mark.parametrize("bad_graph", ["visual", "stale", "missing"])
def test_wrong_visual_reason_stale_or_missing_nodes_fail_and_cleanup(lab, bad_graph):
    lab.bad_graph = bad_graph
    with pytest.raises(checker.StateCheckError, match="Graph searches"):
        run(lab, apply=True)
    assert lab.builds == {101: "finished", 102: "removed"}
    restored(lab)


def test_queue_cancellation_race_cancels_only_the_same_created_id(lab):
    lab.cancel_race = True
    assert run(lab, apply=True)["verified"]
    assert lab.builds == {101: "finished", 102: "finished"}
    cancellations = [request.url.path for request in lab.writes if request.method == "DELETE" or request.url.path.startswith("/app/rest/builds/id:")]
    assert cancellations == ["/app/rest/buildQueue/id:102", "/app/rest/builds/id:102", "/app/rest/builds/id:101"]


def test_queue_endpoint_resolves_running_and_finished_during_full_check(lab):
    lab.queue_returns_all_states = True
    report = run(lab, apply=True)
    assert report["verified"] and report["cleanup"] == "completed"
    assert lab.builds == {101: "finished", 102: "removed"}
    assert not any(request.method == "DELETE" and request.url.path == "/app/rest/buildQueue/id:101" for request in lab.writes)
    restored(lab)


def test_cancel_race_uses_running_state_returned_by_queue_endpoint(lab):
    lab.queue_returns_all_states = True
    lab.cancel_race = True
    assert run(lab, apply=True)["verified"]
    assert lab.builds == {101: "finished", 102: "finished"}
    cancellations = [request.url.path for request in lab.writes if request.method == "DELETE" or request.url.path.startswith("/app/rest/builds/id:")]
    assert cancellations == ["/app/rest/buildQueue/id:102", "/app/rest/builds/id:102", "/app/rest/builds/id:101"]


def test_finished_build_resolved_by_queue_endpoint_is_not_cancelled(lab):
    lab.queue_returns_all_states = True
    lab.queued = [101]
    lab.builds[101] = "finished"
    with httpx.Client(base_url="http://teamcity:8111", transport=httpx.MockTransport(lab)) as tc:
        assert checker._state(tc, 101)["state"] == "finished"
        checker._cancel_created(tc, 101, 2, 1)
    assert not lab.writes


@pytest.mark.parametrize("state", [None, "", "removed", "unknown", False, {}, []])
@pytest.mark.parametrize("queue_resolves", [False, True])
def test_invalid_state_from_either_endpoint_is_never_used_for_cleanup(state, queue_resolves):
    calls = []
    def handle(request):
        calls.append(request)
        if "/buildQueue/" in request.url.path and not queue_resolves:
            return httpx.Response(404)
        return httpx.Response(200, json={"id": 101, "buildTypeId": checker.ACTIVE, "state": state})
    with httpx.Client(base_url="http://teamcity:8111", transport=httpx.MockTransport(handle)) as tc:
        with pytest.raises(checker.StateCheckError, match="invalid state"):
            checker._cancel_created(tc, 101, 2, 1)
    assert all(request.method == "GET" for request in calls)


def test_foreign_build_response_is_never_cancelled(lab):
    lab.foreign_build = True
    with pytest.raises(checker.StateCheckError, match="refusing changes"):
        run(lab, apply=True)
    assert not any(request.method == "DELETE" or request.url.path.startswith("/app/rest/builds/id:") for request in lab.writes)
    restored(lab)


def test_failed_cancellation_does_not_skip_other_cancels_or_restoration(lab):
    lab.fault = lambda request: httpx.Response(500, text="SECRET-UPSTREAM-BODY") if request.method == "DELETE" else None
    with pytest.raises(checker.StateCheckError, match="cancel build 102") as error:
        run(lab, apply=True)
    assert "SECRET" not in str(error.value) and "private-password" not in str(error.value)
    assert lab.builds[101] == "finished"
    restored(lab)


def test_failed_step_restore_still_restores_pause_and_refreshes(lab):
    def fault(request):
        if request.method == "PUT" and request.url.path.endswith("/steps") and checker.TEMPORARY_SCRIPT.encode() not in request.content and lab.queued:
            return httpx.Response(500, text="SECRET-UPSTREAM-BODY")
    lab.fault = fault
    with pytest.raises(checker.StateCheckError, match="restore Composite steps") as error:
        run(lab, apply=True)
    assert "verify restored settings" in str(error.value) and "SECRET" not in str(error.value)
    assert lab.details[checker.PAUSED]["paused"] is False and lab.scan == 2


def test_exception_after_first_change_restores_and_redacts(lab):
    def fault(request):
        if request.method == "PUT" and request.url.path.endswith("/steps") and not lab.details[checker.PAUSED]["paused"]:
            return None
        if request.method == "PUT" and request.url.path.endswith("/steps") and not lab.queued:
            raise RuntimeError("private-password SECRET")
    lab.fault = fault
    with pytest.raises(checker.StateCheckError) as error:
        run(lab, apply=True)
    assert "RuntimeError" in str(error.value) and "SECRET" not in str(error.value)
    assert lab.details[checker.PAUSED]["paused"] is False
    assert lab.scan == 1 and not lab.queued


def test_keyboard_interrupt_runs_cleanup(lab):
    lab.fault = lambda request: (_ for _ in ()).throw(KeyboardInterrupt()) if request.url.path == "/api/graph" else None
    with pytest.raises(checker.StateCheckError, match="KeyboardInterrupt"):
        run(lab, apply=True)
    assert lab.builds == {101: "finished", 102: "removed"}
    restored(lab)


@pytest.mark.parametrize("reply", [{"id": 0}, {"id": True}, {"id": "../other"}, {"id": 777, "buildTypeId": "Demo_Foreign"}])
def test_unidentified_queue_response_is_not_used_for_cancellation(lab, reply):
    lab.queue_response = reply
    with pytest.raises(checker.StateCheckError, match="inspect its queue manually"):
        run(lab, apply=True)
    assert not any(request.method == "DELETE" or request.url.path.startswith("/app/rest/builds/id:") for request in lab.writes)
    restored(lab)


def test_busy_refresh_waits_then_requests_its_own_scan(lab):
    lab.refresh_busy = 1
    assert run(lab, apply=True)["verified"]
    assert lab.scan == 3


@pytest.mark.parametrize("field,value", [("scan_status", "degraded"), ("scan_status", "failed"), ("no_scan_progress", True)])
def test_failed_refresh_does_not_suppress_cleanup(lab, field, value):
    setattr(lab, field, value)
    with pytest.raises(checker.StateCheckError, match="Graph refresh") as error:
        run(lab, apply=True)
    assert "refresh graph after cleanup" in str(error.value)
    assert lab.builds == {101: "finished", 102: "removed"}
    restored(lab)


def test_missing_csrf_blocks_teamcity_mutations(lab):
    lab.csrf = ""
    with pytest.raises(checker.StateCheckError, match="CSRF"):
        run(lab, apply=True)
    assert not lab.writes


def test_invalid_fixture_plan_is_rejected_before_requests(lab):
    lab.plan["fixture_key"] = "foreign"
    with pytest.raises(ProvisionError, match="ownership"):
        run(lab, apply=True)
    assert not lab.calls


@pytest.mark.parametrize("kwargs", [{"timeout": 0}, {"interval": -1}, {"interval": 6}])
def test_invalid_polling_limits_block_requests(lab, kwargs):
    with (httpx.Client(base_url="http://teamcity", transport=httpx.MockTransport(lab)) as tc,
          httpx.Client(base_url="http://graph", transport=httpx.MockTransport(lab)) as graph):
        with pytest.raises(checker.StateCheckError, match="Timeout"):
            checker.check_lab_states(lab.plan, tc, graph, username="root", password="private-password", **kwargs)
    assert not lab.calls


def test_cli_rejects_production_file_without_loading_it(tmp_path, capsys):
    path = tmp_path / ".env.prod"
    path.write_text("TC_ADMIN_TOKEN=private-admin\n")
    assert checker.main(["--env-file", str(path), "--apply"]) == 2
    assert "private-admin" not in capsys.readouterr().err


def test_cli_requires_admin_token_no_reader_fallback(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TC_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("DEPLOYMENT_MODE", raising=False)
    path = tmp_path / ".env"
    path.write_text("TEAMCITY_TOKEN=private-reader\n")
    assert checker.main(["--env-file", str(path)]) == 2
    error = capsys.readouterr().err
    assert "TC_ADMIN_TOKEN" in error and "private-reader" not in error


def test_duplicate_queue_id_does_not_cancel_twice(lab):
    lab.queue_response = {"id": 101, "buildTypeId": checker.ACTIVE}
    with pytest.raises(checker.StateCheckError, match="reused"):
        run(lab, apply=True)
    cancellations = [request.url.path for request in lab.writes if request.url.path.startswith("/app/rest/builds/id:")]
    assert cancellations == ["/app/rest/builds/id:101"]
    restored(lab)


def test_running_timeout_cancels_own_queued_build_and_restores(lab):
    def fault(request):
        if request.method == "GET" and request.url.path == "/app/rest/buildQueue/id:101" and lab.builds.get(101) != "removed":
            return httpx.Response(200, json={"id": 101, "buildTypeId": checker.ACTIVE, "state": "queued"})
    lab.fault = fault
    with pytest.raises(checker.StateCheckError, match="did not reach"):
        run(lab, apply=True)
    assert lab.queued == [101] and lab.builds[101] == "removed"
    restored(lab)


def test_ignored_pause_restore_is_detected_and_refresh_still_attempted(lab):
    lab.fault = lambda request: httpx.Response(200) if request.method == "PUT" and request.url.path.endswith("/paused") and request.content == b"false" else None
    with pytest.raises(checker.StateCheckError, match="Paused state restoration"):
        run(lab, apply=True)
    assert lab.builds == {101: "finished", 102: "removed"} and lab.scan == 2


def test_step_id_reassignment_does_not_break_restore_verification(lab):
    lab.details[checker.ACTIVE]["steps"]["step"][0]["id"] = "original-id"
    def fault(request):
        if request.method == "GET" and request.url.path == f"/app/rest/buildTypes/id:{checker.ACTIVE}" and lab.queued:
            result = copy.deepcopy(lab.details[checker.ACTIVE])
            result["steps"]["step"][0]["id"] = "reassigned-id"
            return httpx.Response(200, json=result)
    lab.fault = fault
    assert run(lab, apply=True)["cleanup"] == "completed"


def test_authenticated_session_is_reused_without_relogin(lab):
    lab.authenticated = True
    assert run(lab)["mode"] == "plan"
    assert not any(request.url.path == "/login" for request in lab.calls)


def test_cli_environment_production_mode_rejected(tmp_path, monkeypatch, capsys):
    path = tmp_path / ".env"
    path.write_text("TC_ADMIN_TOKEN=private-admin\n")
    monkeypatch.setenv("DEPLOYMENT_MODE", "production")
    assert checker.main(["--env-file", str(path), "--apply"]) == 2
    assert "production" in capsys.readouterr().err


def test_cli_defaults_to_plan_and_does_not_print_secrets(lab, tmp_path, monkeypatch, capsys):
    path = tmp_path / ".env"
    path.write_text("TC_ADMIN_TOKEN=private-admin\nWEB_PASSWORD=private-password\n")
    monkeypatch.delenv("DEPLOYMENT_MODE", raising=False)
    monkeypatch.setenv("TEAMCITY_BOOTSTRAP_URL", "http://teamcity:8111")
    monkeypatch.setenv("GRAPH_URL", "http://graph:8080")
    monkeypatch.setenv("BITBUCKET_WORKSPACE", "fixture-space")
    original_client = httpx.Client
    monkeypatch.setattr(checker.httpx, "Client", lambda **kwargs: original_client(**kwargs, transport=httpx.MockTransport(lab)))
    assert checker.main(["--env-file", str(path)]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["mode"] == "plan" and "private-" not in output and not lab.writes
