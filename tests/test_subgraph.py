from app.subgraph import select_visible


def fixture(projects=11, repos=11):
    nodes, edges = [], []
    for p in range(projects):
        pid = f"p-{p:02d}"
        nodes.append({"id": pid, "kind": "bb_project", "label": f"Project {p:02d}"})
        for r in range(repos):
            rid, cid = f"r-{p:02d}-{r:02d}", f"c-{p:02d}-{r:02d}"
            nodes += [{"id": rid, "kind": "repository", "label": f"Repo {p:02d}-{r:02d}"}, {"id": cid, "kind": "build_configuration", "label": cid}]
            edges += [{"source": pid, "target": rid, "relation": "contains"}, {"source": rid, "target": cid, "relation": "built_by"}]
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
    assert {"p-10", "r-10-10", "c-10-10"} <= ids
    assert len([n for n in visible if n["kind"] == "repository"]) == 1
    assert len(visible_edges) == 2
