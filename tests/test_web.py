from app.web import PAGE


def test_build_detail_ui_has_status_and_clickable_artifact_support():
    assert 'node[kind = "build"][status = "SUCCESS"]' in PAGE
    assert 'node[kind = "build"][status = "FAILURE"]' in PAGE
    assert "d.pushedImages?.length" in PAGE
    assert "d.sbomArtifacts?.length" in PAGE
    assert 'target="_blank" rel="noopener"' in PAGE


def test_ui_has_server_filters_and_collapsible_branches():
    for control in ("bbProject", "tcProject", "statusFilter", "engineFilter", "imageFilter", "sbomFilter", "targetOnly", "sinceDays"):
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


def test_outputs_are_build_details_not_separate_graph_nodes():
    assert "outputsMode" not in PAGE
    assert "container_image" not in PAGE
    assert "Подтверждено по TeamCity build log" in PAGE
    assert "Найден как артефакт" in PAGE


def test_build_hover_shows_compact_status_push_and_sbom_summary():
    assert 'id="hover"' in PAGE
    assert "mouseover','node[kind = \"build\"]'" in PAGE
    assert "mousemove','node[kind = \"build\"]'" in PAGE
    assert "mouseout','node[kind = \"build\"]'" in PAGE
    assert "function showHover" in PAGE
    assert "Целевых артефактов нет" in PAGE


def test_viewport_and_collapsed_branches_persist_across_reload():
    assert "artifactGraph.ui.v1" in PAGE
    assert "localStorage.getItem(UI_KEY)" in PAGE
    assert "localStorage.setItem(UI_KEY" in PAGE
    assert "zoom:cy.zoom(),pan:cy.pan(),collapsed:[...collapsed]" in PAGE
    assert "cy.on('pan zoom',scheduleUiSave)" in PAGE
    assert "localStorage.removeItem(UI_KEY)" in PAGE


def test_snapshot_diff_controls_and_highlights_are_present():
    assert 'id="snapshotBefore"' in PAGE
    assert 'id="snapshotAfter"' in PAGE
    assert "function compareSnapshots" in PAGE
    assert "/api/snapshots/" in PAGE
    assert ".diff-added" in PAGE
    assert "Изменения push / SBOM" in PAGE


def test_stale_data_is_visually_marked_and_explained():
    assert "node[?stale]" in PAGE
    assert "Устаревшие данные" in PAGE
    assert "status.degraded" in PAGE


def test_ui_loads_session_and_sends_csrf_for_refresh():
    assert "fetch('/api/session')" in PAGE
    assert "csrfToken=session.csrf" in PAGE
    assert "'X-CSRF-Token':csrfToken" in PAGE


def test_degraded_status_shows_safe_upstream_diagnostics():
    assert "s.lastScan?.upstream" in PAGE
    assert "u.httpStatus" in PAGE
    assert "u.endpoint" in PAGE
    assert "retries" in PAGE


def test_mapping_coverage_panel_and_problem_filter_are_present():
    assert 'id="mappingIssues"' in PAGE
    assert "mapping_issues:'mappingIssues'" in PAGE
    assert "function showCoverage" in PAGE
    assert "fetch('/api/coverage')" in PAGE
    assert "stopPropagation" in PAGE
    assert "Связь BB ↔ TC" in PAGE


def test_server_pagination_controls_and_full_project_options_are_used():
    assert 'id="pagePrev"' in PAGE and 'id="pageNext"' in PAGE
    assert "currentCursor" in PAGE and "nextCursor" in PAGE
    assert "fetch('/api/options')" in PAGE
    assert "function nextPage" in PAGE and "function previousPage" in PAGE
