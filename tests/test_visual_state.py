from app.service import annotate_visual_state, build_node


def test_actual_outputs_are_propagated_only_through_product_chain():
    nodes = [
        {"id": "bb", "kind": "bb_project", "label": "BB"},
        {"id": "repo", "kind": "repository", "label": "Repo", "active": True},
        {"id": "repo-other", "kind": "repository", "label": "Other", "active": False},
        {"id": "tc", "kind": "tc_project", "label": "TC"},
        {"id": "cfg", "kind": "build_configuration", "label": "Build", "active": True},
        {"id": "cfg-other", "kind": "build_configuration", "label": "Other", "active": False},
        {"id": "run", "kind": "build", "label": "#1", "hasImagePush": True, "hasSbom": False},
        {"id": "image", "kind": "container_image", "label": "image"},
    ]
    edges = [
        {"source": "bb", "target": "repo", "relation": "contains"},
        {"source": "bb", "target": "repo-other", "relation": "contains"},
        {"source": "repo", "target": "cfg", "relation": "built_by"},
        {"source": "repo-other", "target": "cfg-other", "relation": "built_by"},
        {"source": "tc", "target": "cfg", "relation": "contains"},
        {"source": "tc", "target": "cfg-other", "relation": "contains"},
        {"source": "cfg", "target": "run", "relation": "ran_as"},
        {"source": "cfg-other", "target": "image", "relation": "pushes"},
    ]

    annotate_visual_state(nodes, edges)
    by_id = {node["id"]: node for node in nodes}

    for node_id in ("bb", "repo", "tc", "cfg"):
        assert by_id[node_id]["hasTargetOutput"] is True
    for node_id in ("repo-other", "cfg-other"):
        assert by_id[node_id]["hasTargetOutput"] is False
    assert by_id["bb"]["active"] is True
    assert by_id["tc"]["active"] is True


def test_running_or_queued_build_never_claims_configured_push_as_actual():
    for state in ("running", "queued"):
        node = build_node({"id": state, "state": state, "status": "SUCCESS"}, [{"image": "registry/app:1"}], [])
        assert node["hasImagePush"] is False
        assert node["pushedImages"] == []
