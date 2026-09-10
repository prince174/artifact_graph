import pytest

from app.subgraph import limit_builds_per_configuration, select_visible, select_visible_page


def fixture(projects=11, repos=11):
    nodes, edges = [], []
    for p in range(projects):
        pid = f"p-{p:02d}"
        nodes.append({"id": pid, "kind": "bb_project", "label": f"Project {p:02d}"})
        for r in range(repos):
            rid, tid, cid = f"r-{p:02d}-{r:02d}", f"t-{p:02d}-{r:02d}", f"c-{p:02d}-{r:02d}"
            nodes += [{"id": rid, "kind": "repository", "label": f"Repo {p:02d}-{r:02d}"}, {"id": tid, "kind": "tc_project", "label": tid}, {"id": cid, "kind": "build_configuration", "label": cid}]
            edges += [{"source": pid, "target": rid, "relation": "contains"}, {"source": rid, "target": tid, "relation": "maps_to"}, {"source": tid, "target": cid, "relation": "contains"}]
    return nodes, edges


def test_default_limits_projects_and_repositories():
    nodes, edges = fixture()
    visible, _ = select_visible(nodes, edges)
    assert len([n for n in visible if n["kind"] == "bb_project"]) == 10
    assert len([n for n in visible if n["kind"] == "repository"]) == 100
    assert not any(n["id"].startswith("r-10-") for n in visible)
    assert not any(n["id"].endswith("-10") and n["kind"] == "repository" for n in visible)


def test_search_uses_full_repository_index_outside_default_page():
    nodes, edges = fixture()
    visible, visible_edges = select_visible(nodes, edges, "Repo 10-10")
    ids = {node["id"] for node in visible}
    assert {"p-10", "r-10-10", "t-10-10", "c-10-10"} <= ids
    assert len([n for n in visible if n["kind"] == "repository"]) == 1
    assert len(visible_edges) == 3
    assert next(node for node in visible if node["id"] == "r-10-10")["searchMatch"] is True


def test_search_matches_config_build_image_and_sbom_and_returns_connected_lineage():
    nodes = [
        {"id": "p", "kind": "bb_project", "label": "Payments"},
        {"id": "r", "kind": "repository", "label": "orders"},
        {"id": "t", "kind": "tc_project", "label": "Orders product"},
        {"id": "c", "kind": "build_configuration", "label": "Deploy production"},
        {"id": "b", "kind": "build", "label": "#42", "pushedImages": [{"engine": "podman", "image": "registry/orders:42"}], "sbomArtifacts": [{"path": "reports/sbom.json"}]},
    ]
    edges = [
        {"source": "p", "target": "r", "relation": "contains"},
        {"source": "r", "target": "t", "relation": "maps_to"},
        {"source": "t", "target": "c", "relation": "contains"},
        {"source": "c", "target": "b", "relation": "ran_as"},
    ]
    for query, matched in (("Deploy production", "c"), ("registry/orders", "b"), ("sbom.json", "b"), ("#42", "b"), ("Payments", "p")):
        visible, visible_edges, page = select_visible_page(nodes, edges, query)
        assert {node["id"] for node in visible} == {"p", "r", "t", "c", "b"}
        assert [node["id"] for node in visible if node.get("searchMatch")] == [matched]
        assert len(visible_edges) == 4 and page["total"] == 1


def test_cursor_paginates_projects_and_search_results_stably():
    nodes, edges = fixture(projects=12, repos=1)
    first, _, page = select_visible_page(nodes, edges, limit=10)
    second, _, last = select_visible_page(nodes, edges, cursor=page["nextCursor"], limit=10)
    assert len([node for node in first if node["kind"] == "bb_project"]) == 10
    assert {node["id"] for node in second if node["kind"] == "bb_project"} == {"p-10", "p-11"}
    assert page["hasMore"] is True and last["hasMore"] is False and page["total"] == 12
    search_first, _, search_page = select_visible_page(nodes, edges, "repo", limit=5)
    search_second, _, _ = select_visible_page(nodes, edges, "repo", cursor=search_page["nextCursor"], limit=5)
    first_repos = {node["id"] for node in search_first if node["kind"] == "repository"}
    second_repos = {node["id"] for node in search_second if node["kind"] == "repository"}
    assert len(first_repos) == len(second_repos) == 5 and first_repos.isdisjoint(second_repos)


def test_invalid_cursor_is_rejected():
    with pytest.raises(ValueError, match="Invalid pagination cursor"):
        select_visible_page([], [], cursor="not-base64")


def test_build_detail_limit_keeps_newest_builds_and_complete_ancestry():
    nodes = [
        {"id": "p", "kind": "bb_project", "label": "P"},
        {"id": "r", "kind": "repository", "label": "R"},
        {"id": "t", "kind": "tc_project", "label": "T"},
        {"id": "c", "kind": "build_configuration", "label": "C"},
        *({"id": f"b{number}", "kind": "build", "label": f"#{number}"} for number in (2, 10, 7)),
    ]
    edges = [
        {"source": "p", "target": "r", "relation": "contains"},
        {"source": "r", "target": "t", "relation": "maps_to"},
        {"source": "t", "target": "c", "relation": "contains"},
        *({"source": "c", "target": f"b{number}", "relation": "ran_as"} for number in (2, 10, 7)),
    ]
    visible, visible_edges = limit_builds_per_configuration(nodes, edges, 2)
    assert {node["id"] for node in visible} == {"p", "r", "t", "c", "b10", "b7"}
    assert len(visible_edges) == 5
