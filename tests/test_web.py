from app.web import PAGE


def test_build_detail_ui_has_status_and_clickable_artifact_support():
    assert 'node[kind = "build"][status = "SUCCESS"]' in PAGE
    assert 'node[kind = "build"][status = "FAILURE"]' in PAGE
    assert "d.pushedImages?.length" in PAGE
    assert "d.sbomArtifacts?.length" in PAGE
    assert 'target="_blank" rel="noopener"' in PAGE


def test_ui_has_server_filters_and_collapsible_branches():
    for control in ("bbProject", "tcProject", "statusFilter", "engineFilter", "imageFilter", "sbomFilter", "targetOnly", "outputsMode", "sinceDays"):
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


def test_node_shapes_colors_and_neutral_edges_follow_visual_language():
    assert 'node[kind = "bb_project"]' in PAGE and "'width':18" in PAGE
    assert 'node[kind = "repository"]' in PAGE and "'height':11" in PAGE
    assert 'node[kind = "tc_project"]' in PAGE and "'shape':'triangle'" in PAGE
    assert 'node[kind = "build_configuration"]' in PAGE and "'shape':'rectangle'" in PAGE
    assert 'node[kind = "build"]' in PAGE and "'shape':'ellipse'" in PAGE
    for color in ("#f2d675", "#e99a95", "#9fd8ad", "#7d8590", "#2f81f7", "#2ea043", "#00b3a4"):
        assert color in PAGE
    assert "[?hasSbom][!hasImagePush]" in PAGE
    assert "[?hasImagePush][!hasSbom]" in PAGE
    assert "[?hasSbom][?hasImagePush]" in PAGE
    assert "edge.actual-push" not in PAGE
    assert "edge.actual-sbom" not in PAGE
    assert "'line-color':'#484f58'" in PAGE
    assert "'target-arrow-color':'#484f58'" in PAGE
    assert "'line-color':'#a371f7'" not in PAGE


def test_ui_preserves_viewport_explains_color_and_polls_for_new_scan():
    assert "zoom:cy.zoom(),pan:cy.pan()" in PAGE
    assert "cy.zoom(viewport.zoom);cy.pan(viewport.pan)" in PAGE
    assert "Причина цвета" in PAGE
    assert "setInterval(status,60000)" in PAGE
    assert "lastScanFinished" in PAGE
