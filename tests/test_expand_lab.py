import copy
import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest


SPEC = importlib.util.spec_from_file_location("expand_lab", Path(__file__).parents[1] / "scripts/bootstrap/expand_lab.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


@pytest.fixture
def plan():
    return {
        "fixture_key": "artifact-graph-lab-v1", "workspace": "artifact_graph",
        "projects": [{"key": "LAB01", "name": "Lab application"}],
        "repositories": [
            {"slug": "lab-app", "project_key": "LAB01", "branch": "develop", "files": {"ci/build.sh": "echo lab\n", "message": "file named message"}},
            {"slug": "lab-overflow", "project_key": "DEMO", "branch": "main", "files": {}},
        ],
        "tc_projects": [{"id": "LabMatrix", "name": "Lab Matrix", "parent_id": "_Root"}, {"id": "LabMatrix_App", "name": "App", "parent_id": "LabMatrix"}],
        "configs": [
            {"id": "LabMatrix_App_Test", "name": "Test", "project_id": "LabMatrix_App", "repository_slugs": ["lab-app"], "script": "echo test", "artifacts": "", "dependencies": [], "checkout_rules": {}},
            {"id": "LabMatrix_App_Build", "name": "Build", "project_id": "LabMatrix_App", "repository_slugs": ["lab-app"], "script": "sh product/ci/build.sh", "artifacts": "**/sbom.json => artifacts", "dependencies": ["LabMatrix_App_Test"], "checkout_rules": {"lab-app": "+:.=>product"}},
        ],
    }


class FakeLab:
    def __init__(self, plan):
        self.plan = plan
        self.calls = []
        self.git_calls = []
        self.entities = {"/workspaces/artifact_graph": {}, "/workspaces/artifact_graph/projects/DEMO": {"key": "DEMO", "description": "foreign baseline"}}
        self.files = {}
        self.licensing = {"licenseUseExceeded": False, "buildTypesLeft": 70, "unlimitedBuildTypes": False}
        self.active_count = 0
        self.failure_path = ""
        self.git_code = 0
        self.git_error = "SECRET-GIT-ERROR"

    def git(self, arguments, **kwargs):
        self.git_calls.append((arguments, kwargs))
        return SimpleNamespace(returncode=self.git_code, stdout="a" * 40 + "\trefs/heads/main\n", stderr=self.git_error)

    def handle(self, request):
        path = request.url.path.removeprefix("/2.0")
        self.calls.append((request.method, path, request.content, dict(request.url.params)))
        if request.method == "PUT" and path.endswith(("/paused", "/settings/artifactRules", "/description")):
            if request.headers.get("accept") != "text/plain" or request.headers.get("content-type") != "text/plain":
                return httpx.Response(406, text="Scalar TeamCity endpoint requires text/plain")
        if path == self.failure_path:
            return httpx.Response(403, text="SECRET-UPSTREAM-ERROR")
        if request.method == "GET":
            if path.endswith("/licensingData"):
                assert "licenseKeys" not in request.url.params.get("fields", "")
                return httpx.Response(200, json=self.licensing)
            if path in {"/app/rest/builds", "/app/rest/buildQueue"}:
                return httpx.Response(200, json={"count": self.active_count})
            if path.startswith("/app/rest/users/"):
                return httpx.Response(200, json={"id": 2, "username": "reader"})
            if path.endswith("/commits"):
                repo = path.rsplit("/", 1)[0]
                return httpx.Response(200, json={"values": [{"hash": "a" * 40}] if repo in self.files else []})
            if "/refs/branches/" in path:
                repo = path.split("/refs/branches/")[0]
                return httpx.Response(200 if repo in self.files else 404, json={"target": {"hash": "a" * 40}})
            if "/src/" in path:
                repo, _, suffix = path.partition("/src/")
                filename = suffix.split("/", 1)[1]
                if filename not in self.files.get(repo, {}):
                    return httpx.Response(404)
                return httpx.Response(200, content=self.files[repo][filename].encode())
            return httpx.Response(200, json=self.entities[path]) if path in self.entities else httpx.Response(404)
        if request.method == "POST":
            if path.endswith("/src"):
                repo_base = path.removesuffix("/src")
                repo = next(item for item in self.plan["repositories"] if repo_base.endswith("/" + item["slug"]))
                assert "multipart/form-data" in request.headers["content-type"]
                assert b'name="branch"' in request.content and repo["branch"].encode() in request.content
                assert b'name="/message"' in request.content
                self.files[repo_base] = dict(repo["files"])
                return httpx.Response(201, json={"hash": "a" * 40})
            body = json.loads(request.content)
            if path == "/workspaces/artifact_graph/projects":
                self.entities[path + "/" + body["key"]] = body
            elif path.startswith("/repositories/"):
                self.entities[path] = {**body, "mainbranch": None}
            elif path == "/app/rest/projects":
                self.entities[path + "/id:" + body["id"]] = {key: value for key, value in body.items() if key != "description"}
            elif path in {"/app/rest/vcs-roots", "/app/rest/buildTypes"}:
                self.entities[path + "/id:" + body["id"]] = body
            else:
                raise AssertionError(f"Unexpected POST {path}")
            return httpx.Response(201, json=body)
        if request.method == "PUT":
            if path.startswith("/repositories/"):
                self.entities[path].update(json.loads(request.content))
            elif "/roles/PROJECT_VIEWER/p:LabMatrix" in path:
                pass
            elif path.startswith("/app/rest/projects/id:") and path.endswith("/description"):
                self.entities[path.removesuffix("/description")]["description"] = request.content.decode()
            elif "/app/rest/buildTypes/" in path:
                base, field = path.rsplit("/", 1)
                if field == "artifactRules":
                    base = base.removesuffix("/settings")
                    self.entities[base]["settings"] = {"property": [{"name": "artifactRules", "value": request.content.decode()}]}
                elif field == "paused":
                    self.entities[base]["paused"] = request.content == b"true"
                else:
                    self.entities[base][field] = json.loads(request.content)
            else:
                raise AssertionError(f"Unexpected PUT {path}")
            return httpx.Response(200, json={})
        raise AssertionError(f"Unexpected {request.method} {path}")

    def provision(self, *, apply=False, **kwargs):
        with (httpx.Client(base_url="https://api.bitbucket.org/2.0", transport=httpx.MockTransport(self.handle)) as bb,
              httpx.Client(base_url="http://localhost:8111", transport=httpx.MockTransport(self.handle)) as tc):
            options = dict(workspace="artifact_graph", bootstrap_token="bootstrap-secret", checkout_token="checkout-ro-secret", tc_admin_token="tc-admin-secret", reader_username="reader", git_runner=self.git, apply=apply)
            options.update(kwargs)
            return module.provision_plan(self.plan, bb, tc, **options)

    def writes(self):
        return [entry for entry in self.calls if entry[0] != "GET"]


def test_default_plan_checks_git_and_capacity_without_writes(plan):
    lab = FakeLab(plan)
    report = lab.provision()
    assert report["mode"] == "plan" and report["newConfigurations"] == 2
    assert report["newProjects"] == 1 and report["newRepositories"] == 2
    assert lab.git_calls and not lab.writes()


def test_repository_matrix_plan_passes_namespace_and_dependency_preflight():
    from app.lab_matrix import build_plan
    matrix = build_plan("artifact_graph")
    module._validate_plan(matrix, "artifact_graph")
    assert len(matrix["configs"]) == 16 and len(matrix["repositories"]) == 11


def test_apply_is_additive_nonmain_branch_secure_checkout_and_idempotent(plan):
    lab = FakeLab(plan)
    first = lab.provision(apply=True)
    assert first["newConfigurations"] == 2
    assert first["existingCheckoutTokensUnchanged"] == [] and not first.get("warnings")
    assert lab.entities["/repositories/artifact_graph/lab-app"]["mainbranch"] == {"name": "develop"}
    assert lab.entities["/workspaces/artifact_graph/projects/DEMO"]["description"] == "foreign baseline"
    assert not any(method == "DELETE" or "/Demo" in path or "/agents" in path or path == "/app/rest/buildQueue" for method, path, _, _ in lab.writes())
    vcs = lab.entities["/app/rest/vcs-roots/id:LabMatrix_Vcs_lab_app"]
    assert module._props(vcs)["secure:password"] == "checkout-ro-secret"
    assert "bootstrap-secret" not in json.dumps(vcs)
    config = lab.entities["/app/rest/buildTypes/id:LabMatrix_App_Build"]
    assert config["vcs-root-entries"]["vcs-root-entry"][0]["checkout-rules"] == "+:.=>product"
    assert config["snapshot-dependencies"]["snapshot-dependency"][0]["source-buildType"]["id"] == "LabMatrix_App_Test"
    lab.calls.clear()
    lab.licensing["buildTypesLeft"] = 0
    second = lab.provision(apply=True)
    assert second["newConfigurations"] == second["newRepositories"] == second["newProjects"] == 0
    assert second["configure"] == []
    assert second["existingCheckoutTokensUnchanged"] == ["LabMatrix_Vcs_lab_app"]
    assert second["warnings"]
    assert all("/roles/PROJECT_VIEWER/p:LabMatrix" in path for _, path, _, _ in lab.writes())


@pytest.mark.parametrize("apply", [False, True])
def test_changed_env_checkout_token_warns_existing_root_is_not_rotated(plan, apply):
    lab = FakeLab(plan)
    lab.provision(apply=True)
    lab.calls.clear()
    report = lab.provision(apply=apply, checkout_token="rotated-checkout-secret")
    assert report["existingCheckoutTokensUnchanged"] == ["LabMatrix_Vcs_lab_app"]
    assert "not updated" in report["warnings"][0]
    assert module._props(lab.entities["/app/rest/vcs-roots/id:LabMatrix_Vcs_lab_app"])["secure:password"] == "checkout-ro-secret"
    assert all("/vcs-roots" not in path for _, path, _, _ in lab.writes())
    assert "rotated-checkout-secret" not in json.dumps(report) and "checkout-ro-secret" not in json.dumps(report)


def test_disabled_runner_is_detected_in_plan_and_reconciled_only_on_apply(plan):
    lab = FakeLab(plan)
    lab.provision(apply=True)
    identity = "LabMatrix_App_Test"
    config = lab.entities[f"/app/rest/buildTypes/id:{identity}"]
    config["steps"]["step"][0]["disabled"] = True
    lab.calls.clear()
    report = lab.provision()
    assert report["configure"] == [identity] and config["steps"]["step"][0]["disabled"] is True
    assert not lab.writes()
    detail_calls = [params for method, path, _, params in lab.calls if method == "GET" and "/buildTypes/id:" in path]
    assert detail_calls and all("step(type,disabled," in params["fields"] for params in detail_calls)
    report = lab.provision(apply=True)
    assert report["configure"] == [identity] and not config["steps"]["step"][0].get("disabled")


def test_disabled_runner_with_active_builds_cannot_be_reconciled(plan):
    lab = FakeLab(plan)
    lab.provision(apply=True)
    lab.entities["/app/rest/buildTypes/id:LabMatrix_App_Test"]["steps"]["step"][0]["disabled"] = True
    lab.active_count = 1
    lab.calls.clear()
    with pytest.raises(module.ProvisionError, match="active builds"):
        lab.provision(apply=True)
    assert not lab.writes()


def test_project_creation_persists_ownership_through_scalar_endpoint(plan):
    lab = FakeLab(plan)
    lab.provision(apply=True)
    for project in plan["tc_projects"]:
        base = "/app/rest/projects/id:" + project["id"]
        expected = module._marker(plan, "tc-project", project["id"], project)
        assert lab.entities[base]["description"] == expected
        assert ("PUT", base + "/description", expected.encode(), {}) in lab.calls


@pytest.mark.parametrize("checkout", ["", "bootstrap-secret", "replace-me"])
def test_missing_or_bootstrap_checkout_token_fails_before_any_network(plan, checkout):
    lab = FakeLab(plan)
    with pytest.raises(module.ProvisionError):
        lab.provision(apply=True, checkout_token=checkout)
    assert not lab.calls and not lab.git_calls


def test_git_read_failure_is_redacted_and_prevents_remote_writes(plan):
    lab = FakeLab(plan)
    lab.git_code = 1
    with pytest.raises(module.ProvisionError) as error:
        lab.provision(apply=True)
    assert "SECRET" not in str(error.value) and "bootstrap-secret" not in str(error.value)
    assert not lab.calls


def test_git_probe_uses_no_token_in_args_no_local_git_config_and_disables_fallback(monkeypatch):
    calls = []
    monkeypatch.setenv("GIT_TRACE", "1")
    monkeypatch.setenv("BB_BOOTSTRAP_TOKEN", "must-not-inherit")
    def runner(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="a" * 40 + "\trefs/heads/main\n", stderr="")
    module.verify_git_read("artifact_graph", "java-maven-api", "ro-secret", runner=runner)
    args, kwargs = calls[0]
    assert args[:3] == ["git", "ls-remote", "--heads"] and "ro-secret" not in str(args)
    assert "GIT_TRACE" not in kwargs["env"] and "BB_BOOTSTRAP_TOKEN" not in kwargs["env"]
    assert kwargs["env"]["GIT_CONFIG_VALUE_1"] == "false"
    assert kwargs["env"]["GIT_CONFIG_VALUE_2"] == ""
    assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert not Path(kwargs["cwd"]).exists()


@pytest.mark.parametrize("licensing", [{"buildTypesLeft": 1}, {"licenseUseExceeded": True, "buildTypesLeft": 70}, {}])
def test_licensing_preflight_fails_before_all_writes(plan, licensing):
    lab = FakeLab(plan)
    lab.licensing = licensing
    with pytest.raises(module.ProvisionError, match="capacity"):
        lab.provision(apply=True)
    assert not lab.writes()


@pytest.mark.parametrize("path", ["/workspaces/artifact_graph/projects/LAB01", "/repositories/artifact_graph/lab-app", "/app/rest/projects/id:LabMatrix", "/app/rest/buildTypes/id:LabMatrix_App_Test"])
def test_foreign_entity_collision_prevents_any_writes(plan, path):
    lab = FakeLab(plan)
    lab.entities[path] = {"description": "belongs to someone else"}
    with pytest.raises(module.ProvisionError, match="Ownership"):
        lab.provision(apply=True)
    assert not lab.writes()


def test_foreign_vcs_root_is_not_overwritten(plan):
    lab = FakeLab(plan)
    lab.entities["/app/rest/vcs-roots/id:LabMatrix_Vcs_lab_app"] = {"name": "foreign"}
    with pytest.raises(module.ProvisionError, match="ownership"):
        lab.provision(apply=True)
    assert not lab.writes()


def test_owned_source_drift_never_overwrites_human_changes(plan):
    lab = FakeLab(plan)
    lab.provision(apply=True)
    lab.calls.clear()
    lab.files["/repositories/artifact_graph/lab-app"]["ci/build.sh"] = "human edit\n"
    with pytest.raises(module.ProvisionError, match="drifted"):
        lab.provision(apply=True)
    assert not lab.writes()


def test_partially_created_owned_repository_and_config_can_resume(plan):
    lab = FakeLab(plan)
    lab.provision(apply=True)
    lab.calls.clear()
    del lab.files["/repositories/artifact_graph/lab-app"]
    lab.entities["/repositories/artifact_graph/lab-app"]["mainbranch"] = None
    lab.entities["/app/rest/buildTypes/id:LabMatrix_App_Build"].pop("steps")
    report = lab.provision(apply=True)
    assert report["newConfigurations"] == 0 and report["configure"] == ["LabMatrix_App_Build"]
    assert len([entry for entry in lab.writes() if entry[1].endswith("/src")]) == 1
    assert not any(path == "/app/rest/buildTypes" for _, path, _, _ in lab.writes())


def test_resume_after_first_scalar_failure_preserves_owned_shells_and_uses_plain_accept(plan):
    lab = FakeLab(plan)
    lab.failure_path = "/app/rest/buildTypes/id:LabMatrix_App_Test/paused"
    with pytest.raises(module.ProvisionError):
        lab.provision(apply=True)
    assert all("/app/rest/buildTypes/id:" + config["id"] in lab.entities for config in plan["configs"])
    lab.calls.clear()
    lab.failure_path = ""
    preview = lab.provision()
    assert preview["newConfigurations"] == preview["newRepositories"] == preview["newTcProjects"] == 0
    assert set(preview["configure"]) == {config["id"] for config in plan["configs"]}
    assert not lab.writes()
    result = lab.provision(apply=True)
    assert result["newConfigurations"] == 0
    assert not any(method == "POST" for method, _, _, _ in lab.writes())
    assert all(not lab.entities["/app/rest/buildTypes/id:" + config["id"]]["paused"] for config in plan["configs"])


def test_managed_active_configuration_cannot_be_reconciled(plan):
    lab = FakeLab(plan)
    lab.provision(apply=True)
    lab.calls.clear()
    lab.entities["/app/rest/buildTypes/id:LabMatrix_App_Build"].pop("steps")
    lab.active_count = 1
    with pytest.raises(module.ProvisionError, match="active builds"):
        lab.provision(apply=True)
    assert not lab.writes()


def test_upstream_errors_are_redacted_even_if_response_contains_secrets(plan):
    lab = FakeLab(plan)
    lab.failure_path = "/workspaces/artifact_graph/projects/LAB01"
    with pytest.raises(module.ProvisionError) as error:
        lab.provision(apply=True)
    assert "HTTP 403" in str(error.value) and "SECRET" not in str(error.value)
    assert not lab.writes()


@pytest.mark.parametrize("change", [
    lambda plan: plan.update(workspace="different"),
    lambda plan: plan["projects"][0].update(key="DEMO"),
    lambda plan: plan["repositories"][0].update(slug="java-maven-api"),
    lambda plan: plan["repositories"][0]["files"].update({"../secret": "bad"}),
    lambda plan: plan["repositories"][0].update(branch="../main"),
    lambda plan: plan["tc_projects"][1].update(parent_id="Demo"),
    lambda plan: plan["configs"][0].update(id="Demo_01"),
    lambda plan: plan["configs"][0].update(dependencies=["LabMatrix_App_Build"]),
])
def test_invalid_or_baseline_targeted_plan_is_rejected_before_network(plan, change):
    change(plan)
    lab = FakeLab(plan)
    with pytest.raises(module.ProvisionError):
        lab.provision(apply=True)
    assert not lab.calls and not lab.git_calls


def test_script_refuses_external_teamcity_even_with_valid_tokens(plan):
    lab = FakeLab(plan)
    with (httpx.Client(base_url="https://api.bitbucket.org/2.0", transport=httpx.MockTransport(lab.handle)) as bb,
          httpx.Client(base_url="https://production.example", transport=httpx.MockTransport(lab.handle)) as tc):
        with pytest.raises(module.ProvisionError, match="local TeamCity lab"):
            module.provision_plan(plan, bb, tc, workspace="artifact_graph", bootstrap_token="bootstrap-secret", checkout_token="read-secret", tc_admin_token="tc-secret", apply=True, git_runner=lab.git)
    assert not lab.calls and not lab.git_calls


def test_main_rejects_bootstrap_env_selection_and_production_file(tmp_path, capsys):
    env = tmp_path / "test.env"
    env.write_text("DEPLOYMENT_MODE=production\nBB_BOOTSTRAP_TOKEN=do-not-print", encoding="utf-8")
    assert module.main(["--env-file", str(env), "--checkout-token-env", "BB_BOOTSTRAP_TOKEN"]) == 2
    assert module.main(["--env-file", str(env), "--apply"]) == 2
    captured = capsys.readouterr()
    assert "do-not-print" not in captured.out + captured.err


def test_transient_get_recovers_with_bounded_backoff(monkeypatch):
    calls, delays = [], []
    def handler(request):
        calls.append(request.method)
        return httpx.Response(500 if len(calls) < 3 else 200, json={"ok": True})
    monkeypatch.setattr(module.time, "sleep", delays.append)
    with httpx.Client(base_url="https://api.bitbucket.org/2.0", transport=httpx.MockTransport(handler)) as client:
        response = module._call(client, "GET", "/repositories/workspace/lab-repo/commits")
    assert response.status_code == 200 and calls == ["GET"] * 3
    assert delays == [0.5, 1.0]


@pytest.mark.parametrize("method,status", [("GET", 401), ("POST", 500), ("PUT", 503), ("POST", 429)])
def test_permanent_auth_errors_and_all_writes_are_never_retried(monkeypatch, method, status):
    calls, delays = [], []
    def handler(request):
        calls.append(request.method)
        return httpx.Response(status, text="SECRET-ERROR-BODY", headers={"Retry-After": "1"})
    monkeypatch.setattr(module.time, "sleep", delays.append)
    with httpx.Client(base_url="https://api.bitbucket.org/2.0", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(module.ProvisionError) as error:
            module._call(client, method, "/test")
    assert calls == [method] and not delays and "SECRET" not in str(error.value)


@pytest.mark.parametrize("retry_after,expected", [("999", 2.0), ("0.25", 0.25), ("invalid", 0.5), ("-1", 0.5), ("NaN", 0.5)])
def test_get_retry_after_is_numeric_finite_nonnegative_and_capped(monkeypatch, retry_after, expected):
    calls, delays = [], []
    def handler(request):
        calls.append(request)
        return httpx.Response(429 if len(calls) == 1 else 200, headers={"Retry-After": retry_after})
    monkeypatch.setattr(module.time, "sleep", delays.append)
    with httpx.Client(base_url="https://api.bitbucket.org/2.0", transport=httpx.MockTransport(handler)) as client:
        assert module._call(client, "GET", "/test").status_code == 200
    assert len(calls) == 2 and delays == [expected]


@pytest.mark.parametrize("method,attempts", [("GET", 3), ("POST", 1), ("PUT", 1)])
def test_transport_errors_retry_only_get_and_hide_error_details(monkeypatch, method, attempts):
    calls, delays = [], []
    def handler(request):
        calls.append(request)
        raise httpx.ConnectError("SECRET-CONNECTION-ERROR", request=request)
    monkeypatch.setattr(module.time, "sleep", delays.append)
    with httpx.Client(base_url="https://api.bitbucket.org/2.0", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(module.ProvisionError) as error:
            module._call(client, method, "/test")
    assert len(calls) == attempts and len(delays) == attempts - 1
    assert "SECRET" not in str(error.value)


def test_get_http_failure_stops_after_three_attempts(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(504, text="SECRET-UPSTREAM-BODY")
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    with httpx.Client(base_url="https://api.bitbucket.org/2.0", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(module.ProvisionError, match="HTTP 504") as error:
            module._call(client, "GET", "/test")
    assert len(calls) == 3 and "SECRET" not in str(error.value)
