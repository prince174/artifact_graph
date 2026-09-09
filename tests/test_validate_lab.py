import copy
from datetime import datetime, timezone
import json

import httpx
import pytest

from scripts import validate_lab
from scripts.validate_lab import (
    LabValidationError, fetch_graph_pages, fetch_tc_builds, merge_pages,
    request_refresh, validate_live_lab, validate_matrix,
)


def fixture_matrix():
    plan = {
        "workspace": "fixture-space", "fixture_key": "test-matrix",
        "repositories": [
            {"slug": "api", "project_key": "LAB01"},
            {"slug": "library", "project_key": "LAB02"},
            {"slug": "web", "project_key": "DEMO"},
        ],
        "configs": [
            {"id": "Product_Build", "project_id": "Shared", "repository_slugs": ["api", "library"],
             "expected": {"status": "SUCCESS", "push_tags": ["registry:5000/api:one", "registry:5000/api:two"], "sbom_paths": ["artifacts/build/sbom.json"], "source_paths": ["pom.xml"]}},
            {"id": "Product_Test", "project_id": "Shared", "repository_slugs": ["web"],
             "expected": {"status": "SUCCESS", "push_tags": [], "sbom_paths": [], "source_paths": []}},
            {"id": "Product_Failed", "project_id": "Shared", "repository_slugs": ["web"],
             "expected": {"status": "FAILURE", "push_tags": [], "sbom_paths": [], "source_paths": []}},
        ],
    }
    builds = {config["id"]: [{"id": index * 10 + run, "buildTypeId": config["id"], "state": "finished", "status": config["expected"]["status"]} for run in (2, 1)] for index, config in enumerate(plan["configs"], 1)}
    graphs = {}
    for repo in plan["repositories"]:
        rid = f"repo:fixture-space/{repo['slug']}"
        bpid = f"bb-project:fixture-space/{repo['project_key']}"
        linked = [config for config in plan["configs"] if repo["slug"] in config["repository_slugs"]]
        nodes = [
            {"id": rid, "kind": "repository", "hasTargetOutput": any(config["expected"]["push_tags"] for config in linked)},
            {"id": bpid, "kind": "bb_project"},
            {"id": "tc-project:Shared", "kind": "tc_project", "hasTargetOutput": True},
        ]
        edges = [{"source": bpid, "target": rid, "relation": "contains"}, {"source": rid, "target": "tc-project:Shared", "relation": "maps_to"}]
        for config in linked:
            cid = f"build-type:{config['id']}"
            expected = config["expected"]
            nodes.append({"id": cid, "kind": "build_configuration", "mappedRepositoryIds": [f"repo:fixture-space/{slug}" for slug in config["repository_slugs"]], "hasTargetOutput": bool(expected["push_tags"] or expected["sbom_paths"])})
            edges.append({"source": "tc-project:Shared", "target": cid, "relation": "contains"})
            for build in builds[config["id"]]:
                bid = f"build:{build['id']}"
                nodes.append({**build, "id": bid, "kind": "build", "hasImagePush": bool(expected["push_tags"]), "hasSbom": bool(expected["sbom_paths"]), "pushedImages": [{"image": tag, "evidence": "teamcity_build_log", "sourcePaths": ["pom.xml"]} for tag in expected["push_tags"]], "sbomArtifacts": [{"path": path} for path in expected["sbom_paths"]], "visualReason": "push_and_sbom" if expected["push_tags"] else "failed" if expected["status"] == "FAILURE" else "success"})
                edges.append({"source": cid, "target": bid, "relation": "ran_as"})
        graphs[repo["slug"]] = {"nodes": nodes, "edges": edges, "positions": {node["id"]: {"x": 1, "y": index} for index, node in enumerate(nodes)}}
    return plan, builds, graphs


def test_matrix_validates_multiroot_shared_project_and_negative_builds():
    plan, builds, graphs = fixture_matrix()
    report = validate_matrix(graphs, builds, plan, min_builds=2)
    assert report == {"fixtureKey": "test-matrix", "repositories": 3, "bbProjects": 3, "tcProjects": 1, "configurations": 3, "builds": 6, "confirmedPushes": 4, "sbomArtifacts": 2, "commandSourcePaths": ["pom.xml"]}


@pytest.mark.parametrize("field,value,message", [
    ("hasImagePush", False, "hasImagePush"),
    ("hasSbom", False, "hasSbom"),
    ("pushedImages", [], "push tags"),
    ("sbomArtifacts", [{"path": "wrong/sbom.json"}], "SBOM artifact paths"),
    ("status", "FAILURE", "actual build status"),
    ("state", "running", "Unfinished"),
    ("visualReason", "success", "build highlight"),
    ("stale", True, "stale or incomplete"),
])
def test_matrix_rejects_wrong_evidence_and_build_state(field, value, message):
    plan, builds, graphs = fixture_matrix()
    node = next(node for node in graphs["api"]["nodes"] if node["kind"] == "build")
    node[field] = value
    with pytest.raises(LabValidationError, match=message):
        validate_matrix(graphs, builds, plan, 2)


def test_matrix_rejects_source_only_and_missing_source_evidence():
    plan, builds, graphs = fixture_matrix()
    node = next(node for node in graphs["api"]["nodes"] if node["kind"] == "build")
    node["pushedImages"][0]["evidence"] = "pom.xml"
    with pytest.raises(LabValidationError, match="build-log evidence"):
        validate_matrix(graphs, builds, plan, 2)
    node["pushedImages"][0]["evidence"] = "teamcity_build_log"
    for push in node["pushedImages"]:
        push["sourcePaths"] = []
    with pytest.raises(LabValidationError, match="command source evidence"):
        validate_matrix(graphs, builds, plan, 2)


def test_matrix_source_paths_match_namespace_suffix_at_path_boundary():
    plan, builds, graphs = fixture_matrix()
    for graph in graphs.values():
        for node in graph["nodes"]:
            for push in node.get("pushedImages", []):
                push["sourcePaths"] = ["fixture-space/api/pom.xml"]
    assert validate_matrix(graphs, builds, plan, 2)["commandSourcePaths"] == ["fixture-space/api/pom.xml"]
    for node in graphs["api"]["nodes"]:
        for push in node.get("pushedImages", []):
            push["sourcePaths"] = ["fixture-space/api/not-pom.xml"]
    with pytest.raises(LabValidationError, match="command source evidence"):
        validate_matrix(graphs, builds, plan, 2)


def test_matrix_combines_legacy_source_path_with_configured_source_paths():
    plan, builds, graphs = fixture_matrix()
    for graph in graphs.values():
        for node in graph["nodes"]:
            for push in node.get("pushedImages", []):
                push["sourcePath"] = "build-step.sh"
    report = validate_matrix(graphs, builds, plan, 2)
    assert report["commandSourcePaths"] == ["build-step.sh", "pom.xml"]


def test_matrix_checks_expected_invalid_sbom_parsing_status():
    plan, builds, graphs = fixture_matrix()
    plan["configs"][0]["expected"]["sbom_statuses"] = {"artifacts/build/sbom.json": "invalid_json"}
    with pytest.raises(LabValidationError, match="SBOM parsing status"):
        validate_matrix(graphs, builds, plan, 2)
    for graph in graphs.values():
        for node in graph["nodes"]:
            for sbom in node.get("sbomArtifacts", []):
                sbom["sbomStatus"] = "invalid_json"
    validate_matrix(graphs, builds, plan, 2)


def test_matrix_rejects_false_positive_output_highlight_for_shared_project_repo():
    plan, builds, graphs = fixture_matrix()
    graphs["web"]["nodes"][0]["hasTargetOutput"] = True
    with pytest.raises(LabValidationError, match="target-output highlight"):
        validate_matrix(graphs, builds, plan, 2)


def test_matrix_rejects_extra_shared_project_config_and_incorrect_mapping():
    plan, builds, graphs = fixture_matrix()
    extra = copy.deepcopy(next(node for node in graphs["api"]["nodes"] if node["kind"] == "build_configuration"))
    graphs["web"]["nodes"].append(extra)
    graphs["web"]["positions"][extra["id"]] = {"x": 0, "y": 0}
    with pytest.raises(LabValidationError, match="leaked or lost"):
        validate_matrix(graphs, builds, plan, 2)
    plan, builds, graphs = fixture_matrix()
    config = next(node for node in graphs["api"]["nodes"] if node["kind"] == "build_configuration")
    config["mappedRepositoryIds"] = ["repo:fixture-space/api"]
    with pytest.raises(LabValidationError, match="exact repository mapping"):
        validate_matrix(graphs, builds, plan, 2)


@pytest.mark.parametrize("kind,node_id", [("image", "image:a"), ("sbom", "sbom:a"), ("workspace", "workspace:w"), ("tc_project", "tc-project:_Root")])
def test_matrix_forbids_separate_outputs_and_service_nodes(kind, node_id):
    plan, builds, graphs = fixture_matrix()
    graphs["api"]["nodes"].append({"id": node_id, "kind": kind})
    with pytest.raises(LabValidationError, match="service entity|TeamCity root"):
        validate_matrix(graphs, builds, plan, 2)


def test_matrix_rejects_missing_builds_edges_and_actual_tc_mismatch():
    plan, builds, graphs = fixture_matrix()
    plan["configs"][0]["expected"]["min_builds"] = 5
    with pytest.raises(LabValidationError, match="Too few actual"):
        validate_matrix(graphs, builds, plan, 2)
    plan["configs"][0]["expected"]["min_builds"] = 2
    builds["Product_Build"][0]["buildTypeId"] = "Other"
    with pytest.raises(LabValidationError, match="another configuration"):
        validate_matrix(graphs, builds, plan, 2)
    builds["Product_Build"][0]["buildTypeId"] = "Product_Build"
    graphs["api"]["edges"].pop()
    with pytest.raises(LabValidationError, match="Incorrect graph links"):
        validate_matrix(graphs, builds, plan, 2)


def empty_page(offset=0, next_cursor=None, mode="projects"):
    return {"nodes": [], "edges": [], "positions": {}, "pagination": {"mode": mode, "offset": offset, "hasMore": bool(next_cursor), "nextCursor": next_cursor}}


def test_graph_pagination_reads_all_pages_and_detects_loop():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=empty_page(0, "page2") if not request.url.params["cursor"] else empty_page(10))
    with httpx.Client(base_url="http://graph", transport=httpx.MockTransport(handler)) as client:
        assert len(fetch_graph_pages(client)) == 2
    assert all(request.method == "GET" for request in calls)
    offset = -10
    def loop(request):
        nonlocal offset
        offset += 10
        return httpx.Response(200, json=empty_page(offset, "same-cursor"))
    with httpx.Client(base_url="http://graph", transport=httpx.MockTransport(loop)) as client:
        with pytest.raises(LabValidationError, match="pagination loop"):
            fetch_graph_pages(client)


def test_graph_page_enforces_ten_repositories_per_project():
    page = empty_page()
    page["nodes"] = [{"id": "p", "kind": "bb_project"}] + [{"id": f"r{i}", "kind": "repository"} for i in range(11)]
    page["edges"] = [{"source": "p", "target": f"r{i}", "relation": "contains"} for i in range(11)]
    page["positions"] = {node["id"]: {} for node in page["nodes"]}
    with httpx.Client(base_url="http://graph", transport=httpx.MockTransport(lambda request: httpx.Response(200, json=page))) as client:
        with pytest.raises(LabValidationError, match="ten repositories"):
            fetch_graph_pages(client)


def test_tc_builds_use_bearer_ro_get_context_path_and_requested_limit():
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/tc/app/rest/builds"
        assert request.headers["authorization"] == "Bearer read-only-token"
        assert request.url.params["locator"] == "buildType:(id:My_Build),state:finished,count:5,defaultFilter:false"
        return httpx.Response(200, json={"build": [{"id": 8}]})
    with httpx.Client(base_url="https://tc.example/tc", headers={"Authorization": "Bearer read-only-token"}, transport=httpx.MockTransport(handler)) as client:
        assert fetch_tc_builds(client, "My_Build", 5) == [{"id": 8}]


def test_optional_refresh_uses_csrf_and_waits_for_new_success():
    calls, scans = [], iter([{"status": "success", "finishedAt": "old"}, {"status": "running", "finishedAt": None}, {"status": "success", "finishedAt": "new"}])
    def handler(request):
        calls.append(request)
        if request.url.path == "/api/status":
            return httpx.Response(200, json={"lastScan": next(scans)})
        if request.url.path == "/api/session":
            return httpx.Response(200, json={"csrf": "csrf-only"})
        assert request.url.path == "/api/refresh" and request.method == "POST"
        assert request.headers["x-csrf-token"] == "csrf-only"
        return httpx.Response(202, json={"status": "queued"})
    with httpx.Client(base_url="http://graph", transport=httpx.MockTransport(handler)) as client:
        request_refresh(client, timeout=1, poll_seconds=0)
    assert sum(request.method == "POST" for request in calls) == 1


@pytest.mark.parametrize("status", ["failed", "degraded"])
def test_refresh_rejects_new_partial_or_failed_scan(status):
    scans = iter([{"status": "success", "finishedAt": "old"}, {"status": status, "finishedAt": "new"}])
    def handler(request):
        if request.url.path == "/api/status":
            return httpx.Response(200, json={"lastScan": next(scans)})
        return httpx.Response(202 if request.method == "POST" else 200, json={"csrf": "token"})
    with httpx.Client(base_url="http://graph", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(LabValidationError, match="failed or is degraded"):
            request_refresh(client, timeout=1, poll_seconds=0)


def test_live_orchestrator_is_read_only_and_covers_all_repository_searches():
    plan, builds, graphs = fixture_matrix()
    calls = []
    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        if request.url.path == "/api/status":
            return httpx.Response(200, json={"mode": "live", "lastScan": {"status": "success", "finishedAt": datetime.now(timezone.utc).isoformat()}})
        if request.url.path == "/api/graph":
            query = request.url.params["q"]
            if not query:
                page = merge_pages(list(graphs.values()))
            else:
                page = copy.deepcopy(graphs[query])
            page["pagination"] = empty_page(mode="repositories" if query else "projects")["pagination"]
            return httpx.Response(200, json=page)
        config_id = request.url.params["locator"].split("id:", 1)[1].split(")", 1)[0]
        return httpx.Response(200, json={"build": builds[config_id]})
    with httpx.Client(base_url="http://graph", transport=httpx.MockTransport(handler)) as graph_client, httpx.Client(base_url="https://tc.example", transport=httpx.MockTransport(handler)) as tc_client:
        report = validate_live_lab(graph_client, tc_client, plan, min_builds=2, build_limit=2)
    assert report["repositorySearches"] == 3 and report["defaultPages"] == 1
    assert len(calls) == 8
    assert "token" not in json.dumps(report)


@pytest.mark.parametrize("finished", [None, "not-a-date", "2000-01-01T00:00:00Z"])
def test_live_orchestrator_rejects_unverifiable_or_old_success(finished):
    plan, _, _ = fixture_matrix()
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"mode": "live", "lastScan": {"status": "success", "finishedAt": finished}}))
    with httpx.Client(base_url="http://graph", transport=transport) as graph_client, httpx.Client(base_url="http://tc", transport=transport) as tc_client:
        with pytest.raises(LabValidationError, match="freshness|too old"):
            validate_live_lab(graph_client, tc_client, plan)


def test_cli_uses_runtime_token_and_writes_nonsecret_report_without_refresh(tmp_path, monkeypatch, capsys):
    env_file = tmp_path / ".env"
    env_file.write_text("BITBUCKET_WORKSPACE=fixture-space\nTEAMCITY_TOKEN=runtime-only-secret\nTC_ADMIN_TOKEN=never-used-admin\nWEB_USERNAME=root\nWEB_PASSWORD=web-only-secret\n", encoding="utf-8")
    for name in ("BITBUCKET_WORKSPACE", "TEAMCITY_TOKEN", "WEB_USERNAME", "WEB_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    def authenticated(client, username, password):
        assert username == "root" and password == "web-only-secret"
    def validate(graph_client, tc_client, plan, **kwargs):
        assert tc_client.headers["authorization"] == "Bearer runtime-only-secret"
        assert plan["workspace"] == "fixture-space"
        return {"builds": 50}
    monkeypatch.setattr(validate_lab, "ensure_authenticated", authenticated)
    monkeypatch.setattr(validate_lab, "validate_live_lab", validate)
    monkeypatch.setattr(validate_lab, "request_refresh", lambda *args, **kwargs: pytest.fail("No implicit refresh is allowed"))
    report = tmp_path / "report.json"
    args = ["--env-file", str(env_file), "--tc-url", "http://localhost:18111", "--output", str(report)]
    assert validate_lab.main(args) == 0
    assert json.loads(report.read_text(encoding="utf-8"))["builds"] == 50
    assert validate_lab.main(args) == 1
    output = capsys.readouterr()
    assert "FileExistsError" in output.err
    assert not any(secret in output.out + output.err + report.read_text(encoding="utf-8") for secret in ("runtime-only-secret", "never-used-admin", "web-only-secret"))


def test_cli_never_falls_back_to_admin_token(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TEAMCITY_TOKEN", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("BITBUCKET_WORKSPACE=fixture-space\nTC_ADMIN_TOKEN=admin-secret\n", encoding="utf-8")
    assert validate_lab.main(["--env-file", str(env_file)]) == 1
    error = capsys.readouterr().err
    assert "TEAMCITY_TOKEN is required" in error and "admin-secret" not in error


@pytest.mark.parametrize("cli_url,bootstrap_url,runtime_url,expected", [
    ("http://localhost:18112/tc", "http://localhost:18111", "http://teamcity:8111", "http://localhost:18112/tc/"),
    (None, "http://localhost:18111", "http://teamcity:8111", "http://localhost:18111"),
    (None, None, "http://teamcity:8111", "http://teamcity:8111"),
])
def test_cli_teamcity_url_precedence_uses_shared_bootstrap_env_name(tmp_path, monkeypatch, capsys, cli_url, bootstrap_url, runtime_url, expected):
    env_file = tmp_path / ".env"
    lines = ["BITBUCKET_WORKSPACE=fixture-space", "TEAMCITY_TOKEN=runtime-secret", f"TEAMCITY_URL={runtime_url}", "TC_BOOTSTRAP_URL=http://obsolete-name:1"]
    if bootstrap_url:
        lines.append(f"TEAMCITY_BOOTSTRAP_URL={bootstrap_url}")
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for name in ("BITBUCKET_WORKSPACE", "TEAMCITY_TOKEN", "TEAMCITY_URL", "TEAMCITY_BOOTSTRAP_URL", "TC_BOOTSTRAP_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(validate_lab, "ensure_authenticated", lambda *args: None)
    def validate(graph_client, tc_client, plan, **kwargs):
        assert str(tc_client.base_url) == expected
        assert tc_client.headers["authorization"] == "Bearer runtime-secret"
        return {"builds": 50}
    monkeypatch.setattr(validate_lab, "validate_live_lab", validate)
    args = ["--env-file", str(env_file)] + (["--tc-url", cli_url] if cli_url else [])
    assert validate_lab.main(args) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "success"
