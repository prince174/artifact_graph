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
