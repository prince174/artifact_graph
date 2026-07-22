from app.web import PAGE


def test_build_detail_ui_has_status_and_clickable_artifact_support():
    assert 'node[kind = "build"][status = "SUCCESS"]' in PAGE
    assert 'node[kind = "build"][status = "FAILURE"]' in PAGE
    assert "d.pushedImages?.length" in PAGE
    assert "d.sbomArtifacts?.length" in PAGE
    assert 'target="_blank" rel="noopener"' in PAGE


def test_ui_has_server_filters_and_collapsible_branches():
    for control in ("bbProject", "tcProject", "statusFilter", "engineFilter", "imageFilter", "sbomFilter", "sinceDays"):
        assert f'id="{control}"' in PAGE
    assert "function toggleCollapse" in PAGE
    assert "function descendants" in PAGE
    assert "expandAll()" in PAGE
    assert "Технические данные" in PAGE


def test_detail_panel_closes_on_background_header_and_escape():
    assert "if(e.target===cy)closeDetail()" in PAGE
    assert "if(e.key==='Escape')closeDetail()" in PAGE
    assert "document.querySelector('header').addEventListener('click',closeDetail)" in PAGE
    assert "cy.$(':selected').unselect()" in PAGE


def test_actual_pushes_and_sboms_have_distinct_graph_markers():
    assert "node[?hasImagePush]" in PAGE
    assert "node[?hasSbom]" in PAGE
    assert "edge.actual-push" in PAGE
    assert "edge.actual-sbom" in PAGE
    assert "push image" in PAGE
