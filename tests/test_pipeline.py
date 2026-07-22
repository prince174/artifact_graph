from app.pipeline import PIPELINE_STAGES, product_project_id, stage_build_id, stage_script


def test_product_pipeline_has_ordered_lifecycle_stages():
    assert [stage.key for stage in PIPELINE_STAGES] == ["Test", "Build", "Deploy"]
    assert product_project_id(3) == "Demo_03_Product"
    assert stage_build_id(3, PIPELINE_STAGES[1]) == "Demo_03_Build"


def test_build_stage_pushes_and_generates_sbom():
    scripts = {stage.key: stage_script("api", stage, True) for stage in PIPELINE_STAGES}
    assert "ci/build.sh" in scripts["Build"]
    assert all("ci/build.sh" not in script for key, script in scripts.items() if key != "Build")
    assert "ci/sbom.sh" in scripts["Build"]
    assert all("ci/sbom.sh" not in script for key, script in scripts.items() if key != "Build")
